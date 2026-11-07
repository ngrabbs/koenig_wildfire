"""Manual native-image boxes; separate from analysis and scientific eligibility."""
import argparse
import copy
import json
from pathlib import Path
import re

import cv2
import numpy as np

from .adapter import MAX_PIXELS, parse, read, require, sha
from .native_modes import rectangle

MAPPING = {'0': 770, '1': 750, '2': 780}
WARNING = ('UNVERIFIED: physical correspondence, source containment, background '
           'comparability, JPEG radiometry and synchronization. Manual confirmation '
           'does not establish scientific eligibility; UNKNOWN / NOT_CALIBRATED.')


def validate_boxes(entry):
    dims = entry.get('dimensions')
    require(type(dims) is list and len(dims) == 2
            and all(type(v) is int and v > 0 for v in dims)
            and dims[0] * dims[1] <= MAX_PIXELS, 'INVALID_DIMENSIONS')
    width, height = dims
    source = rectangle(entry.get('source'), (height, width))
    background = rectangle(entry.get('background'), (height, width))
    require(not (source & background).any(), 'REGIONS_OVERLAP')


def validate_preset(preset):
    require(type(preset) is dict and preset.get('schema') == 'NATIVE_BOX_PRESET_V1', 'PRESET_SCHEMA')
    require(type(preset.get('selection_rationale')) is str
            and preset['selection_rationale'].strip(), 'SELECTION_RATIONALE_REQUIRED')
    require(preset.get('warning') == WARNING, 'UNVERIFIED_WARNING_REQUIRED')
    entries = preset.get('cameras')
    require(type(entries) is list and len(entries) == 3
            and all(type(e) is dict and type(e.get('camera_id')) is str
                    and type(e.get('wavelength_nm')) is int for e in entries)
            and {e['camera_id'] for e in entries} == set(MAPPING)
            and {e['camera_id']: e['wavelength_nm'] for e in entries} == MAPPING,
            'WAVELENGTH_OR_CAMERA_MAPPING')
    for entry in entries:
        validate_boxes(entry)
    return preset


def original_point(x, y, original_size, display_size):
    """Canvas-relative coordinates; preserve out-of-bounds values for rejection."""
    return (round(x * original_size[0] / display_size[0]),
            round(y * original_size[1] / display_size[1]))


def drag_box(start, end):
    return [min(start[0], end[0]), min(start[1], end[1]),
            max(start[0], end[0]), max(start[1], end[1])]


def load_triplet(paths):
    require(type(paths) is dict and set(paths) == set(MAPPING), 'WAVELENGTH_OR_CAMERA_MAPPING')
    result = []
    resolved = [Path(paths[c]).resolve(strict=True) for c in MAPPING]
    require(len(set(resolved)) == 3, 'DUPLICATE_IMAGE_PATH')
    events = set()
    for (camera, wave), path in zip(MAPPING.items(), resolved):
        require(path.suffix.lower() in ('.jpg', '.jpeg'), 'JPEG_INPUT_REQUIRED')
        # Explicit flags are the mapping declaration. Check filename tags when present.
        tag = re.search(r'cam([0-9]+)_([0-9]+)nm', path.name, re.I)
        require(tag is None or tag.groups() == (camera, str(wave)), 'FILENAME_CAMERA_MAPPING_MISMATCH')
        event = re.match(r'(\d{8}_\d{6}_\d{3})_cam', path.name)
        if event:
            events.add(event.group(1))
        raw = read(path)
        require(raw.startswith(b'\xff\xd8'), 'JPEG_INPUT_REQUIRED')
        image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
        require(isinstance(image, np.ndarray) and image.dtype == np.uint8
                and image.ndim in (2, 3) and image.shape[0]*image.shape[1] <= MAX_PIXELS
                and (image.ndim == 2 or image.shape[2] == 3), 'JPEG_REPRESENTATION')
        result.append(dict(camera_id=camera, wavelength_nm=wave, path=str(path),
                           sha256=sha(raw), dimensions=[image.shape[1], image.shape[0]], image=image))
    require(len(events) <= 1, 'MIXED_CAPTURE_EVENTS')
    return result


