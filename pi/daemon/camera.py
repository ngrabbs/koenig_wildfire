"""Three-camera capture through the Arducam v2.2 mux.

Phase 3: each click captures one frame from each channel, sequentially.
Phase 4: per-burst settings are applied via picamera2.set_controls(),
and resolution changes trigger a reconfigure on the affected camera.

The kernel's video-mux + pca954x drivers handle the physical port
switching when we start a Picamera2 instance on a specific
camera_num — we just create three instances at boot and start/stop
one at a time.
"""
from __future__ import annotations
from pathlib import Path
from typing import Callable, NamedTuple, Optional
import threading


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

    def capture_burst(
        self,
        path_for: Callable[[Channel], Path],
        controls_for: Optional[ControlsFn] = None,
        resolution: Optional[tuple[int, int]] = None,
    ) -> list[CaptureResult]:
        """Capture once from each channel. Returns one CaptureResult per channel.

        controls_for(port) -> dict of picamera2 controls applied before that
        channel's capture. Pass None to leave controls at whatever was last set.

        resolution forces a reconfigure of any camera whose current size doesn't
        match (so the user can pick a smaller resolution to speed up bursts).
        """
        results: list[CaptureResult] = []
        with self._lock:
            for ch in CHANNELS:
                p = self._picams[ch.port]

                if resolution is not None and self._configured_size[ch.port] != resolution:
                    # reconfigure requires not-running
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

    def close(self) -> None:
        with self._lock:
            for p in self._picams.values():
                try:
                    p.close()
                except Exception:
                    pass
