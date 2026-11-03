import json
from pathlib import Path
import tempfile
import unittest
import cv2
import numpy as np
from .adapter import Scratch, evaluate, sha
from .exploratory_jpeg import evaluate_jpeg
from .frozen.fixtures import produce


class JPEGTests(unittest.TestCase):
    def test_opt_in_geometry_and_independent_support(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = produce('P01_IDENTITY64', Scratch())
            channels = []
            for p, wave in enumerate((770, 750, 780)):
                raw = fixture.compressed[p]
                (root/f'{wave}.jpg').write_bytes(raw)
                channels.append(dict(path=f'{wave}.jpg', sha256=sha(raw), camera_id=str(p), wavelength_nm=wave))
            request = json.loads((Path(__file__).parent/'example_jpeg_request.json').read_text())
            request.update(channels=channels, calibration_reason='synthetic test, no calibration')
            path = root/'request.json'
            path.write_text(json.dumps(request))
            self.assertEqual(evaluate(path)['status'], 'UNKNOWN')
            self.assertIn('EXPLICIT_JPEG_OPT_IN_REQUIRED', evaluate_jpeg(path)['reason_codes'])
            result = evaluate_jpeg(path, opt_in=True)
            self.assertEqual(result['registration']['state'], 'ACCEPTED_ENGINEERING_ONLY')
            self.assertIsNone(result['I'])
            self.assertIn('SOURCE_BACKGROUND_ELIGIBILITY_MISSING', result['reason_codes'])
            self.assertEqual(len(result['channel_statistics']), 3)
            request['provisional_regions'] = dict(schema='PROVISIONAL_RECTANGLES_V1',
                source=[16,16,24,24], background=[32,32,40,40], selection_rationale='Generated geometry only')
            path.write_text(json.dumps(request))
            provisional = evaluate_jpeg(path, opt_in=True)
            self.assertEqual(provisional['status'], 'UNKNOWN')
            self.assertIsNone(provisional['I'])
            self.assertTrue(provisional['provisional']['I_numerical']['numeric_valid'])
            request['provisional_regions']['background'] = [16,16,24,24]
            path.write_text(json.dumps(request))
            self.assertIn('REGIONS_OVERLAP', evaluate_jpeg(path, opt_in=True)['reason_codes'])
            del request['provisional_regions']
            for name, data in (('source', fixture.source.view()), ('background', fixture.background.view())):
                np.save(root/f'{name}.npy', data)
                request[name+'_mask'] = dict(path=f'{name}.npy', sha256=sha((root/f'{name}.npy').read_bytes()))
            evidence = dict(schema='INDEPENDENT_SUPPORT_V1', optical_compatible=True, synchronized=True,
                evidence_reference='GENERATED_TEST_ONLY', alignment_residual_px=0.1, p_tolerance_px=0.2,
                i_margin_px=2, source_complete=True, background_representative=True, selection_independent=True,
                image_sha256s=[c['sha256'] for c in channels], transforms=result['geometry']['transforms'],
                source_mask_sha256=request['source_mask']['sha256'], background_mask_sha256=request['background_mask']['sha256'])
            (root/'evidence.json').write_text(json.dumps(evidence))
            request['eligibility'] = dict(path='evidence.json', sha256=sha((root/'evidence.json').read_bytes()))
            path.write_text(json.dumps(request))
            before = {p.name:sha(p.read_bytes()) for p in root.iterdir()}
            result = evaluate_jpeg(path, opt_in=True)
            self.assertEqual(result['status'], 'DIAGNOSTIC_ONLY', result)
            self.assertTrue(result['I']['numeric_valid'])
            self.assertEqual(result['I']['units'], 'JPEG_CODE_VALUE_PROXY_PIXELS')
            self.assertEqual(result['scientific_decision'], 'UNKNOWN')
            self.assertEqual(before, {p.name:sha(p.read_bytes()) for p in root.iterdir()})
            request['correction'] = dict(path='anything.npz')
            path.write_text(json.dumps(request))
            self.assertIn('JPEG_CALIBRATION_COMPATIBILITY_NOT_ESTABLISHED', evaluate_jpeg(path, opt_in=True)['reason_codes'])

    def test_rejected_geometry_is_not_forced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            channels = []
            ok, encoded = cv2.imencode('.jpg', np.full((64,64), 64, np.uint8))
            self.assertTrue(ok)
            for p,w in enumerate((770,750,780)):
                (root/f'{w}.jpg').write_bytes(encoded.tobytes())
                channels.append(dict(path=f'{w}.jpg', sha256=sha(encoded.tobytes()), camera_id=str(p), wavelength_nm=w))
            path=root/'request.json'
            path.write_text(json.dumps(dict(schema='EXPLORATORY_JPEG_TRIPLET_V1',mode='EXPLORATORY_JPEG',
                channels=channels,correction_policy='NONE_UNVERIFIED',calibration_reason='test',eligibility=None)))
            result=evaluate_jpeg(path,opt_in=True)
            self.assertEqual(result['status'],'UNKNOWN')
            self.assertEqual(result['registration']['state'],'REJECTED')
            self.assertIsNone(result['P']); self.assertIsNone(result['I'])

    def test_provisional_support_and_frozen_arithmetic(self):
        from .provisional import evaluate_regions
        from .adapter import View
        from .integrated_arithmetic import integrated_arithmetic
        from .frozen.measurements import unavailable_i
        planes = [np.full((64,64), v, np.float32) for v in (12,8,8)]
        common = np.ones((64,64), bool)
        definition = dict(schema='PROVISIONAL_RECTANGLES_V1', source=[10,10,20,20],
                          background=[30,30,40,40], selection_rationale='Synthetic fixed regions')
        view = View(planes, common, 'synthetic')
        result = evaluate_regions(view, definition)
        source = np.zeros((64,64), bool); source[10:20,10:20] = True
        background = np.zeros_like(source); background[30:40,30:40] = True
        expected = integrated_arithmetic(planes,source,background,100,100,Scratch(),unavailable_i())
        self.assertEqual(result['I_numerical'], expected)
        self.assertIsNone(result['I_numerical']['numeric_ratio'])
        common[10,10] = False
        rejected = evaluate_regions(View(planes,common,'synthetic'),definition)
        self.assertIsNone(rejected['I_numerical'])
        self.assertEqual(rejected['counts']['source']['requested'],100)
        self.assertEqual(rejected['counts']['source']['common'],99)


if __name__ == '__main__':
    unittest.main()
