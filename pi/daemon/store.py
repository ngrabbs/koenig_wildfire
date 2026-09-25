"""Image directory: list, fetch path, delete, clear. Path-traversal safe.

Filenames are UTC-timestamp-prefixed so a three-channel burst sorts
together in alphabetical order, e.g.

    20260917_211238_123_cam0_750nm.jpg
    20260917_211238_123_cam1_770nm.jpg
    20260917_211238_123_cam2_780nm.jpg
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
import re

_ID_RE = re.compile(r"^[0-9A-Za-z_\-]+\.(jpg|jpeg|png)$")
_STEM_RE = re.compile(r"\d{8}_\d{6}_\d{3}", re.ASCII)
_FRAME_RE = re.compile(
    r"(?P<stem>\d{8}_\d{6}_\d{3})_cam(?P<port>[0-2])_"
    r"(?P<wavelength>\d{3})nm\.(?:jpe?g|png|tiff?)",
    re.ASCII | re.IGNORECASE,
)


@dataclass(frozen=True)
class FrameRef:
    path: Path
    stem: str
    port: int
    wavelength_nm: int


class TripletSelectionError(ValueError):
    """Invalid or ambiguous stored event, with a machine-readable reason."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _validate_stem(stem: str) -> None:
    if not isinstance(stem, str) or not _STEM_RE.fullmatch(stem):
        raise ValueError("expected timestamp stem YYYYMMDD_HHMMSS_mmm")
    datetime.strptime(stem, "%Y%m%d_%H%M%S_%f")


def parse_capture_filename(path: str | Path) -> FrameRef:
    """Parse an original frame, not an aligned image or composite.

    Parsing validates metadata only; it does not decode image pixels.
    """
    path = Path(path)
    match = _FRAME_RE.fullmatch(path.name)
    if not match:
        raise ValueError(f"invalid capture filename: {path.name!r}")
    _validate_stem(match["stem"])
    wavelength = int(match["wavelength"])
    if not 700 <= wavelength <= 900:
        raise ValueError("capture wavelength must be in 700-900 nm")
    return FrameRef(path, match["stem"], int(match["port"]), wavelength)


def select_stored_triplet(directory: str | Path, stem: str) -> list[FrameRef]:
    """Select exactly one original 750/770/780 event without changing files.

    Extra candidates (including another extension for the same frame) are
    errors. Never silently overwrite duplicates or mix timestamp stems.
    Derived alignment/composite artifacts are not acquisition candidates.
    """
    try:
        _validate_stem(stem)
    except ValueError as exc:
        raise TripletSelectionError("INVALID_STEM", str(exc)) from exc
    directory = Path(directory).expanduser().resolve()
    frames = []
    for path in sorted(directory.iterdir()):
        if not path.name.startswith(stem + "_cam"):
            continue
        if path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".tif", ".tiff"):
            continue
        if path.stem.lower().endswith("_aligned"):
            continue
        if not path.is_file() or path.resolve().parent != directory:
            raise TripletSelectionError("INVALID_FRAME", f"not a local frame: {path.name}")
        try:
            frames.append(parse_capture_filename(path))
        except ValueError as exc:
            raise TripletSelectionError("INVALID_FRAME", str(exc)) from exc
    ports = [f.port for f in frames]
    waves = [f.wavelength_nm for f in frames]
    if len(set(ports)) != len(ports) or len(set(waves)) != len(waves):
        raise TripletSelectionError("AMBIGUOUS_TRIPLET", "duplicate camera or wavelength")
    if len(frames) != 3 or set(waves) != {750, 770, 780}:
        raise TripletSelectionError("INCOMPLETE_TRIPLET", "need exactly one 750/770/780 nm triplet")
    return sorted(frames, key=lambda frame: frame.port)


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:-3]


class ImageStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def burst_path_fn(self, ext: str = "jpg", wavelength_for=None) -> Callable:
        """Return a `(channel) -> Path` callable that gives each channel of a
        single burst a shared timestamp prefix and a unique cam/wavelength
        suffix.

        `wavelength_for(port)` comes from settings, which is the record of
        which filter is physically on which camera. The filename is the only
        place that mapping is carried once the data leaves the payload, so it
        is read live rather than taken from a compiled-in default.
        """
        ts = _utc_stamp()

        def make(channel) -> Path:
            nm = wavelength_for(channel.port) if wavelength_for else channel.wavelength_nm
            name = f"{ts}_cam{channel.port}_{nm}nm.{ext}"
            return self.root / name

        return make

    def list(self) -> list[str]:
        return sorted(
            (p.name for p in self.root.iterdir() if p.is_file()),
            reverse=True,
        )

    def path_for(self, image_id: str) -> Path:
        if not _ID_RE.match(image_id):
            raise ValueError(f"invalid image id: {image_id!r}")
        candidate = (self.root / image_id).resolve()
        if self.root.resolve() != candidate.parent:
            raise ValueError("image id escapes store root")
        if not candidate.is_file():
            raise FileNotFoundError(image_id)
        return candidate

    def delete(self, image_id: str) -> None:
        self.path_for(image_id).unlink()

    def clear(self) -> int:
        n = 0
        for p in self.root.iterdir():
            if p.is_file():
                p.unlink()
                n += 1
        return n

    def disk_usage(self) -> dict:
        import shutil
        u = shutil.disk_usage(self.root)
        return {"total": u.total, "used": u.used, "free": u.free}
