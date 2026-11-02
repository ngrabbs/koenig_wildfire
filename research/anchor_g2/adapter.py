"""Read-only real-triplet adapter, separate from the disabled generated B3 API."""
import contextlib
import hashlib
import io
import json
import math
import os
import sys
from pathlib import Path
from types import SimpleNamespace
import zipfile

for _key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[_key] = '1'
import cv2
import numpy as np
from .frozen import _legacy as L
from .frozen.immutable import FrozenArray
from .frozen.measurements import primary_p, unavailable_p, unavailable_i
from .integrated_arithmetic import integrated_arithmetic

MAX_BYTES = 32 * 1024 * 1024
MAX_PIXELS = 4_000_000


class Ineligible(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise Ineligible(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def read(path, limit=MAX_BYTES):
    with Path(path).open('rb') as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, 'INPUT_SIZE_LIMIT')
    return raw


def parse(raw):
    def pairs(items):
        obj = {}
        for k, v in items:
            require(k not in obj, 'DUPLICATE_JSON_KEY')
            obj[k] = v
        return obj
    def nonfinite(_):
        raise Ineligible('NONFINITE_JSON')
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=nonfinite)


def number(x):
    return type(x) in (int, float) and math.isfinite(x) and x > 0


class Scratch:
    """Allocation-name compatibility only; NOT a qualified B3 resource supervisor."""
    def __init__(self):
        self.live = {}
    def own(self, name, size):
        require(name not in self.live, 'ALLOCATION_LIFETIME')
        self.live[name] = size
    def release(self, name):
        self.live.pop(name, None)
    def count(self, *args):
        pass
    @contextlib.contextmanager
    def stage(self, name):
        yield


class View:
    def __init__(self, planes, common, geometry):
        self._planes = tuple(FrozenArray(a) for a in planes)
        self._common = FrozenArray(common)
        self.geometry = geometry
        self.identity = sha(canonical([a.descriptor('CORRECTED_DN') for a in self._planes]))
    @property
    def planes(self):
        return tuple(a.view() for a in self._planes)
    @property
    def common(self):
        return self._common.view()
    def provenance(self):
        return {'geometry_id': self.geometry}


def register(images, gains, darks):
    """Frozen correction/preparation/phase kernels; same B2 sampling and masks."""
    cv2.setNumThreads(1)
    cv2.setRNGSeed(0)
    cv2.ocl.setUseOpenCL(False)
    h, w = images[0].shape
    require(all(a.shape == (h, w) and a.dtype == np.uint8 for a in images), 'IMAGE_REPRESENTATION')
    require(min(h, w) >= 64 and h*w <= MAX_PIXELS, 'IMAGE_DIMENSIONS')
    values = [L.corrected(a, g, d, 'phase4_fixedpoint') for a, g, d in zip(images, gains, darks)]
    my, mx = int(np.ceil(h*.1)), int(np.ceil(w*.1))
    base = np.zeros((h, w), bool)
    base[my:h-my, mx:w-mx] = True
    masks = [base & (images[p] < 254) & (values[p] < 255*256) for p in range(3)]
    prep = [L.prepare(a, mask) for a, mask in zip(values, masks)]
    pairs = []
    for p in (1, 2):
        result = L.phase_prepared(prep[p], prep[0])
        pairs.append(result)
        require(result['status'] == 'PASS', 'GEOMETRY_REJECTED:' + result['reason'])
    matrices = [np.eye(3)] + [np.array(row['matrix']) for row in pairs]
    planes, valid = [], []
    for a, mask, matrix in zip(values, masks, matrices):
        planes.append(cv2.warpPerspective(a, matrix, (w, h), flags=cv2.INTER_LINEAR).astype(np.float32)/256)
        sampled = cv2.warpPerspective(mask.astype(np.float32), matrix, (w, h), flags=cv2.INTER_LINEAR) >= 1
        valid.append(cv2.erode(sampled.astype(np.uint8), np.ones((3, 3), np.uint8),
                              borderType=cv2.BORDER_CONSTANT, borderValue=0) > 0)
    common = np.logical_and.reduce(valid)
    fraction = float(common.sum()/max(1, base.sum()))
    require(fraction >= .6, 'INSUFFICIENT_COMMON_SUPPORT')
    geometry = {'method': 'PHASE_ANCHOR_G2_COMPATIBLE', 'pairs': pairs,
                'transforms': [m.tolist() for m in matrices], 'common_fraction': fraction,
                'acceptance': 'ENGINEERING_ONLY_NOT_PHYSICAL_ALIGNMENT_PROOF'}
    return View(planes, common, sha(canonical(geometry))), geometry


