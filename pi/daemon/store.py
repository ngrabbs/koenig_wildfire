"""Image directory: list, fetch path, delete, clear. Path-traversal safe."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import re

_ID_RE = re.compile(r"^[0-9A-Za-z_\-]+\.(jpg|jpeg|png)$")


class ImageStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def next_path(self, ext: str = "jpg") -> Path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:-3]
        return self.root / f"{ts}.{ext}"

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
