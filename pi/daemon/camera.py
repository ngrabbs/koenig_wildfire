"""Picamera2 wrapper.

Phase 2 spike: single camera, hardcoded defaults, JPEG output. Phase 3
adds the mux selector and per-channel addressing; Phase 4 makes the
controls operator-tunable.
"""
from __future__ import annotations
from pathlib import Path
import threading


class Camera:
    def __init__(self):
        from picamera2 import Picamera2  # imported lazily so dev machines without it can import this module
        self._picam = Picamera2()
        config = self._picam.create_still_configuration()
        self._picam.configure(config)
        self._picam.start()
        self._lock = threading.Lock()

    def capture(self, path: Path) -> None:
        with self._lock:
            self._picam.capture_file(str(path))

    def close(self) -> None:
        with self._lock:
            self._picam.stop()
            self._picam.close()
