"""Generated-image selector/preset tests; no interactive approvals or B3 run."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import sys
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

from .box_selector import (MAPPING, WARNING, apply_preset, drag_box, load_triplet,
                           main, original_point, validate_preset, write_new, Selector)
from .exploratory_jpeg import evaluate_jpeg


class BoxSelectorTests(unittest.TestCase):
    def fixture(self, root):
        paths = {}
        for camera, wave in MAPPING.items():
            path = root/f'cam{camera}_{wave}nm.jpg'
            image = np.full((32, 48), 20, np.uint8)
            image[2:8, 2:8] = 40
            ok, data = cv2.imencode('.jpg', image)
            self.assertTrue(ok)
            path.write_bytes(data.tobytes()); paths[camera] = path
        triplet = load_triplet(paths)
        preset = dict(schema='NATIVE_BOX_PRESET_V1', warning=WARNING,
                      selection_rationale='Generated geometry, not human approval', cameras=[
                          dict(camera_id=c, wavelength_nm=w, dimensions=[48, 32],
                               source=[2, 2, 8, 8], background=[20, 2, 26, 8])
                          for c, w in MAPPING.items()])
        return paths, triplet, preset

    def test_scaled_coordinates_and_reversed_drag(self):
        self.assertEqual(original_point(330, 210, (1280, 960), (640, 480)), (660, 420))
        self.assertEqual(original_point(640, 480, (1280, 960), (640, 480)), (1280, 960))
        self.assertEqual(original_point(60, 50, (120, 100), (73, 61)), (99, 82))
        self.assertEqual(drag_box((880, 610), (660, 420)), [660, 420, 880, 610])
        self.assertLess(original_point(-2, 0, (1280, 960), (640, 480))[0], 0)

    def test_camera_mapping_rejected_without_coercion(self):
        with tempfile.TemporaryDirectory() as d:
            paths, triplet, preset = self.fixture(Path(d))
            for ids in ([0, 1, 2], ['0', 1, '2'], [False, True, '2'], ['0', '0', '2']):
                bad = copy.deepcopy(preset)
                for entry, value in zip(bad['cameras'], ids):
                    entry['camera_id'] = value
                with self.subTest(ids=ids), self.assertRaisesRegex(ValueError, 'MAPPING'):
                    validate_preset(bad)
            bad = copy.deepcopy(preset); bad['cameras'][0]['wavelength_nm'] = 750
            with self.assertRaisesRegex(ValueError, 'MAPPING'): validate_preset(bad)
            swapped = dict(paths); swapped['0'], swapped['1'] = paths['1'], paths['0']
            with self.assertRaisesRegex(ValueError, 'MAPPING'): load_triplet(swapped)
            duplicate = dict(paths); duplicate['1'] = paths['0']
            with self.assertRaisesRegex(ValueError, 'DUPLICATE'): load_triplet(duplicate)

    def test_empty_outside_overlap_and_dimensions_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _, triplet, preset = self.fixture(Path(d))
            for box in ([2, 2, 2, 8], [-1, 2, 8, 8], [2, 2, 49, 8], [20, 2, 26, 8], [True, 2, 8, 8]):
                bad = copy.deepcopy(preset); bad['cameras'][0]['source'] = box
                with self.subTest(box=box), self.assertRaises(ValueError): validate_preset(bad)
            bad = copy.deepcopy(preset); bad['cameras'][0]['dimensions'] = [49, 32]
            with self.assertRaisesRegex(ValueError, 'DIMENSIONS_MISMATCH'): apply_preset(bad, triplet)
            bad = copy.deepcopy(preset); bad['warning'] = 'approved'
            with self.assertRaisesRegex(ValueError, 'WARNING'): validate_preset(bad)

    def test_roundtrip_manifest_and_existing_analyzer(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            paths, triplet, preset = self.fixture(root)
            before = {c: p.read_bytes() for c, p in paths.items()}
            write_new(root/'preset.json', preset)
            saved = json.loads((root/'preset.json').read_text())
            metadata = {c: {'exposure_us': 50} for c in MAPPING}
            manifest = apply_preset(saved, triplet, metadata)
            self.assertEqual(manifest['channels'][0]['acquisition']['exposure_us'], 50)
            self.assertIsNone(manifest['channels'][0]['acquisition']['gain'])
            write_new(root/'manifest.json', manifest)
            result = evaluate_jpeg(root/'manifest.json', opt_in=True)
            self.assertEqual(result['registration']['state'], 'NOT_REQUESTED')
            self.assertEqual(result['provisional']['schema'], 'PROVISIONAL_JPEG_DIAGNOSTICS')
            self.assertEqual(result['status'], 'UNKNOWN')
            self.assertEqual(result['production_decision'], 'NOT_CALIBRATED')
            self.assertIsNone(result['P']); self.assertIsNone(result['I'])
            self.assertEqual(before, {c: p.read_bytes() for c, p in paths.items()})
            with self.assertRaises(ValueError): apply_preset(preset, triplet, {'0': {'gain': 1}})

    def test_new_triplet_hashes_and_safe_cli_output(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            paths, triplet, preset = self.fixture(root)
            write_new(root/'preset.json', preset)
            ok, encoded = cv2.imencode('.jpg', np.full((32, 48), 65, np.uint8))
            self.assertTrue(ok)
            # Generated replacement image only; preset must bind the new bytes.
            paths['0'].write_bytes(encoded.tobytes())
            args = ['apply', '--preset', str(root/'preset.json'), '--output', str(root/'new.json'),
                    '--acknowledge-unverified-reuse']
            for camera, path in paths.items(): args += ['--cam'+camera, str(path)]
            main(args)
            result = json.loads((root/'new.json').read_text())
            self.assertNotEqual(result['channels'][0]['sha256'], triplet[0]['sha256'])
            existing = (root/'new.json').read_bytes()
            with self.assertRaises(SystemExit): main(args)
            self.assertEqual(existing, (root/'new.json').read_bytes())
            with self.assertRaises(FileExistsError): write_new(root/'new.json', {})
            with self.assertRaises(FileExistsError): write_new(root/'preset.json', {})
            with self.assertRaises(ValueError): write_new(root/'bad.json', {'v': float('nan')})
            self.assertFalse((root/'bad.json').exists())


@unittest.skipUnless(sys.platform == 'win32', 'Mapped Windows Tk regression tests')
class WindowsTkSelectorTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = tk.Tk()
        self.addCleanup(lambda: self.root.destroy() if self.root.winfo_exists() else None)
        self.root.withdraw()
        self.root.geometry('1000x800+30+30')
        image = np.full((1088, 1456, 3), (30, 80, 140), np.uint8)
        self.triplet = [dict(camera_id=c, wavelength_nm=w, dimensions=[1456, 1088],
                             image=image, path=f'generated_cam{c}_{w}nm.jpg') for c, w in MAPPING.items()]
        self.output = Path(self.temp.name)/'boxes.json'
        self.app = Selector(self.root, self.triplet, 'Generated GUI test only', self.output)

    def map_window(self):
        self.root.deiconify()
        self.root.update()
        self.assertTrue(self.app.canvas.winfo_ismapped())
        self.assertGreater(self.app.display[0], 500)
        self.assertGreater(self.app.display[1], 400)

    def gesture(self, start, end):
        canvas = self.app.canvas
        def screen(p):
            return (round(p[0]*self.app.display[0]/1456-canvas.canvasx(0)),
                    round(p[1]*self.app.display[1]/1088-canvas.canvasy(0)))
        for name, p in [('<ButtonPress-1>', start), ('<B1-Motion>', end), ('<ButtonRelease-1>', end)]:
            x, y = screen(p)
            canvas.event_generate(name, x=x, y=y)
            self.root.update()

    def assert_box_close(self, actual, expected, tolerance=1):
        for a, b in zip(actual, expected): self.assertLessEqual(abs(a-b), tolerance)

    def test_startup_1px_regression_and_image_rendering(self):
        # This is exactly the user's giant-coordinate signature under the old Fit.
        self.assertEqual(original_point(20, 112, (1456, 1088), (1, 1)), (29120, 121856))
        self.assertIsNone(self.app.display)
        self.app.press(SimpleNamespace(x=20, y=112))
        self.app.release(SimpleNamespace(x=402, y=365))
        self.assertEqual(self.app.boxes, {})
        self.map_window()
        self.assertEqual(self.app.boxes, {})
        self.assertNotIn('OUT_OF_BOUNDS', self.app.status.get())
        self.assertEqual((self.app.photo.width(), self.app.photo.height()), self.app.display)
        self.assertEqual(self.app.photo.get(10, 10), (140, 80, 30))
        images = [i for i in self.app.canvas.find_all() if self.app.canvas.type(i) == 'image']
        self.assertEqual(len(images), 1)
        self.assertEqual(self.app.canvas.itemcget(images[0], 'image'), str(self.app.photo))
        self.assertEqual(self.app.canvas.bbox(images[0]), (0, 0, *self.app.display))

    def test_fit_draw_move_resize_refit_and_scrolled_one_to_one(self):
        self.map_window()
        self.gesture((660, 420), (880, 610))
        self.assert_box_close(self.app.boxes['source'], [660, 420, 880, 610])
        self.gesture((770, 510), (790, 530))
        self.assert_box_close(self.app.boxes['source'], [680, 440, 900, 630], 2)
        self.gesture(tuple(self.app.boxes['source'][2:]), (920, 650))
        self.assert_box_close(self.app.boxes['source'], [680, 440, 920, 650], 2)
        before = copy.deepcopy(self.app.boxes)
        self.root.geometry('900x720+30+30'); self.root.update()
        self.assertEqual(self.app.boxes, before)
        self.app.show(scale=1); self.root.update()
        self.assertEqual(self.app.display, (1456, 1088))
        self.app.canvas.xview_moveto(.25); self.app.canvas.yview_moveto(.25)
        self.root.update()
        self.assertGreater(self.app.canvas.canvasx(0), 0)
        self.assertGreater(self.app.canvas.canvasy(0), 0)
        self.app.reset()
        self.gesture((660, 420), (880, 610))
        self.assertEqual(self.app.boxes['source'], [660, 420, 880, 610])
        self.gesture((770, 510), (780, 520))
        self.assertEqual(self.app.boxes['source'], [670, 430, 890, 620])
        self.gesture((890, 620), (900, 630))
        self.assertEqual(self.app.boxes['source'], [670, 430, 900, 630])
        self.app.role.set('background')
        self.gesture((980, 420), (1140, 610))
        self.assertEqual(self.app.boxes['background'], [980, 420, 1140, 610])
        validate_preset(dict(schema='NATIVE_BOX_PRESET_V1', warning=WARNING,
                            selection_rationale='Generated only', cameras=[
                                dict(camera_id=c, wavelength_nm=w, dimensions=[1456,1088], **self.app.boxes)
                                for c,w in MAPPING.items()]))

    def test_invalid_gestures_and_save_boundary(self):
        self.map_window()
        self.gesture((660,420), (660,420))
        self.assertEqual(self.app.boxes, {})
        self.gesture((660,420), (880,610))
        source = copy.deepcopy(self.app.boxes)
        self.app.role.set('background')
        self.gesture((700,450), (850,600))
        self.assertEqual(self.app.boxes, source)
        # Pointer outside the image must not create giant coordinates.
        self.app.press(SimpleNamespace(x=self.app.display[0]+50, y=50))
        self.assertIsNone(self.app.drag)
        with patch.object(self.app.messagebox, 'showerror') as error, \
             patch.object(self.app.messagebox, 'askyesno') as approval:
            self.app.confirm()
            error.assert_called_once(); approval.assert_not_called()
            self.assertFalse(self.output.exists())
        self.gesture((980,420), (1140,610))
        saved_boxes = copy.deepcopy(self.app.boxes)
        with patch.object(self.app.messagebox, 'askyesno', return_value=True), \
             patch.object(self.app.messagebox, 'showinfo'), patch.object(self.root, 'destroy'):
            for i in range(3):
                self.app.boxes = copy.deepcopy(saved_boxes)
                self.app.confirm()
            saved = json.loads(self.output.read_text())
            validate_preset(saved)
            self.assertEqual(saved['cameras'][0]['source'], saved_boxes['source'])


if __name__ == '__main__':
    unittest.main()
