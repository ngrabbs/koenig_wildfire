"""Three-camera capture through the Arducam v2.2 mux.

Phase 3: each click captures one frame from each of the three channels,
sequentially. The kernel's video-mux + pca954x drivers handle the
physical switching when we start a Picamera2 instance on a specific
camera_num; we just create three instances at boot, keep them
configured, and start/capture/stop one at a time.

Settings are hardcoded library defaults at this point; Phase 4 makes
them tunable through the UI with a shared-by-default policy.
"""
from __future__ import annotations
from pathlib import Path
from typing import Callable, NamedTuple
import threading


class Channel(NamedTuple):
    port: int
    wavelength_nm: int


# Mapping from physical mux port to intended filter wavelength.
# Cam0 = off-line reference (762 nm). Cam1 + Cam2 = on-line (766, 770 nm).
# Without filters bolted on, all three channels see the same scene —
# the labels are aspirational until the filter housings arrive.
CHANNELS: list[Channel] = [
    Channel(port=0, wavelength_nm=762),
    Channel(port=1, wavelength_nm=766),
    Channel(port=2, wavelength_nm=770),
]


class CaptureResult(NamedTuple):
    port: int
    wavelength_nm: int
    path: Path


class Cameras:
    """Owns all three Picamera2 handles. Sequential capture across channels.

    The CSI controller is shared across mux ports, so only one camera can
    be streaming at a time. We don't try to parallelise — the architecture
    doc documents the simultaneity tradeoff this implies.
    """

    def __init__(self):
        from picamera2 import Picamera2  # lazy: lets the module import on dev hosts without picamera2
        self._picams: dict[int, "Picamera2"] = {}
        for ch in CHANNELS:
            p = Picamera2(camera_num=ch.port)
            p.configure(p.create_still_configuration())
            self._picams[ch.port] = p
        self._lock = threading.Lock()

    def capture_burst(self, path_for: Callable[[Channel], Path]) -> list[CaptureResult]:
        """Capture once from each channel.

        `path_for(channel)` returns the file path each channel should write
        to. All three writes happen under a single lock so concurrent /capture
        calls are serialised.
        """
        results: list[CaptureResult] = []
        with self._lock:
            for ch in CHANNELS:
                path = path_for(ch)
                p = self._picams[ch.port]
                p.start()
                try:
                    p.capture_file(str(path))
                finally:
                    p.stop()
                results.append(CaptureResult(ch.port, ch.wavelength_nm, path))
        return results

    def close(self) -> None:
        with self._lock:
            for p in self._picams.values():
                try:
                    p.close()
                except Exception:
                    pass
