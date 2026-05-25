"""Image directory: list, fetch path, delete, clear. Path-traversal safe.

Filenames are UTC-timestamp-prefixed so a three-channel burst sorts
together in alphabetical order, e.g.

    20260525_211238_123_cam0_762nm.jpg
    20260525_211238_123_cam1_766nm.jpg
    20260525_211238_123_cam2_770nm.jpg
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
import re

_ID_RE = re.compile(r"^[0-9A-Za-z_\-]+\.(jpg|jpeg|png)$")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:-3]


class ImageStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def burst_path_fn(self, ext: str = "jpg") -> Callable:
        """Return a `(channel) -> Path` callable that gives each channel of a
        single burst a shared timestamp prefix and a unique cam/wavelength
        suffix."""
        ts = _utc_stamp()

        def make(channel) -> Path:
            name = f"{ts}_cam{channel.port}_{channel.wavelength_nm}nm.{ext}"
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
