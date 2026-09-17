"""Camera settings store.

JSON file at $PAYLOAD_SETTINGS (default ~/.payload/settings.json).
Curated subset of picamera2 controls — the eight the operator actually
tunes — plus a shared/per-camera advanced-mode switch.

Shared mode (default): all three cameras get the same controls. The
K-index only makes sense when the channels are
radiometrically comparable, so this is the default.

Advanced mode: per-camera overrides become live. The UI shows a red
"per-camera settings active — ratio measurement invalid" banner the
whole time advanced mode is on. Use for diagnostics, not for science
runs.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any
import copy
import json
import threading


# Curated picamera2 controls and their valid ranges.
# (Internal default, accepted-type, validator-or-None). Validator is a
# callable that raises ValueError on bad input.
def _in_range(lo, hi):
    def check(v):
        if not (lo <= v <= hi):
            raise ValueError(f"must be between {lo} and {hi}")
    return check


CONTROL_SCHEMA: dict[str, dict] = {
    # microseconds. 0 means "leave the sensor's current value" (don't drive AE
    # logic), but in practice we use AeEnable=False + a real value.
    "ExposureTime":  {"type": int,   "default": 5000,  "validate": _in_range(1, 1_000_000_000)},
    "AnalogueGain":  {"type": float, "default": 1.0,   "validate": _in_range(1.0, 16.0)},
    "AeEnable":      {"type": bool,  "default": False, "validate": None},
    "AwbEnable":     {"type": bool,  "default": False, "validate": None},
    "Brightness":    {"type": float, "default": 0.0,   "validate": _in_range(-1.0, 1.0)},
    "Contrast":      {"type": float, "default": 1.0,   "validate": _in_range(0.0, 32.0)},
    "Sharpness":     {"type": float, "default": 1.0,   "validate": _in_range(0.0, 16.0)},
    "Saturation":    {"type": float, "default": 0.0,   "validate": _in_range(0.0, 32.0)},
}

# IMX477 still-config sizes we expose. (Width, height) → label.
# Fallback list, used only until the daemon reports what the cameras
# actually offer (see set_supported_resolutions). These are the IMX477
# modes; an IMX296 has exactly one mode, 1456x1088, and none of these are
# valid for it. Hardcoding this list rejected the IMX296's only resolution
# with HTTP 400 and made the sensor unusable through the UI.
RESOLUTIONS: list[tuple[int, int]] = [
    (4056, 3040),   # full sensor
    (2028, 1520),   # 2x2 binned, full FOV, faster, better SNR — default
    (1332, 990),    # crop, fastest, NARROWER field of view
]

# Replaced at daemon startup with the modes libcamera reports for the
# cameras actually fitted. Module-level so _coerce_resolution can see it
# without threading a reference through every call site.
_supported_resolutions: list[tuple[int, int]] = list(RESOLUTIONS)


def set_supported_resolutions(sizes) -> None:
    """Tell the settings layer what the fitted cameras actually support.

    Called by the daemon once the cameras are open. Keeps one settings
    schema working across sensors instead of baking in one sensor's modes.
    """
    global _supported_resolutions
    cleaned = [(int(w), int(h)) for w, h in sizes if int(w) > 0 and int(h) > 0]
    if cleaned:
        _supported_resolutions = cleaned


def supported_resolutions() -> list[tuple[int, int]]:
    return list(_supported_resolutions)

# 2028x1520 rather than the full 4056x3040. Two reasons, both from TL-002
# (see docs/flight_findings.md):
#
#   Speed — it more than halves the three-channel cycle, 1.18 s -> 0.52 s
#   measured on the rig. That cycle time is the dominant cause of channel
#   misregistration, because the aircraft moves between exposures.
#
#   SNR — this is the sensor's native 2x2 binned mode, so four photosites
#   are summed per output pixel. Once the 10 nm narrowband filters cut
#   ~98% of the light, signal-to-noise is the scarce resource, not pixels.
#
# It keeps the full field of view (1332x990 does not) and is still 3.1 MP.
DEFAULT_RESOLUTION = (2028, 1520)


BURST_COUNT_MAX = 200            # arbitrary sanity cap
TIMER_INTERVAL_MIN = 5           # seconds; below this and bursts will overlap
TIMER_INTERVAL_MAX = 24 * 3600   # one day

# Per-camera rotation in degrees. 0 or 180 only — picamera2 does these in
# hardware via Transform(hflip/vflip). 90/270 would need CPU post-rotation.
ROTATIONS_ALLOWED: tuple[int, ...] = (0, 180)


def _default_settings() -> dict:
    return {
        "shared": {name: spec["default"] for name, spec in CONTROL_SCHEMA.items()},
        "resolution": list(DEFAULT_RESOLUTION),
        "advanced_mode": False,
        "per_camera": {"0": {}, "1": {}, "2": {}},
        "rotations": {"0": 0, "1": 0, "2": 0},
        # Which filter is physically bolted to which camera. This is the
        # record of a hardware fact, and every capture filename is derived
        # from it, so it must be set to match reality and verified - see
        # docs/filter_assignment.md. Defaults to the long-range set.
        "wavelengths": {"0": 750, "1": 770, "2": 780},
        "burst_count": 1,
        "timer": {"enabled": False, "interval_seconds": 60},
    }


def _coerce_and_validate_controls(raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce each input value to its declared type and run the validator.

    Unknown keys are dropped silently — easier than 422'ing on a stale UI
    sending a field we no longer expose.
    """
    out: dict[str, Any] = {}
    for name, value in raw.items():
        spec = CONTROL_SCHEMA.get(name)
        if not spec:
            continue
        try:
            if spec["type"] is bool:
                # Accept bool, "true"/"false" strings, 0/1, etc.
                if isinstance(value, str):
                    coerced = value.strip().lower() in ("1", "true", "yes", "on")
                else:
                    coerced = bool(value)
            else:
                coerced = spec["type"](value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name}: cannot coerce {value!r} to {spec['type'].__name__}") from exc
        if spec["validate"]:
            spec["validate"](coerced)
        out[name] = coerced
    return out


