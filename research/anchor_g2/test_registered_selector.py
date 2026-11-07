"""Generated registration and mapped Tk tests; no human ROI approval."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from .adapter import Scratch, View, sha
from .frozen.fixtures import produce
from .box_selector import MAPPING, load_triplet, main, write_new
from .registered_selector import (prepare, make_preset, validate_preset,
                                  apply_registered, RegisteredSelector)
from .exploratory_jpeg import evaluate_jpeg


class RegisteredTests(unittest.TestCase):
    def fixture(self, root):
        fixture = produce('P01_IDENTITY64', Scratch())
        paths = {}
        for i, (c, w) in enumerate(MAPPING.items()):
            image = cv2.imdecode(np.frombuffer(fixture.compressed[i], np.uint8), 0)
            ok, encoded = cv2.imencode('.jpg', image)
            self.assertTrue(ok)
            paths[c] = root/f'cam{c}_{w}nm.jpg'
            paths[c].write_bytes(encoded.tobytes())
        triplet = load_triplet(paths)
        view, evidence, display = prepare(triplet)
        preset = make_preset(dict(source=[16,16,24,24], background=[32,32,40,40]),
                             'Generated geometry only', evidence, view)
        return paths, triplet, view, evidence, display, preset

    def test_reference_rendering_and_registered_analysis_parity(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, triplet, view, evidence, display, preset = self.fixture(root)
            self.assertTrue(np.all(display[~view.common] == (255,0,255)))
            self.assertTrue(np.array_equal(display[:,:,0][view.common], triplet[0]['image'][view.common]))
            manifest = apply_registered(preset, triplet)
            self.assertNotIn('independent_regions', manifest)
            write_new(root/'request.json', manifest)
            result = evaluate_jpeg(root/'request.json', opt_in=True)
            self.assertEqual(result['geometry'], evidence['geometry'])
            self.assertEqual(result['status'], 'UNKNOWN')
            self.assertEqual(result['production_decision'], 'NOT_CALIBRATED')
            self.assertIsNone(result['I']); self.assertIsNone(result['P'])
            self.assertIsNotNone(result['provisional']['I_numerical'])

    def test_save_load_apply_new_images_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            paths, triplet, _, _, _, preset = self.fixture(root)
            write_new(root/'preset.json', preset)
            # New generated JPEG bytes (valid JPEG permits a trailing comment).
            for p in paths.values(): p.write_bytes(p.read_bytes()+b'new-generated-capture')
            args = ['apply-registered', '--preset', str(root/'preset.json'),
                    '--output', str(root/'request.json'), '--acknowledge-unverified-reuse']
            for c,p in paths.items(): args += ['--cam'+c, str(p)]
            with patch('research.anchor_g2.registered_selector.register', wraps=__import__(
                    'research.anchor_g2.registered_selector', fromlist=['register']).register) as reg:
                main(args)
                reg.assert_called_once()
            manifest = json.loads((root/'request.json').read_text())
            self.assertNotEqual(manifest['channels'][0]['sha256'], triplet[0]['sha256'])
            self.assertEqual(manifest['provisional_regions']['source'], preset['source'])
            before = (root/'request.json').read_bytes()
            with self.assertRaises(SystemExit): main(args)
            self.assertEqual(before, (root/'request.json').read_bytes())

    def test_frame_mapping_and_configuration_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            _, triplet, view, evidence, _, preset = self.fixture(Path(d))
            for key, value in [('camera_id','1'), ('wavelength_nm',750), ('dimensions',[65,64]),
                               ('coordinates','OTHER_GRID'), ('configuration_sha256','0'*64)]:
                bad = copy.deepcopy(preset); bad['reference']['frame'][key] = value
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'MISMATCH'):
                    validate_preset(bad, evidence, view)
            bad = copy.deepcopy(preset); bad['reference']['configuration']['correction'] = 'ASSUMED'
            with self.assertRaisesRegex(ValueError,'MISMATCH'): validate_preset(bad,evidence,view)
            triplet[0]['camera_id'] = 0
            with self.assertRaisesRegex(ValueError,'MAPPING'): prepare(triplet)

    def test_bad_boxes_and_support_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _, _, view, evidence, _, preset = self.fixture(Path(d))
            for box in ([1,1,4,4], [0,0,0,4], [-1,16,24,24], [32,32,40,40]):
                bad = dict(preset, source=box)
                with self.subTest(box=box), self.assertRaises(ValueError): validate_preset(bad,evidence,view)
            common = view.common.copy(); common[16,16] = False
            changed = View(view.planes, common, view.geometry)
            with self.assertRaisesRegex(ValueError,'COMMON_SUPPORT'):
                validate_preset(preset,evidence,changed)

    def test_flat_registration_failure_produces_no_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            paths, _, _, _, _, preset = self.fixture(root)
            write_new(root/'preset.json', preset)
            for p in paths.values():
                _, encoded = cv2.imencode('.jpg', np.full((64,64),50,np.uint8))
                p.write_bytes(encoded.tobytes())
            args = ['apply-registered','--preset',str(root/'preset.json'),'--output',str(root/'bad.json'),
                    '--acknowledge-unverified-reuse']
            for c,p in paths.items(): args += ['--cam'+c,str(p)]
            with self.assertRaises(SystemExit): main(args)
            self.assertFalse((root/'bad.json').exists())

    @unittest.skipUnless(sys.platform == 'win32', 'Mapped Windows Tk')
    def test_registered_tk_mouse_fit_one_to_one_and_confirmation(self):
        import tkinter as tk
        with tempfile.TemporaryDirectory() as d:
            rootdir = Path(d)
            _, triplet, _, evidence, _, _ = self.fixture(rootdir)
            # Large synthetic registered-view fixture isolates GUI mechanics.
            shape = (1088,1456)
            common = np.ones(shape,bool); common[:10] = False
            view = View([np.full(shape,80,np.float32)]*3, common, 'gui-only')
            image = np.full((*shape,3),80,np.uint8); image[~common] = (255,0,255)
            for e in triplet: e['dimensions'] = [1456,1088]
            evidence['frame']['dimensions'] = [1456,1088]
            root = tk.Tk(); root.withdraw(); root.geometry('1000x800+30+30')
            try:
                app = RegisteredSelector(root,triplet,'Generated GUI test',rootdir/'saved.json',(view,evidence,image))
                self.assertIsNone(app.display)
                root.deiconify(); root.update()
                self.assertIn('REGISTERED REFERENCE',app.title.get())
                self.assertEqual(app.photo.get(1,1),(255,0,255))
                def drag(start,end):
                    for event,p in [('<ButtonPress-1>',start),('<B1-Motion>',end),('<ButtonRelease-1>',end)]:
                        x=round(p[0]*app.display[0]/1456-app.canvas.canvasx(0))
                        y=round(p[1]*app.display[1]/1088-app.canvas.canvasy(0))
                        app.canvas.event_generate(event,x=x,y=y); root.update()
                for fit in (True,False):
                    app.show(fit=fit); root.update(); app.reset()
                    if not fit:
                        app.canvas.xview_moveto(.25); app.canvas.yview_moveto(.25); root.update()
                    for role,x in [('source',660),('background',980)]:
                        app.role.set(role)
                        drag((x,420),(x+140,560))
                        drag((x+70,490),(x+80,500))
                        drag(tuple(app.boxes[role][2:]),(x+160,580))
                        for a,b in zip(app.boxes[role],[x+10,430,x+160,580]):
                            self.assertLessEqual(abs(a-b),2 if fit else 0)
                with patch.object(app.messagebox,'askyesno',return_value=True), \
                     patch.object(app.messagebox,'showinfo'), patch.object(root,'destroy'):
                    app.confirm()
                    saved = json.loads((rootdir/'saved.json').read_text())
                    validate_preset(saved,evidence,view)
                    with patch.object(app.messagebox,'showerror') as error:
                        app.confirm(); error.assert_called_once()
            finally:
                root.destroy()


if __name__ == '__main__':
    unittest.main()
