"""Option 1 manual shared boxes; unchanged registration and measurement gates."""
import copy
from pathlib import Path
import sys

import cv2
import numpy as np

from .adapter import canonical, read, parse, sha, require, register, Ineligible
from .box_selector import (MAPPING, WARNING, Selector, validate_boxes, rectangle,
                           apply_preset, write_new)


def configuration():
    root = Path(__file__).parent
    return dict(method='PHASE_ANCHOR_G2_COMPATIBLE', correction='NONE_UNVERIFIED',
                jpeg_encoding='UNIT_GAIN_ZERO_OFFSET_NOT_CALIBRATION',
                grayscale='OPENCV_BGR2GRAY_OR_MONO_UNCHANGED',
                python=sys.version.split()[0], numpy=np.__version__, opencv=cv2.__version__,
                opencv_build_sha256=sha(cv2.getBuildInformation().encode()),
                source_sha256={p: sha(read(root/p)) for p in
                               ('adapter.py', 'frozen/_legacy.py', 'exploratory_jpeg.py')})


def prepare(triplet):
    require(sys.version_info[:3] == (3, 12, 14) and np.__version__ == '2.3.5'
            and cv2.__version__ == '5.0.0', 'UNSUPPORTED_RUNTIME_VERSION')
    require(len(triplet) == 3 and all(type(e['camera_id']) is str for e in triplet)
            and [(e['camera_id'], e['wavelength_nm']) for e in triplet] == list(MAPPING.items()),
            'WAVELENGTH_OR_CAMERA_MAPPING')
    images = [cv2.cvtColor(e['image'], cv2.COLOR_BGR2GRAY) if e['image'].ndim == 3
              else e['image'] for e in triplet]
    shape = images[0].shape
    require(all(a.shape == shape for a in images), 'IMAGE_REPRESENTATION')
    diagnostics = {}
    try:
        view, geometry = register(images, [np.ones(shape, np.float32) for _ in images],
                                  [np.zeros(shape, np.float32) for _ in images], diagnostics)
    except Exception as error:
        raise Ineligible('REGISTRATION_FAILED: ' + str(error)) from error
    cfg = configuration()
    frame = dict(camera_id='0', wavelength_nm=770, dimensions=[shape[1], shape[0]],
                 coordinates='NATIVE_770_GRID_NO_EXIF_ROTATION_XY_RIGHT_BOTTOM_EXCLUSIVE',
                 mapping=MAPPING, configuration_sha256=sha(canonical(cfg)))
    evidence = dict(frame=frame, configuration=cfg, geometry=geometry,
                    image_sha256s=[e['sha256'] for e in triplet],
                    image_paths=[e['path'] for e in triplet],
                    common_support_sha256=sha(view.common.tobytes()),
                    common_support_pixels=int(view.common.sum()))
    # Display only the unchanged reference view, NOT spectral excess. Magenta
    # marks excluded pixels; all coordinates retain the original 770 grid.
    display = cv2.cvtColor(images[0], cv2.COLOR_GRAY2BGR)
    display[~view.common] = (255, 0, 255)
    return view, evidence, display


def check_regions(boxes, view):
    h, w = view.common.shape
    validate_boxes(dict(dimensions=[w, h], source=boxes.get('source'), background=boxes.get('background')))
    for name in ('source', 'background'):
        mask = rectangle(boxes[name], (h, w))
        require(not (mask & ~view.common).any(), 'REGION_OUTSIDE_COMMON_SUPPORT_NO_PIXELS_DROPPED')


def make_preset(boxes, rationale, evidence, view):
    require(type(rationale) is str and rationale.strip(), 'SELECTION_RATIONALE_REQUIRED')
    check_regions(boxes, view)
    return dict(schema='REGISTERED_BOX_PRESET_V1', source=copy.deepcopy(boxes['source']),
                background=copy.deepcopy(boxes['background']), selection_rationale=rationale,
                warning=WARNING, reference=copy.deepcopy(evidence),
                scientific_decision='UNKNOWN', production_decision='NOT_CALIBRATED')