def write_new(path, value):
    encoded = json.dumps(value, indent=2, allow_nan=False) + '\n'
    with Path(path).open('x', encoding='utf-8') as stream:
        stream.write(encoded)


def apply_preset(preset, triplet, metadata=None):
    validate_preset(preset)
    require([(c['camera_id'], c['wavelength_nm']) for c in triplet] == list(MAPPING.items()),
            'WAVELENGTH_OR_CAMERA_MAPPING')
    template = json.loads((Path(__file__).parent/'example_independent_boxes.json').read_text())
    fields = set(template['channels'][0]['acquisition'])
    if metadata is not None:
        require(type(metadata) is dict and set(metadata) == set(MAPPING)
                and all(type(v) is dict and set(v) <= fields for v in metadata.values()),
                'ACQUISITION_METADATA_MUST_BE_CAMERA_KEYED')
    boxes = {e['camera_id']: e for e in preset['cameras']}
    for channel, entry, region in zip(template['channels'], triplet, template['independent_regions']['cameras']):
        camera = entry['camera_id']
        require(boxes[camera]['dimensions'] == entry['dimensions'], 'PRESET_IMAGE_DIMENSIONS_MISMATCH')
        channel.update(path=entry['path'], sha256=entry['sha256'])
        channel['acquisition'] = dict.fromkeys(sorted(fields))
        if metadata is not None:
            channel['acquisition'].update(metadata[camera])
        region.update(source=copy.deepcopy(boxes[camera]['source']),
                      background=copy.deepcopy(boxes[camera]['background']), source_containment_note=WARNING)
    template['independent_regions'].update(selection_rationale=preset['selection_rationale'],
                                           correspondence_description=WARNING)
    template['box_preset_provenance'] = dict(
        sha256=sha(json.dumps(preset, sort_keys=True, separators=(',', ':')).encode()),
        dimensions={c: boxes[c]['dimensions'] for c in MAPPING}, warning=WARNING,
        metadata_status='USER_SUPPLIED_UNVERIFIED' if metadata is not None else 'UNKNOWN')
    return template


