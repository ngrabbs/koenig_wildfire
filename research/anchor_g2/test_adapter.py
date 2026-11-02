"""Small generated regression subset, NOT the 24-case B3 qualification."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import cv2
import numpy as np
from .adapter import View, Scratch, measure, register, evaluate, sha
from .frozen.fixtures import produce
from .frozen.measurements import primary_i

ROOT = Path(__file__).parent


def support(**changes):
    result = dict(alignment_residual_px=0.1, p_tolerance_px=0.2, i_margin_px=2,
                  source_complete=True, background_representative=True, selection_independent=True)
    result.update(changes)
    return result


class AdapterTests(unittest.TestCase):
    def test_frozen_identities_and_arithmetic_extraction(self):
        manifest = json.loads((ROOT/'upstream_source_manifest.json').read_text())
        for path in (ROOT/'frozen').glob('*.py'):
            self.assertEqual(sha(path.read_bytes()), manifest['files']['optiona_cpu/'+path.name])
        source = (ROOT/'frozen'/'measurements.py').read_text()
        block = source[source.index('        gross=[];beta=[];net=[]'):]
        self.assertTrue((ROOT/'integrated_arithmetic.py').read_text().endswith(block))
        self.assertFalse(json.loads((ROOT/'execution_policy.json').read_text())['execution_enabled'])

    def test_generated_math_parity_and_i_only(self):
        for name in ('U01_ZERO_EXCESS', 'U02_KNOWN_EXCESS', 'U03_NET_ZERO', 'U04_NET_NEGATIVE', 'U05_BELOW_P_FLOOR'):
            with self.subTest(name=name):
                fixture = produce(name, Scratch())
                view = View([a.view() for a in fixture.aligned_unit], fixture.common_unit.view(), 'generated-test')
                p, result, reasons = measure(view, fixture.source.view(), fixture.background.view(), support())
                oracle = primary_i(view, fixture, Scratch(), 'PASS_GENERATED_ONLY')
                for key in ('gross_signals', 'net_signals', 'net_continuum', 'additive_excess', 'numeric_ratio'):
                    self.assertEqual(result[key], oracle[key])
                self.assertEqual(reasons, [])
                if name == 'U05_BELOW_P_FLOOR':
                    self.assertFalse(p['numeric_valid'])
                    self.assertTrue(result['numeric_valid'])
                    self.assertEqual(result['numeric_ratio'], 1.0)

    def test_independent_eligibility(self):
        f = produce('U02_KNOWN_EXCESS', Scratch())
        view = View([a.view() for a in f.aligned_unit], f.common_unit.view(), 'g')
        p, i, reasons = measure(view, f.source.view(), f.background.view(), support(alignment_residual_px=1))
        self.assertFalse(p['numeric_valid']); self.assertTrue(i['numeric_valid'])
        for evidence in (support(source_complete=False), support(background_representative=False),
                         support(selection_independent=False), support(alignment_residual_px=3)):
            _, i, reasons = measure(view, f.source.view(), f.background.view(), evidence)
            self.assertFalse(i['numeric_valid']); self.assertTrue(reasons)
        _, i, reasons = measure(view, f.source.view(), f.source.view(), support())
        self.assertIn('BACKGROUND_UNSUPPORTED', reasons)

    def test_generated_geometry_and_flat_rejection(self):
        f = produce('P01_IDENTITY64', Scratch())
        images = [cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_UNCHANGED) for b in f.compressed]
        view, geometry = register(images, [a.view() for a in f.gain], [a.view() for a in f.dark])
        self.assertGreater(geometry['common_fraction'], .6)
        for matrix in geometry['transforms']:
            np.testing.assert_allclose(matrix, np.eye(3), atol=1e-6)
        with self.assertRaises(ValueError):
            register([np.full((64,64),64,np.uint8)]*3, [np.ones((64,64),np.float32)]*3,
                     [np.zeros((64,64),np.float32)]*3)

    def test_invalid_request_is_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'request.json'
            path.write_text('{"schema":"wrong"}')
            result = evaluate(path)
            self.assertEqual(result['status'], 'UNKNOWN')
            self.assertEqual(result['production_decision'], 'NOT_CALIBRATED')

    def test_read_only_end_to_end_and_hash_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def ref(name):
                return dict(path=name, sha256=sha((root/name).read_bytes()))
            f = produce('P01_IDENTITY64', Scratch())
            images = [cv2.imdecode(np.frombuffer(b,np.uint8),cv2.IMREAD_UNCHANGED) for b in f.compressed]
            maps = {**{f'gain_{p}':f.gain[p].view() for p in range(3)},
                    **{f'dark_{p}':f.dark[p].view() for p in range(3)}}
            np.savez(root/'maps.npz', **maps)
            channels = []
            for p, wave in enumerate((770,750,780)):
                cv2.imwrite(str(root/f'{wave}.png'), images[p])
                channels.append(dict(**ref(f'{wave}.png'), camera_id=f'camera{p}', wavelength_nm=wave,
                    correction_port=p, acquisition=dict(exposure_us=100, gain=1, encoding='LINEAR_UINT8_MONO',
                                                        filter_id=f'filter{wave}', optical_configuration='synthetic')))
            provenance = dict(schema='CORRECTION_PROVENANCE_V1', correction_sha256=ref('maps.npz')['sha256'],
                linear_8bit_dn=True, has_measured_dark=True,
                channels={str(c['wavelength_nm']): {k:c[k] for k in ('camera_id','wavelength_nm','correction_port','acquisition')}
                          for c in channels})
            (root/'provenance.json').write_text(json.dumps(provenance))
            np.save(root/'source.npy', f.source.view()); np.save(root/'background.npy', f.background.view())
            _, geometry = register(images, [f.gain[p].view() for p in range(3)], [f.dark[p].view() for p in range(3)])
            evidence = dict(support(), schema='INDEPENDENT_SUPPORT_V1', optical_compatible=True, synchronized=True,
                evidence_reference='SYNTHETIC_TEST_ONLY', transforms=geometry['transforms'],
                image_sha256s=[c['sha256'] for c in channels], source_mask_sha256=ref('source.npy')['sha256'],
                background_mask_sha256=ref('background.npy')['sha256'])
            (root/'evidence.json').write_text(json.dumps(evidence))
            request = dict(schema='OFFLINE_TRIPLET_V1', channels=channels, correction=ref('maps.npz'),
                correction_provenance=ref('provenance.json'), eligibility=ref('evidence.json'),
                source_mask=ref('source.npy'), background_mask=ref('background.npy'))
            (root/'request.json').write_text(json.dumps(request))
            before = {p.name:sha(p.read_bytes()) for p in root.iterdir()}
            result = evaluate(root/'request.json')
            self.assertEqual(result['status'], 'DIAGNOSTIC_ONLY', result)
            self.assertEqual(before, {p.name:sha(p.read_bytes()) for p in root.iterdir()})
            # Fail closed on changed mapping, mismatched calibration acquisition,
            # or a JPEG extension even when the bytes/hash would otherwise match.
            original = json.loads(json.dumps(request))
            for field, value in (('wavelength_nm', 750), ('correction_port', 1), ('path', '770.jpg')):
                request = json.loads(json.dumps(original))
                request['channels'][0][field] = value
                (root/'request.json').write_text(json.dumps(request))
                self.assertEqual(evaluate(root/'request.json')['status'], 'UNKNOWN')
            request = json.loads(json.dumps(original))
            request['channels'][0]['acquisition']['exposure_us'] = 200
            (root/'request.json').write_text(json.dumps(request))
            self.assertEqual(evaluate(root/'request.json')['status'], 'UNKNOWN')
            request = original
            request['channels'][0]['sha256'] = '0'*64
            (root/'request.json').write_text(json.dumps(request))
            self.assertEqual(evaluate(root/'request.json')['status'], 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