def _coerce_burst_count(value) -> int:
    n = int(value)
    if not (1 <= n <= BURST_COUNT_MAX):
        raise ValueError(f"burst_count must be between 1 and {BURST_COUNT_MAX}")
    return n


def _coerce_rotation(value) -> int:
    n = int(value)
    if n not in ROTATIONS_ALLOWED:
        raise ValueError(f"rotation must be one of {ROTATIONS_ALLOWED}")
    return n


def _coerce_wavelengths(value) -> dict[str, int]:
    """Port -> filter centre wavelength in nm.

    Deliberately permissive about which wavelength sits on which port: the
    filters are bolted on by hand and either of the two standard sets may be
    fitted, so the software records what the operator says is there rather
    than enforcing a particular arrangement. It does reject values outside
    the near-infrared band the payload works in, which catches a typo like
    77 or 7700 before it reaches a filename.
    """
    if not isinstance(value, dict):
        raise ValueError("wavelengths must be an object of port -> nm")
    out: dict[str, int] = {}
    for k, v in value.items():
        port = str(int(k))
        if port not in ("0", "1", "2"):
            raise ValueError(f"unknown camera port {k}")
        nm = int(v)
        if not (700 <= nm <= 900):
            raise ValueError(
                f"wavelength {nm} nm for cam{port} is outside 700-900 nm; "
                f"the standard sets are 750/770/780 and 760/770/780")
        out[port] = nm
    if len(out) == 3 and len(set(out.values())) != 3:
        raise ValueError(
            f"two cameras cannot carry the same filter: {out}")
    return out


def _coerce_rotations(value: dict) -> dict[str, int]:
    out = {"0": 0, "1": 0, "2": 0}
    if not isinstance(value, dict):
        raise ValueError("rotations must be a dict keyed by port string")
    for k, v in value.items():
        if k in out:
            out[k] = _coerce_rotation(v)
    return out


def _coerce_timer(value: dict) -> dict:
    out = {"enabled": False, "interval_seconds": 60}
    if "enabled" in value:
        v = value["enabled"]
        if isinstance(v, str):
            out["enabled"] = v.strip().lower() in ("1", "true", "yes", "on")
        else:
            out["enabled"] = bool(v)
    if "interval_seconds" in value:
        secs = int(value["interval_seconds"])
        if not (TIMER_INTERVAL_MIN <= secs <= TIMER_INTERVAL_MAX):
            raise ValueError(
                f"timer.interval_seconds must be between {TIMER_INTERVAL_MIN} and {TIMER_INTERVAL_MAX}"
            )
        out["interval_seconds"] = secs
    return out


def _coerce_resolution(value) -> list[int]:
    """Accept [w, h], 'WxH', or 'W,H'. Must match a supported resolution."""
    if isinstance(value, str):
        sep = "x" if "x" in value else ","
        parts = [p.strip() for p in value.split(sep)]
    else:
        parts = list(value)
    if len(parts) != 2:
        raise ValueError("resolution must be [width, height]")
    w, h = int(parts[0]), int(parts[1])
    allowed = supported_resolutions()
    if (w, h) not in allowed:
        supported = ", ".join(f"{a}x{b}" for a, b in allowed)
        raise ValueError(f"resolution {w}x{h} not supported (try {supported})")
    return [w, h]


class SettingsStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._data = self._load_or_default()

    def reload(self) -> None:
        """Re-read the settings file from disk.

        Needed because the store is constructed before the cameras are open,
        so a stored resolution is first validated against the fallback list
        rather than the fitted sensor's real modes. A valid stored value —
        1456x1088 on an IMX296 — was therefore rejected at load and silently
        replaced by the default, leaving the in-memory settings disagreeing
        with the file on disk. The daemon calls this once the sensor modes
        are known.
        """
        with self._lock:
            self._data = self._load_or_default()

    def _load_or_default(self) -> dict:
        if self.path.exists():
            try:
                stored = json.loads(self.path.read_text())
            except (OSError, json.JSONDecodeError):
                stored = {}
        else:
            stored = {}
        # Merge stored over defaults so a partial / outdated file still works.
        merged = _default_settings()
        if "shared" in stored:
            merged["shared"].update(_coerce_and_validate_controls(stored["shared"]))
        if "resolution" in stored:
            try:
                merged["resolution"] = _coerce_resolution(stored["resolution"])
            except ValueError:
                pass
        if "advanced_mode" in stored:
            merged["advanced_mode"] = bool(stored["advanced_mode"])
        if "per_camera" in stored and isinstance(stored["per_camera"], dict):
            for k, v in stored["per_camera"].items():
                if k in merged["per_camera"] and isinstance(v, dict):
                    try:
                        merged["per_camera"][k] = _coerce_and_validate_controls(v)
                    except ValueError:
                        pass
        if "burst_count" in stored:
            try:
                merged["burst_count"] = _coerce_burst_count(stored["burst_count"])
            except ValueError:
                pass
        if "timer" in stored and isinstance(stored["timer"], dict):
            try:
                merged["timer"] = _coerce_timer(stored["timer"])
            except ValueError:
                pass
        if "rotations" in stored and isinstance(stored["rotations"], dict):
            try:
                merged["rotations"] = _coerce_rotations(stored["rotations"])
            except ValueError:
                pass
        if "wavelengths" in stored and isinstance(stored["wavelengths"], dict):
            try:
                merged["wavelengths"] = _coerce_wavelengths(stored["wavelengths"])
            except ValueError:
                pass
        return merged

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def update(self, patch: dict) -> dict:
        """Apply a partial update. Raises ValueError on validation failure."""
        with self._lock:
            new = copy.deepcopy(self._data)
            if "shared" in patch:
                new["shared"].update(_coerce_and_validate_controls(patch["shared"]))
            if "resolution" in patch:
                new["resolution"] = _coerce_resolution(patch["resolution"])
            if "advanced_mode" in patch:
                new["advanced_mode"] = bool(patch["advanced_mode"])
            if "per_camera" in patch and isinstance(patch["per_camera"], dict):
                for k, v in patch["per_camera"].items():
                    if k in new["per_camera"] and isinstance(v, dict):
                        new["per_camera"][k] = _coerce_and_validate_controls(v)
            if "burst_count" in patch:
                new["burst_count"] = _coerce_burst_count(patch["burst_count"])
            if "timer" in patch and isinstance(patch["timer"], dict):
                new["timer"] = _coerce_timer({**new["timer"], **patch["timer"]})
            if "rotations" in patch and isinstance(patch["rotations"], dict):
                merged_rot = {**new["rotations"], **{
                    k: v for k, v in patch["rotations"].items() if k in new["rotations"]
                }}
                new["rotations"] = _coerce_rotations(merged_rot)
            if "wavelengths" in patch and isinstance(patch["wavelengths"], dict):
                merged_wl = {**new["wavelengths"], **{
                    k: v for k, v in patch["wavelengths"].items()
                    if k in new["wavelengths"]
                }}
                new["wavelengths"] = _coerce_wavelengths(merged_wl)
            self._data = new
            self._save()
            return copy.deepcopy(self._data)

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2))
        tmp.replace(self.path)

    def controls_for(self, port: int) -> dict[str, Any]:
        """Resolved picamera2 controls dict for one channel."""
        with self._lock:
            merged = dict(self._data["shared"])
            if self._data["advanced_mode"]:
                merged.update(self._data["per_camera"].get(str(port), {}))
            return merged

    def resolution(self) -> tuple[int, int]:
        with self._lock:
            w, h = self._data["resolution"]
            return int(w), int(h)

    def burst_count(self) -> int:
        with self._lock:
            return int(self._data["burst_count"])

    def timer(self) -> dict:
        with self._lock:
            return dict(self._data["timer"])

    def rotation_for(self, port: int) -> int:
        with self._lock:
            return int(self._data["rotations"].get(str(port), 0))

    def wavelength_for(self, port: int) -> int:
        """Centre wavelength of the filter fitted to this camera, in nm.

        Every capture filename is built from this, so it is the record of
        which physical filter is on which physical camera.
        """
        with self._lock:
            return int(self._data["wavelengths"].get(str(port), 0))

    def wavelengths(self) -> dict[str, int]:
        with self._lock:
            return dict(self._data["wavelengths"])
