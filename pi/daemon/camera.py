"""Three-camera capture through the Arducam v2.2 mux.

Phase 3: each click captures one frame from each channel, sequentially.
Phase 4a: per-burst settings via picamera2.set_controls(); resolution
  changes trigger reconfigure.
Phase 4b: capture_bursts(n) runs N consecutive 3-channel bursts under a
  single lock; non-blocking acquire raises BusyError so manual + timer
  paths can't stack.

The kernel's video-mux + pca954x drivers handle the physical port
switching when we start a Picamera2 instance on a specific camera_num —
we just create three instances at boot and start/stop one at a time.
"""
from __future__ import annotations
from pathlib import Path
from typing import Callable, NamedTuple, Optional
import threading


class BusyError(RuntimeError):
    """Raised when a capture is requested while one is already in progress."""


class Channel(NamedTuple):
    port: int
    wavelength_nm: int


# Port → intended filter wavelength. Without filters bolted on, all three
# channels see the same scene; the labels are aspirational. Move this into
# settings.json if/when filter mapping ever needs to vary per deployment.
CHANNELS: list[Channel] = [
    Channel(port=0, wavelength_nm=762),
    Channel(port=1, wavelength_nm=766),
    Channel(port=2, wavelength_nm=770),
]


class CaptureResult(NamedTuple):
    port: int
    wavelength_nm: int
    path: Path


ControlsFn = Callable[[int], dict]


class Cameras:
    """Owns all three Picamera2 handles. Sequential capture across channels.

    The CSI controller is shared across mux ports, so only one camera can
    be streaming at a time. Settings are applied per-burst (per-channel
    where advanced mode is on) — see pi/shared/settings.py.
    """

    def __init__(self, default_resolution: tuple[int, int] = (4056, 3040)):
        from picamera2 import Picamera2  # lazy: dev hosts without picamera2 can still import
        self._Picamera2 = Picamera2
        self._picams: dict[int, "Picamera2"] = {}
        self._configured_size: dict[int, tuple[int, int]] = {}
        for ch in CHANNELS:
            p = Picamera2(camera_num=ch.port)
            self._configure(p, default_resolution)
            self._picams[ch.port] = p
            self._configured_size[ch.port] = default_resolution
        self._lock = threading.Lock()

    def _configure(self, p, size: tuple[int, int]) -> None:
        cfg = p.create_still_configuration(main={"size": size})
        p.configure(cfg)

    def _single_burst(
        self,
        path_for: Callable[[Channel], Path],
        controls_for: Optional[ControlsFn],
        resolution: Optional[tuple[int, int]],
    ) -> list[CaptureResult]:
        results: list[CaptureResult] = []
        for ch in CHANNELS:
            p = self._picams[ch.port]
            if resolution is not None and self._configured_size[ch.port] != resolution:
                self._configure(p, resolution)
                self._configured_size[ch.port] = resolution
            if controls_for is not None:
                p.set_controls(controls_for(ch.port))
            p.start()
            try:
                path = path_for(ch)
                p.capture_file(str(path))
            finally:
                p.stop()
            results.append(CaptureResult(ch.port, ch.wavelength_nm, path))
        return results

    def capture_bursts(
        self,
        n: int,
        path_for: Callable[[Channel], Path],
        controls_for: Optional[ControlsFn] = None,
        resolution: Optional[tuple[int, int]] = None,
    ) -> list[CaptureResult]:
        """Run N consecutive 3-channel bursts. Raises BusyError if another
        capture is already running on this Cameras instance."""
        if n < 1:
            raise ValueError("n must be >= 1")
        if not self._lock.acquire(blocking=False):
            raise BusyError("capture already in progress")
        try:
            all_results: list[CaptureResult] = []
            for _ in range(n):
                all_results.extend(self._single_burst(path_for, controls_for, resolution))
            return all_results
        finally:
            self._lock.release()

    # Phase 3 compatibility shim. Equivalent to capture_bursts(1, ...).
    def capture_burst(self, *args, **kwargs) -> list[CaptureResult]:
        return self.capture_bursts(1, *args, **kwargs)

    def close(self) -> None:
        with self._lock:
            for p in self._picams.values():
                try:
                    p.close()
                except Exception:
                    pass
