"""Explicit JPEG code-value exploration; no assumption of radiometric linearity."""
import io
import sys
import zipfile
from pathlib import Path
import cv2
import numpy as np
from .adapter import (Ineligible, L, MAX_BYTES, MAX_PIXELS, parse, read, require,
                      sha, register, measure)


def reference_bytes(ref, base):
    if 'archive' not in ref:
        return read(base / ref['path'])
    with zipfile.ZipFile(base / ref['archive']) as archive:
        matches = [x for x in archive.infolist() if x.filename == ref['member']]
        require(len(matches) == 1 and matches[0].file_size <= MAX_BYTES, 'ZIP_MEMBER_AMBIGUOUS_OR_OVERSIZE')
        with archive.open(matches[0]) as stream:
            raw = stream.read(MAX_BYTES + 1)
        require(len(raw) <= MAX_BYTES, 'INPUT_SIZE_LIMIT')
        return raw


def intensity(a):
    return dict(min=int(a.min()), max=int(a.max()), mean=float(a.mean()),
                p01=float(np.percentile(a, 1)), median=float(np.median(a)),
                p99=float(np.percentile(a, 99)), zero_fraction=float((a == 0).mean()),
                clipping_fraction_ge254=float((a >= 254).mean()))


def evaluate_jpeg(request_path, *, opt_in=False):
    out = dict(schema='EXPLORATORY_JPEG_DIAGNOSTIC_V1', mode='EXPLORATORY_JPEG', status='UNKNOWN',
               production_decision='NOT_CALIBRATED', scientific_decision='UNKNOWN',
               correction='NOT_APPLIED', radiometric_compatibility='UNKNOWN',
               signal_units='JPEG_CODE_VALUE_PROXY_NOT_RADIANCE', reason_codes=[],
               channel_statistics=[], registration={}, geometry=None, P=None, I=None,
               aperture_background={'status': 'UNAVAILABLE_NO_INDEPENDENT_SUPPORT'}, provenance={},
               limitations=['UNKNOWN_JPEG_ISP_RESPONSE_AND_GAMMA', 'LOSSY_COMPRESSION',
                            'CROSS_CAMERA_RADIOMETRY_UNVERIFIED', 'NO_COVARIANCE_OR_UNCERTAINTY_MODEL',
                            'ENGINEERING_REGISTRATION_IS_NOT_PHYSICAL_ALIGNMENT_PROOF'])
    out['runtime'] = dict(python=sys.version.split()[0], numpy=np.__version__, opencv=cv2.__version__,
                          opencv_build_sha256=sha(cv2.getBuildInformation().encode()))
    try:
        require(opt_in is True, 'EXPLICIT_JPEG_OPT_IN_REQUIRED')
        require(sys.version_info[:3] == (3, 12, 14) and np.__version__ == '2.3.5'
                and cv2.__version__ == '5.0.0', 'UNSUPPORTED_RUNTIME_VERSION')
        request_path = Path(request_path).resolve()
        base = request_path.parent
        raw = read(request_path, 1024*1024)
        request = parse(raw)
        out['provenance']['request_sha256'] = sha(raw)
        require(request['schema'] == 'EXPLORATORY_JPEG_TRIPLET_V1'
                and request['mode'] == 'EXPLORATORY_JPEG', 'EXPLICIT_JPEG_REQUEST_REQUIRED')
        require(request['correction_policy'] == 'NONE_UNVERIFIED'
                and request.get('correction') is None, 'JPEG_CALIBRATION_COMPATIBILITY_NOT_ESTABLISHED')
        require(type(request['calibration_reason']) is str and request['calibration_reason'].strip(),
                'CALIBRATION_PROVENANCE_REASON_REQUIRED')
        out['provenance']['calibration_reason'] = request['calibration_reason']
        out['reason_codes'].append('CALIBRATION_NOT_APPLIED_COMPATIBILITY_UNVERIFIED')
        channels = request['channels']
        require(type(channels) is list and len(channels) == 3
                and {c['wavelength_nm'] for c in channels} == {770, 750, 780}
                and len({c['camera_id'] for c in channels}) == 3, 'WAVELENGTH_OR_CAMERA_MAPPING')
        channels = sorted(channels, key=lambda c: (770, 750, 780).index(c['wavelength_nm']))
        images, hashes = [], []
        for channel in channels:
            name = channel.get('member', channel.get('path', ''))
            require(Path(name).suffix.lower() in ('.jpg', '.jpeg'), 'JPEG_INPUT_REQUIRED')
            blob = reference_bytes(channel, base)
            require(sha(blob) == channel['sha256'], 'IMAGE_HASH_MISMATCH')
            hashes.append(sha(blob))
            decoded = cv2.imdecode(np.frombuffer(blob, np.uint8), cv2.IMREAD_UNCHANGED)
            require(isinstance(decoded, np.ndarray) and decoded.dtype == np.uint8
                    and decoded.ndim in (2, 3) and decoded.shape[0]*decoded.shape[1] <= MAX_PIXELS,
                    'JPEG_REPRESENTATION')
            require(decoded.ndim == 2 or decoded.shape[2] == 3, 'JPEG_CHANNELS')
            mono = cv2.cvtColor(decoded, cv2.COLOR_BGR2GRAY) if decoded.ndim == 3 else decoded
            out['channel_statistics'].append(dict(wavelength_nm=channel['wavelength_nm'],
                sha256=hashes[-1], shape=list(mono.shape), decoded_channels=1 if decoded.ndim == 2 else 3,
                intensity=intensity(mono), acquisition=channel.get('acquisition', {}),
                grayscale_rule='OPENCV_BGR2GRAY' if decoded.ndim == 3 else 'MONO_UNCHANGED'))
            images.append(mono)
        out['provenance']['image_sha256s'] = hashes
        # Unit gain/zero offset ONLY encode unchanged JPEG values into the frozen
        # fixed-point representation. They are not estimated/applied calibration.
        shape = images[0].shape
        gains = [np.ones(shape, np.float32) for _ in range(3)]
        darks = [np.zeros(shape, np.float32) for _ in range(3)]
        view, geometry = register(images, gains, darks, diagnostics=out['registration'])
        out['geometry'] = geometry
        out['registration']['state'] = 'ACCEPTED_ENGINEERING_ONLY'
        if request.get('provisional_regions') is not None:
            from .provisional import evaluate_regions
            provisional = evaluate_regions(view, request['provisional_regions'])
            # Relabel units only; preserve frozen arithmetic and strict P/I fields.
            def proxy_units(value):
                if isinstance(value, dict):
                    return {k: proxy_units(v) for k, v in value.items()}
                if isinstance(value, list):
                    return [proxy_units(v) for v in value]
                if isinstance(value, str) and value.startswith('CORRECTED_DN'):
                    return value.replace('CORRECTED_DN', 'JPEG_CODE_VALUE_PROXY')
                return value
            out['provisional'] = proxy_units(provisional)
        if request.get('eligibility') is None:
            out['reason_codes'].extend(['INDEPENDENT_ALIGNMENT_UNKNOWN', 'SOURCE_BACKGROUND_ELIGIBILITY_MISSING'])
            return out
        def bound_ref(ref):
            blob = reference_bytes(ref, base)
            require(sha(blob) == ref['sha256'], 'EVIDENCE_HASH_MISMATCH')
            return blob
        evidence = parse(bound_ref(request['eligibility']))
        require(evidence['schema'] == 'INDEPENDENT_SUPPORT_V1' and evidence['optical_compatible'] is True
                and evidence['synchronized'] is True and bool(evidence['evidence_reference'].strip()),
                'OPTICAL_OR_SYNCHRONIZATION_EVIDENCE_MISSING')
        require(evidence['image_sha256s'] == hashes and evidence['transforms'] == geometry['transforms'],
                'EVIDENCE_NOT_BOUND_TO_THIS_GEOMETRY')
        masks = []
        for field in ('source_mask', 'background_mask'):
            ref = request[field]
            require(evidence[field+'_sha256'] == ref['sha256'], 'APERTURE_EVIDENCE_MISMATCH')
            mask = np.load(io.BytesIO(bound_ref(ref)), allow_pickle=False)
            require(isinstance(mask, np.ndarray) and mask.dtype == bool and mask.shape == shape,
                    'APERTURE_REPRESENTATION')
            masks.append(mask)
        p, integrated, reasons = measure(view, *masks, evidence)
        # Frozen numerical code describes corrected-DN arrays. Relabel only the
        # report's units, never alter its arithmetic or claim calibrated values.
        def units(value):
            if isinstance(value, dict):
                return {k: units(v) for k, v in value.items()}
            if isinstance(value, list):
                return [units(v) for v in value]
            if isinstance(value, str) and value.startswith('CORRECTED_DN'):
                return value.replace('CORRECTED_DN', 'JPEG_CODE_VALUE_PROXY')
            return value
        out.update(P=units(p), I=units(integrated),
                   aperture_background=dict(source_pixels=int(masks[0].sum()), background_pixels=int(masks[1].sum()),
                       source_common_pixels=int((masks[0]&view.common).sum()),
                       background_common_pixels=int((masks[1]&view.common).sum()), status='OPERATOR_EVIDENCE_ONLY'))
        out['reason_codes'].extend(reasons)
        for metric in (p, integrated):
            if not metric['numeric_valid']:
                out['reason_codes'].append(metric['reason'])
        if not reasons and (p['numeric_valid'] or integrated['numeric_valid']):
            out['status'] = 'DIAGNOSTIC_ONLY'
    except Exception as error:
        out['reason_codes'].append(str(error) if isinstance(error, (Ineligible, L.MethodError))
                                  else 'INVALID_INPUT_OR_PROCESSING_FAILURE:'+type(error).__name__)
        out['registration'].setdefault('state', 'NOT_ACCEPTED')
    return out