def measure(view, source, background, evidence):
    """Independent physical support gates before unchanged numerical arithmetic."""
    monitor = Scratch()
    p, integrated = unavailable_p(), unavailable_i()
    residual = evidence['alignment_residual_px']
    require(type(residual) in (float, int) and math.isfinite(residual) and residual >= 0,
            'INDEPENDENT_ALIGNMENT_UNKNOWN')
    require(number(evidence['p_tolerance_px']) and number(evidence['i_margin_px']), 'SUPPORT_TOLERANCE_UNKNOWN')
    if residual <= evidence['p_tolerance_px']:
        p = primary_p(view, source, monitor)
    else:
        p = unavailable_p('INDEPENDENT_PIXEL_ALIGNMENT_INELIGIBLE')
    reasons = []
    if residual > evidence['i_margin_px']:
        reasons.append('APERTURE_ALIGNMENT_INELIGIBLE')
    for key in ('source_complete', 'background_representative', 'selection_independent'):
        if evidence.get(key) is not True:
            reasons.append(key.upper() + '_UNVERIFIED')
    common = view.common
    if not source.any() or not background.any():
        reasons.append('EMPTY_SUPPORT')
    if (source & background).any() or (background & ~common).any():
        reasons.append('BACKGROUND_UNSUPPORTED')
    if (source & ~common).any():
        reasons.append('SOURCE_TRUNCATED')
    if not all(np.isfinite(a[source | background]).all() for a in view.planes):
        reasons.append('NUMERIC_NONFINITE')
    if reasons:
        integrated = unavailable_i(reasons[0])
    else:
        integrated = integrated_arithmetic(view.planes, source, background,
                    int(source.sum()), int(background.sum()), monitor, integrated)
        integrated.update(geometry_id=view.provenance()['geometry_id'], lease_id=view.identity,
                          source_complete='OPERATOR_EVIDENCE_ONLY',
                          source_requested_count=int(source.sum()), background_requested_count=int(background.sum()))
    return p, integrated, reasons


