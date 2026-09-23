"""Pick the camera backend that matches the hardware this is running on.

Two boards, two camera stacks, one daemon:

    camera.py         Raspberry Pi 4 + Arducam multiplexer + 3x IMX296.
                      picamera2/libcamera. Three narrowband channels.
    camera_jetson.py  Jetson Orin Nano + single IMX477.
                      OpenCV/GStreamer via nvarguscamerasrc. One channel.

Neither import survives on the other board - picamera2 is not installed on
the Jetson, and the Pi has neither OpenCV nor nvarguscamerasrc. So the import
cannot happen at module scope in main.py: whichever one is named there, the
daemon fails to start on the other machine with ModuleNotFoundError before
anything is logged. Hence a function, importing inside the branch it picks.

Autodetects by board model. Override when testing:

    PAYLOAD_CAMERA_BACKEND=pi
    PAYLOAD_CAMERA_BACKEND=jetson
    PAYLOAD_CAMERA_BACKEND=auto      (default)

The two backends present the same interface - available_sizes,
capture_bursts, focus_port, start_focus, iter_frames, stop_focus, close, and
BusyError - so main.py does not care which one it gets. That shared surface
is load-bearing: anything added to one backend has to appear in the other,
or the daemon breaks on that board only, and probably not until someone
touches that route in the field.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("payload.daemon")

MODEL_PATH = Path("/proc/device-tree/model")

# Substrings that identify a Jetson in /proc/device-tree/model. A Pi 4 reports
# "Raspberry Pi 4 Model B Rev 1.4"; an Orin Nano reports a string carrying
# "NVIDIA" and "Orin". Matching Jetson rather than Pi means an unrecognised
# board falls through to the Pi backend, which is the safe default here: this
# repo's flight hardware is the Pi, and a wrong guess there fails loudly at
# once rather than silently producing single-channel data.
JETSON_MARKERS = ("nvidia", "orin", "tegra", "jetson")


def detect_board() -> str:
    """Return "jetson" or "pi" from the device-tree model string."""
    try:
        model = MODEL_PATH.read_text(errors="ignore").strip("\x00").strip()
    except OSError:
        return "pi"
    low = model.lower()
    if any(m in low for m in JETSON_MARKERS):
        return "jetson"
    return "pi"


def load_backend(name: str | None = None):
    """Return (BusyError, Cameras) for the selected backend.

    `name` is "pi", "jetson" or "auto"; it defaults to
    $PAYLOAD_CAMERA_BACKEND, itself defaulting to "auto".
    """
    if name is None:
        name = os.environ.get("PAYLOAD_CAMERA_BACKEND", "auto")
    name = name.strip().lower()

    if name not in ("auto", "pi", "jetson"):
        raise ValueError(
            f"PAYLOAD_CAMERA_BACKEND={name!r} is not one of "
            "'auto', 'pi', 'jetson'")

    chosen = detect_board() if name == "auto" else name
    how = "detected" if name == "auto" else "forced by PAYLOAD_CAMERA_BACKEND"

    try:
        if chosen == "jetson":
            from .camera_jetson import BusyError, Cameras
        else:
            from .camera import BusyError, Cameras
    except ImportError as exc:
        # Worth spelling out: this is the failure that looks like a broken
        # payload but is really a backend on the wrong board.
        raise ImportError(
            f"the {chosen!r} camera backend ({how}) could not be imported: "
            f"{exc}. The Pi backend needs picamera2; the Jetson backend needs "
            f"OpenCV with GStreamer and nvarguscamerasrc. If this is the "
            f"wrong board for this backend, set PAYLOAD_CAMERA_BACKEND."
        ) from exc

    log.info("camera backend: %s (%s)", chosen, how)
    return BusyError, Cameras