def validate_preset(preset, evidence, view):
    require(type(preset) is dict and preset.get('schema') == 'REGISTERED_BOX_PRESET_V1', 'PRESET_SCHEMA')
    require(preset.get('warning') == WARNING and preset.get('scientific_decision') == 'UNKNOWN'
            and preset.get('production_decision') == 'NOT_CALIBRATED', 'UNVERIFIED_WARNING_REQUIRED')
    require(type(preset.get('selection_rationale')) is str and preset['selection_rationale'].strip(),
            'SELECTION_RATIONALE_REQUIRED')
    reference = preset.get('reference')
    require(type(reference) is dict and type(reference.get('configuration')) is dict,
            'REFERENCE_PROVENANCE_REQUIRED')
    require(reference.get('frame') == evidence['frame']
            and sha(canonical(reference['configuration'])) == evidence['frame']['configuration_sha256'],
            'REFERENCE_FRAME_OR_CONFIGURATION_MISMATCH')
    hashes = reference.get('image_sha256s')
    require(type(hashes) is list and len(hashes) == 3
            and all(type(h) is str and len(h) == 64 and all(c in '0123456789abcdef' for c in h)
                    for h in hashes), 'REFERENCE_IMAGE_HASHES_REQUIRED')
    check_regions(preset, view)


def apply_registered(preset, triplet, metadata=None):
    # Never reuse a reference transform on a later capture.
    view, evidence, _ = prepare(triplet)
    validate_preset(preset, evidence, view)
    native = dict(schema='NATIVE_BOX_PRESET_V1', warning=WARNING,
                  selection_rationale=preset['selection_rationale'], cameras=[
                      dict(camera_id=c, wavelength_nm=w, dimensions=evidence['frame']['dimensions'],
                           source=preset['source'], background=preset['background']) for c,w in MAPPING.items()])
    manifest = apply_preset(native, triplet, metadata)
    del manifest['independent_regions']
    del manifest['box_preset_provenance']
    manifest.update(processing_mode='registered-boxes', provisional_regions=dict(
        schema='PROVISIONAL_RECTANGLES_V1', source=copy.deepcopy(preset['source']),
        background=copy.deepcopy(preset['background']), selection_rationale=preset['selection_rationale']))
    manifest['registered_box_provenance'] = dict(preset_sha256=sha(canonical(preset)),
        current_registration=evidence, warning=WARNING,
        compatibility='GRID_AND_SOFTWARE_ONLY; CROSS_SESSION_PHYSICAL_COMPATIBILITY_UNVERIFIED')
    return manifest


class RegisteredSelector(Selector):
    confirm_label = 'Confirm shared boxes / save'
    footer = 'Registered 770 grid. Magenta = excluded support. Closing before confirmation saves nothing.'

    def __init__(self, root, triplet, rationale, output, prepared=None):
        self.view, self.evidence, image = prepared if prepared is not None else prepare(triplet)
        self.rationale = rationale
        reference = dict(triplet[0], image=image)
        super().__init__(root, [reference], rationale, output)

    def render(self):
        super().render()
        if self.display is not None:
            self.title.set('REGISTERED REFERENCE: camera 0 / 770 nm — original pixel grid; engineering alignment only')

    def confirm(self):
        try:
            preset = make_preset(self.boxes, self.rationale, self.evidence, self.view)
        except ValueError as error:
            self.messagebox.showerror('Shared boxes rejected', str(error)); return
        if not self.messagebox.askyesno('Confirm shared regions', f'{self.boxes}\n\n{WARNING}'):
            return
        try:
            write_new(self.output, preset)
        except (ValueError, OSError) as error:
            self.messagebox.showerror('Preset not saved', str(error)); return
        self.messagebox.showinfo('Saved', str(self.output))
        self.root.destroy()


def run(args, triplet):
    if args.command == 'apply-registered':
        preset = parse(read(args.preset, 1024*1024))
        metadata = parse(read(args.metadata, 1024*1024)) if args.metadata else None
        write_new(args.output, apply_registered(preset, triplet, metadata))
    else:
        require(args.rationale.strip(), 'SELECTION_RATIONALE_REQUIRED')
        prepared = prepare(triplet)  # Reject before opening the selection window.
        print('Registration accepted for engineering use only. Select shared boxes; magenta pixels are excluded.')
        import tkinter as tk
        root = tk.Tk()
        RegisteredSelector(root, triplet, args.rationale, args.output, prepared)
        root.mainloop()
