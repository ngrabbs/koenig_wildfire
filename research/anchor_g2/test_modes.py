"""Focused mode routing/numerical tests, not formal B3 qualification."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import cv2
import numpy as np
from .adapter import Scratch, sha
from .exploratory_jpeg import evaluate_jpeg
from .frozen.fixtures import produce
from .integrated_arithmetic import integrated_arithmetic
from .frozen.measurements import unavailable_i
from .native_modes import evaluate_native, spectral_summary

ROOT = Path(__file__).parent
CHANNELS = [dict(camera_id=str(i), wavelength_nm=w) for i, w in enumerate((770, 750, 780))]


def regions():
    return dict(schema='INDEPENDENT_RECTANGLES_V1', selection_rationale='Synthetic known geometry',
                correspondence_description='Generated source; no physical approval', cameras=[
                    dict(**c, source=[i+1, 1, i+3, 3], background=[8, 8, 10, 10],
                         source_containment_note='Generated only') for i, c in enumerate(CHANNELS)])


class ModeTests(unittest.TestCase):
    def make_request(self, root, texture=False):
        request = json.loads((ROOT/'example_jpeg_request.json').read_text())
        fixture = produce('P01_IDENTITY64', Scratch()) if texture else None
        for i, c in enumerate(request['channels']):
            if fixture:
                image = cv2.imdecode(np.frombuffer(fixture.compressed[i], np.uint8), 0)
            else:
                image = np.full((64+i, 64+2*i), (12, 8, 10)[i], np.uint8)
            ok, encoded = cv2.imencode('.jpg', image)
            self.assertTrue(ok)
            raw = encoded.tobytes()
            (root/c['path']).write_bytes(raw)
            c['sha256'] = sha(raw)
        path = root/'request.json'
        path.write_text(json.dumps(request))
        return path, request

    def test_native_modes_never_register_warp_or_claim_eligibility(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, request = self.make_request(root)
            for mode in ('independent-boxes', 'whole-frame'):
                request['independent_regions'] = regions() if mode == 'independent-boxes' else None
                path.write_text(json.dumps(request))
                before = {p.name: sha(p.read_bytes()) for p in root.iterdir()}
                with patch('research.anchor_g2.exploratory_jpeg.register') as reg, \
                     patch('cv2.warpPerspective') as warp:
                    result = evaluate_jpeg(path, opt_in=True, processing_mode=mode)
                reg.assert_not_called(); warp.assert_not_called()
                self.assertEqual(result['processing_mode'], mode)
                self.assertEqual(result['registration']['state'], 'NOT_REQUESTED')
                self.assertEqual(result['timing_seconds']['registration'], 0)
                self.assertEqual(result['status'], 'UNKNOWN')
                self.assertEqual(result['production_decision'], 'NOT_CALIBRATED')
                self.assertIsNone(result['P']); self.assertIsNone(result['I'])
                self.assertEqual(result['provisional']['schema'], 'PROVISIONAL_JPEG_DIAGNOSTICS')
                self.assertEqual(before, {p.name: sha(p.read_bytes()) for p in root.iterdir()})

    def test_mode1_default_equals_explicit_registered_results(self):
        with tempfile.TemporaryDirectory() as directory:
            path, request = self.make_request(Path(directory), texture=True)
            request['provisional_regions'] = dict(schema='PROVISIONAL_RECTANGLES_V1',
                source=[16,16,24,24], background=[32,32,40,40], selection_rationale='Generated')
            path.write_text(json.dumps(request))
            a = evaluate_jpeg(path, opt_in=True)
            b = evaluate_jpeg(path, opt_in=True, processing_mode='registered-boxes')
            self.assertEqual(a['registration']['state'], 'ACCEPTED_ENGINEERING_ONLY')
            self.assertTrue(a['provisional']['I_numerical']['numeric_valid'])
            for r in (a, b):
                self.assertGreater(r.pop('timing_seconds')['registration'], 0)
            self.assertEqual(a, b)

    def test_independent_coordinates_counts_and_shared_case_frozen_parity(self):
        definition = regions()
        images = [np.full((16+i, 18+i), 2, np.uint8) for i in range(3)]
        for a, e, value in zip(images, definition['cameras'], (12, 8, 10)):
            x0,y0,x1,y1 = e['source']; a[y0:y1,x0:x1] = value
        r = evaluate_native(images, CHANNELS, 'independent-boxes', definition)
        self.assertEqual(r['gross']['signals'], [48, 32, 40])
        self.assertEqual(r['net']['signals'], [40, 24, 32])
        self.assertAlmostEqual(r['net']['continuum'], 88/3)
        self.assertAlmostEqual(r['net']['excess'], 32/3)
        self.assertAlmostEqual(r['net']['numeric_ratio'], 4/11)
        # Identical coordinates case agrees with unchanged extracted I arithmetic.
        for e in definition['cameras']: e['source'] = [1,1,3,3]
        same = [np.full((16,18), 2, np.float32) for _ in range(3)]
        for a, v in zip(same, (12,8,10)): a[1:3,1:3] = v
        source = np.zeros((16,18), bool); source[1:3,1:3] = True
        bg = np.zeros_like(source); bg[8:10,8:10] = True
        frozen = integrated_arithmetic(same, source, bg, 4, 4, Scratch(), unavailable_i())
        native = evaluate_native(same, CHANNELS, 'independent-boxes', definition)
        self.assertEqual(native['net']['signals'], frozen['net_signals'])
        self.assertEqual(native['net']['continuum'], frozen['net_continuum'])
        self.assertEqual(native['net']['excess'], frozen['additive_excess'])

    def test_whole_frame_exact_sums_means_and_unequal_dimensions(self):
        images = [np.full(shape, value, np.uint8) for shape, value in
                  [((8,9),12), ((10,11),8), ((12,13),10)]]
        r = evaluate_native(images, CHANNELS, 'whole-frame')
        self.assertEqual(r['gross']['signals'], [864,880,1560])
        self.assertEqual([c['source']['mean'] for c in r['channels']], [12,8,10])
        self.assertEqual([c['source']['pixel_count'] for c in r['channels']], [72,110,156])
        self.assertEqual(r['gross']['continuum'], 4000/3)
        self.assertIsNone(r['net'])
        self.assertIn('DIFFERENT_IMAGE_DIMENSIONS_NO_RESAMPLING', r['reason_codes'])

    def test_denominator_roundoff_negative_zero_and_saturation(self):
        for signals in ([0,0,0], [4,-1,-1], [1,1e-18,1e-18]):
            self.assertIsNone(spectral_summary(signals)['numeric_ratio'])
        self.assertEqual(spectral_summary([12,8,10])['denominator_reason'], 'DENOM_UNCERTAINTY_UNRESOLVED')
        images = [np.full((8,8), 255, np.uint8)]*3
        r = evaluate_native(images, CHANNELS, 'whole-frame')
        self.assertEqual(r['channels'][0]['source']['pixel_count'],64)
        self.assertEqual(r['gross']['signals'],[16320]*3)
        self.assertIsNone(r['gross']['numeric_ratio'])
        self.assertIn('SATURATION_PRESENT_NO_PIXELS_REMOVED',r['reason_codes'])

    def test_invalid_mapping_missing_image_and_mode_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, request = self.make_request(root)
            request['channels'][1]['wavelength_nm'] = 770
            path.write_text(json.dumps(request))
            self.assertIn('WAVELENGTH_OR_CAMERA_MAPPING', evaluate_jpeg(path, opt_in=True, processing_mode='whole-frame')['reason_codes'])
            request['channels'][1]['wavelength_nm'] = 750
            request['channels'][0]['camera_id'], request['channels'][1]['camera_id'] = '1', '0'
            path.write_text(json.dumps(request))
            self.assertIn('WAVELENGTH_OR_CAMERA_MAPPING', evaluate_jpeg(path, opt_in=True, processing_mode='whole-frame')['reason_codes'])
            request['channels'][0]['camera_id'], request['channels'][1]['camera_id'] = '0', '1'
            request['channels'][1]['path'] = 'missing.jpg'
            path.write_text(json.dumps(request))
            r = evaluate_jpeg(path, opt_in=True, processing_mode='whole-frame')
            self.assertNotIn('provisional',r)
            self.assertTrue(any('FileNotFoundError' in reason for reason in r['reason_codes']))
            request['processing_mode'] = 'whole-frame'
            path.write_text(json.dumps(request))
            self.assertIn('PROCESSING_MODE_CONFLICT',evaluate_jpeg(path,opt_in=True,processing_mode='independent-boxes')['reason_codes'])

    def test_malformed_camera_mapping_blocks_all_modes_before_image_read(self):
        cases = [
            ('integer', [0, 1, 2], [770, 750, 780]),
            ('mixed', ['0', 1, '2'], [770, 750, 780]),
            ('boolean', [False, True, '2'], [770, 750, 780]),
            ('duplicate', ['0', '0', '2'], [770, 750, 780]),
            ('unhashable', ['0', [], '2'], [770, 750, 780]),
            ('unknown', ['0', '1', '3'], [770, 750, 780]),
            ('swapped', ['0', '1', '2'], [750, 770, 780]),
            ('integer_swapped', [0, 1, 2], [750, 770, 780]),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path, request = self.make_request(Path(directory))
            for mode in ('registered-boxes', 'independent-boxes', 'whole-frame'):
                for name, ids, waves in cases:
                    with self.subTest(mode=mode, mapping=name):
                        for c, camera_id, wave in zip(request['channels'], ids, waves):
                            c.update(camera_id=camera_id, wavelength_nm=wave)
                        path.write_text(json.dumps(request))
                        with patch('research.anchor_g2.exploratory_jpeg.reference_bytes') as read_image:
                            result = evaluate_jpeg(path, opt_in=True, processing_mode=mode)
                        read_image.assert_not_called()
                        self.assertIn('WAVELENGTH_OR_CAMERA_MAPPING', result['reason_codes'])
                        self.assertNotIn('provisional', result)
                        self.assertEqual(result['channel_statistics'], [])
                        self.assertIsNone(result['geometry'])
                        self.assertIsNone(result['P']); self.assertIsNone(result['I'])
                        self.assertEqual(result['status'], 'UNKNOWN')
                        self.assertEqual(result['production_decision'], 'NOT_CALIBRATED')

    def test_native_regions_explicit_disjoint_and_no_silent_ignored_boxes(self):
        with tempfile.TemporaryDirectory() as directory:
            path, request = self.make_request(Path(directory))
            r = evaluate_jpeg(path,opt_in=True,processing_mode='independent-boxes')
            self.assertIn('INDEPENDENT_CAMERA_REGIONS_REQUIRED',r['reason_codes'])
            request['independent_regions'] = regions()
            path.write_text(json.dumps(request))
            self.assertIn('WHOLE_FRAME_DOES_NOT_USE_REGIONS',evaluate_jpeg(path,opt_in=True,processing_mode='whole-frame')['reason_codes'])
            request['independent_regions']['cameras'][1]['source'] = [8,8,10,10]
            path.write_text(json.dumps(request))
            self.assertIn('REGIONS_OVERLAP',evaluate_jpeg(path,opt_in=True,processing_mode='independent-boxes')['reason_codes'])

    def test_cli_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'report.json'; output.write_bytes(b'KEEP')
            r = subprocess.run([sys.executable,'-B','-m','research.anchor_g2','missing.json',
                '--exploratory-jpeg','--processing-mode','whole-frame','--output',str(output)],
                capture_output=True,text=True,timeout=20)
            self.assertEqual(r.returncode,2)
            self.assertIn('--output must name a new file',r.stderr)
            self.assertEqual(output.read_bytes(),b'KEEP')


if __name__ == '__main__':
    unittest.main()