class Selector:
    """Tk canvas; resizing/moving is always translated back to native pixels."""
    confirm_label = 'Confirm camera / next'
    footer = 'Unverified physical correspondence. Closing before all 3 confirmations saves nothing.'
    def __init__(self, root, triplet, rationale, output):
        import tkinter as tk
        from tkinter import messagebox
        self.tk, self.messagebox, self.root = tk, messagebox, root
        self.triplet, self.output, self.index = triplet, output, 0
        self.preset = dict(schema='NATIVE_BOX_PRESET_V1', selection_rationale=rationale,
                           warning=WARNING, cameras=[])
        self.boxes, self.drag = {}, None
        self.display = None
        self.fit_mode, self.scale, self.render_pending = True, 1, None
        self.role = tk.StringVar(value='source')
        self.title = tk.StringVar()
        self.status = tk.StringVar()
        root.title('Research boxes — UNKNOWN / NOT_CALIBRATED')
        tk.Label(root, textvariable=self.title).pack()
        tk.Label(root, text='Draw manually. Drag inside selected box to move; drag a corner to resize.').pack()
        bar = tk.Frame(root); bar.pack()
        for role, color in [('source', '#ffff00'), ('background', '#0080ff')]:
            tk.Radiobutton(bar, text=role.upper(), variable=self.role, value=role,
                           background=color).pack(side='left')
        for label, command in [('Fit', lambda: self.show(fit=True)),
                               ('1:1', lambda: self.show(scale=1)),
                               ('Reset camera', self.reset), (self.confirm_label, self.confirm)]:
            tk.Button(bar, text=label, command=command).pack(side='left')
        frame = tk.Frame(root); frame.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(frame, width=1000, height=650, background='#333333',
                                borderwidth=0, highlightthickness=0)
        xscroll = tk.Scrollbar(frame, orient='horizontal', command=self.canvas.xview)
        yscroll = tk.Scrollbar(frame, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=xscroll.set, yscrollcommand=yscroll.set)
        self.canvas.grid(row=0, column=0, sticky='nsew'); yscroll.grid(row=0, column=1, sticky='ns')
        xscroll.grid(row=1, column=0, sticky='ew')
        frame.rowconfigure(0, weight=1); frame.columnconfigure(0, weight=1)
        tk.Label(root, textvariable=self.status, wraplength=950).pack()
        tk.Label(root, text=self.footer).pack()
        self.canvas.bind('<ButtonPress-1>', self.press)
        self.canvas.bind('<B1-Motion>', self.motion)
        self.canvas.bind('<ButtonRelease-1>', self.release)
        self.canvas.bind('<Configure>', self.viewport_changed)
        self.canvas.bind('<Map>', self.viewport_changed)
        self.show(fit=True)

    def viewport_changed(self, event=None):
        # Windows may report a 1x1 canvas until it is mapped. Render only after
        # geometry negotiation, and refit when the actual viewport changes.
        if (self.fit_mode or self.display is None) and self.render_pending is None:
            self.render_pending = self.root.after_idle(self.render)

    def show(self, fit=False, scale=1):
        self.fit_mode, self.scale = fit, scale
        self.render()

    def render(self):
        if self.render_pending is not None:
            self.root.after_cancel(self.render_pending)
            self.render_pending = None
        if self.drag is not None:
            self.boxes = self.before_drag
        self.drag = None
        if (not self.canvas.winfo_ismapped() or self.canvas.winfo_width() <= 2
                or self.canvas.winfo_height() <= 2):
            self.display = None
            self.status.set('Waiting for image viewport; draw SOURCE and BACKGROUND after the image appears.')
            return
        entry = self.triplet[self.index]
        w, h = entry['dimensions']
        scale = self.scale
        if self.fit_mode:
            scale = min(1, self.canvas.winfo_width()/w, self.canvas.winfo_height()/h)
        self.display = (max(1, round(w*scale)), max(1, round(h*scale)))
        image = entry['image']
        rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB if image.ndim == 2 else cv2.COLOR_BGR2RGB)
        rgb = cv2.resize(rgb, self.display, interpolation=cv2.INTER_AREA)
        ppm = f'P6\n{self.display[0]} {self.display[1]}\n255\n'.encode() + rgb.tobytes()
        self.photo = self.tk.PhotoImage(data=ppm, format='PPM', master=self.root)
        self.canvas.delete('all')
        self.canvas.create_image(0, 0, anchor='nw', image=self.photo)
        self.canvas.configure(scrollregion=(0, 0, *self.display))
        self.canvas.xview_moveto(0); self.canvas.yview_moveto(0)
        self.title.set(f"Camera {entry['camera_id']} — {entry['wavelength_nm']} nm — {w}×{h} — {Path(entry['path']).name}")
        self.overlay()

    def point(self, event):
        require(self.display is not None, 'IMAGE_VIEWPORT_NOT_READY')
        return original_point(self.canvas.canvasx(event.x), self.canvas.canvasy(event.y),
                              self.triplet[self.index]['dimensions'], self.display)

    def press(self, event):
        if self.display is None:
            return
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        if not (0 <= x <= self.display[0] and 0 <= y <= self.display[1]):
            return  # Gray margin is not image content.
        p, role = self.point(event), self.role.get()
        self.drag_role, self.before_drag = role, copy.deepcopy(self.boxes)
        box = self.boxes.get(role)
        self.drag = ('draw', p, p)
        if box:
            corners = [(box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3])]
            w, h = self.triplet[self.index]['dimensions']
            for x, y in corners:
                if abs(p[0]-x)*self.display[0]/w <= 8 and abs(p[1]-y)*self.display[1]/h <= 8:
                    self.drag = ('draw', (box[2] if x == box[0] else box[0],
                                          box[3] if y == box[1] else box[1]), p)
                    return
            if box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]:
                self.drag = ('move', p, box[:])

    def motion(self, event):
        if self.drag is None:
            return
        mode, start, old = self.drag
        p = self.point(event)
        if mode == 'move':
            dx, dy = p[0]-start[0], p[1]-start[1]
            box = [old[0]+dx, old[1]+dy, old[2]+dx, old[3]+dy]
        else:
            box = drag_box(start, p)
        self.boxes[self.drag_role] = box
        self.overlay()

    def release(self, event):
        if self.drag is None:
            return
        self.motion(event)
        self.drag = None
        try:
            w, h = self.triplet[self.index]['dimensions']
            rectangle(self.boxes[self.drag_role], (h, w))
            if len(self.boxes) == 2:
                validate_boxes(dict(dimensions=[w, h], **self.boxes))
        except ValueError as error:
            self.boxes = self.before_drag
            self.overlay()
            self.status.set(f'Rejected gesture: {error}. Previous boxes retained; redraw inside the image.')

    def overlay(self):
        self.canvas.delete('boxes')
        if self.display is None:
            return
        w, h = self.triplet[self.index]['dimensions']
        for role, box in self.boxes.items():
            color = '#ffff00' if role == 'source' else '#0080ff'
            coords = [v*self.display[i % 2]/(w if i % 2 == 0 else h) for i, v in enumerate(box)]
            self.canvas.create_rectangle(*coords, outline=color, width=2, tags='boxes')
            self.canvas.create_text(coords[0], coords[1], text=role.upper(), anchor='sw', fill=color, tags='boxes')
        state = 'Draw SOURCE and BACKGROUND manually.'
        if len(self.boxes) == 2:
            try:
                validate_boxes(dict(dimensions=[w, h], **self.boxes))
                state = 'Valid boxes; confirm when ready.'
            except ValueError as error:
                state = str(error)
        self.status.set(f"Original pixels: {self.boxes} | {state}")

    def reset(self):
        self.boxes = {}; self.drag = None
        self.overlay()

    def confirm(self):
        entry = self.triplet[self.index]
        record = {k: v for k, v in entry.items() if k != 'image'}
        record.update(copy.deepcopy(self.boxes))
        try:
            validate_boxes(record)
        except ValueError as error:
            self.messagebox.showerror('Invalid boxes', str(error)); return
        if not self.messagebox.askyesno('Confirm manual regions', f"Camera {entry['camera_id']}: {self.boxes}\n\n{WARNING}"):
            return
        if self.index == 2:
            final = copy.deepcopy(self.preset)
            final['cameras'].append(record)
            try:
                validate_preset(final)
                write_new(self.output, final)
            except (ValueError, OSError) as error:
                self.messagebox.showerror('Preset not saved', str(error)); return
            self.messagebox.showinfo('Saved', str(self.output))
            self.root.destroy()
        else:
            self.preset['cameras'].append(record)
            self.index += 1; self.reset(); self.show(fit=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('select', 'apply', 'select-registered', 'apply-registered'):
        cmd = sub.add_parser(name)
        for camera, wave in MAPPING.items():
            cmd.add_argument('--cam'+camera, required=True, help=f'Original {wave} nm JPEG; explicit camera mapping')
        cmd.add_argument('--output', required=True, type=Path, help='NEW JSON file only')
        if name.startswith('select'):
            cmd.add_argument('--rationale', required=True)
        else:
            cmd.add_argument('--preset', required=True, type=Path)
            cmd.add_argument('--metadata', type=Path, help='Optional JSON keyed by string camera ID; acquisition fields only')
            cmd.add_argument('--acknowledge-unverified-reuse', action='store_true', required=True)
    args = parser.parse_args(argv)
    try:
        require(not args.output.exists(), 'OUTPUT_ALREADY_EXISTS')
        require(args.output.parent.is_dir(), 'OUTPUT_PARENT_MISSING')
        triplet = load_triplet({c: getattr(args, 'cam'+c) for c in MAPPING})
        if args.command.endswith('-registered'):
            from .registered_selector import run
            return run(args, triplet)
        if args.command == 'apply':
            preset = parse(read(args.preset, 1024*1024))
            metadata = parse(read(args.metadata, 1024*1024)) if args.metadata else None
            write_new(args.output, apply_preset(preset, triplet, metadata))
        else:
            require(args.rationale.strip(), 'SELECTION_RATIONALE_REQUIRED')
            import tkinter as tk
            root = tk.Tk()
            Selector(root, triplet, args.rationale, args.output)
            root.mainloop()
    except (ValueError, OSError, ImportError) as error:
        parser.exit(2, f'Box selector: {error}\n')


if __name__ == '__main__':
    main()