def evaluate(request_path):
    report = {'schema': 'OFFLINE_ANCHOR_G2_DIAGNOSTIC_V1', 'production_decision': 'NOT_CALIBRATED',
              'scientific_decision': 'UNKNOWN', 'reason_codes': [], 'geometry': None,
              'P': None, 'I': None, 'provenance': {}, 'status': 'UNKNOWN'}
    report['runtime'] = {'python': sys.version.split()[0], 'numpy': np.__version__, 'opencv': cv2.__version__,
                         'opencv_build_sha256': sha(cv2.getBuildInformation().encode())}
    try:
        require(sys.version_info[:3] == (3, 12, 14) and np.__version__ == '2.3.5'
                and cv2.__version__ == '5.0.0', 'UNSUPPORTED_RUNTIME_VERSION')
        request_path = Path(request_path).resolve()
        raw = read(request_path, 1024*1024)
        request = parse(raw)
        report['provenance']['request_sha256'] = sha(raw)
        require(request['schema'] == 'OFFLINE_TRIPLET_V1', 'REQUEST_SCHEMA')
        base = request_path.parent
        def load_ref(ref):
            blob = read(base / ref['path'])
            require(sha(blob) == ref['sha256'], 'INPUT_HASH_MISMATCH')
            report['provenance'][ref['path']] = ref['sha256']
            return blob
        channels = request['channels']
        require(type(channels) is list and len(channels) == 3, 'THREE_CHANNELS_REQUIRED')
        require({c['wavelength_nm'] for c in channels} == {770, 750, 780}, 'WAVELENGTH_MAPPING')
        require(len({c['camera_id'] for c in channels}) == 3, 'CAMERA_MAPPING')
        require(len({c['correction_port'] for c in channels}) == 3, 'CORRECTION_PORT_MAPPING')
        channels = sorted(channels, key=lambda c: (770, 750, 780).index(c['wavelength_nm']))
        correction = load_ref(request['correction'])
        provenance = parse(load_ref(request['correction_provenance']))
        require(provenance['schema'] == 'CORRECTION_PROVENANCE_V1'
                and provenance['correction_sha256'] == sha(correction), 'CORRECTION_PROVENANCE')
        require(provenance['linear_8bit_dn'] is True and provenance['has_measured_dark'] is True,
                'RADIOMETRY_UNSUPPORTED')
        evidence = parse(load_ref(request['eligibility']))
        require(evidence['schema'] == 'INDEPENDENT_SUPPORT_V1'
                and evidence['optical_compatible'] is True and evidence['synchronized'] is True
                and type(evidence['evidence_reference']) is str and bool(evidence['evidence_reference'].strip()),
                'OPTICAL_OR_SYNCHRONIZATION_EVIDENCE_MISSING')
        report['provenance']['evidence_status'] = 'OPERATOR_SUPPLIED_NOT_INDEPENDENTLY_VERIFIED_BY_SOFTWARE'
        images, gains, darks = [], [], []
        with zipfile.ZipFile(io.BytesIO(correction)) as archive:
            require(sum(z.file_size for z in archive.infolist()) <= 128*1024*1024, 'CORRECTION_SIZE_LIMIT')
        with np.load(io.BytesIO(correction), allow_pickle=False) as maps:
            for channel in channels:
                capture = channel['acquisition']
                require(all(number(capture[k]) for k in ('exposure_us', 'gain')), 'ACQUISITION_UNKNOWN')
                for key in ('camera_id', 'wavelength_nm', 'acquisition'):
                    require(channel[key] == provenance['channels'][str(channel['wavelength_nm'])][key],
                            'CORRECTION_INCOMPATIBLE:' + key)
                require(capture['encoding'] == 'LINEAR_UINT8_MONO'
                        and all(type(capture[k]) is str and capture[k].strip()
                                for k in ('filter_id', 'optical_configuration')), 'OPTICAL_PROVENANCE_UNKNOWN')
                require(Path(channel['path']).suffix.lower() in ('.png', '.tif', '.tiff'),
                        'JPEG_OR_UNSUPPORTED_RADIOMETRY')
                blob = load_ref(channel)
                image = cv2.imdecode(np.frombuffer(blob, np.uint8), cv2.IMREAD_UNCHANGED)
                require(isinstance(image, np.ndarray) and image.ndim == 2 and image.dtype == np.uint8
                        and image.size <= MAX_PIXELS, 'IMAGE_REPRESENTATION')
                port = channel['correction_port']
                require(type(port) is int and port >= 0
                        and port == provenance['channels'][str(channel['wavelength_nm'])]['correction_port'], 'CORRECTION_PORT')
                g, d = maps[f'gain_{port}'], maps[f'dark_{port}']
                require(g.shape == image.shape and d.shape == image.shape and g.dtype.kind in 'fi'
                        and d.dtype.kind in 'fi' and np.isfinite(g).all() and np.isfinite(d).all()
                        and (g > 0).all(), 'CORRECTION_MAP_INVALID')
                images.append(image); gains.append(g.copy()); darks.append(d.copy())
        require(len({c['acquisition']['exposure_us'] for c in channels}) == 1
                and len({c['acquisition']['gain'] for c in channels}) == 1, 'INCONSISTENT_ACQUISITION')
        view, geometry = register(images, gains, darks)
        report['geometry'] = geometry
        masks = []
        for key in ('source_mask', 'background_mask'):
            a = np.load(io.BytesIO(load_ref(request[key])), allow_pickle=False)
            require(isinstance(a, np.ndarray) and a.dtype == bool and a.shape == view.common.shape, 'APERTURE_REPRESENTATION')
            masks.append(a)
        require(evidence['source_mask_sha256'] == request['source_mask']['sha256']
                and evidence['background_mask_sha256'] == request['background_mask']['sha256']
                and evidence['image_sha256s'] == [c['sha256'] for c in channels]
                and evidence['transforms'] == geometry['transforms'], 'EVIDENCE_NOT_BOUND_TO_THIS_GEOMETRY')
        p, integrated, reasons = measure(view, *masks, evidence)
        report.update(P=p, I=integrated, reason_codes=reasons,
                      status='DIAGNOSTIC_ONLY' if not reasons and (p['numeric_valid'] or integrated['numeric_valid']) else 'UNKNOWN')
        if not p['numeric_valid']:
            report['reason_codes'].append(p['reason'])
        if not integrated['numeric_valid'] and integrated['reason'] not in report['reason_codes']:
            report['reason_codes'].append(integrated['reason'])
    except Exception as error:
        report['status'] = 'UNKNOWN'
        report['reason_codes'].append(str(error) if isinstance(error, (Ineligible, L.MethodError))
                                      else 'INVALID_INPUT_OR_PROCESSING_FAILURE:' + type(error).__name__)
    return report
