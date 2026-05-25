"""Camera settings store.

JSON file at $KOENIG_SETTINGS (default ~/.koenig/settings.json).
Curated subset of picamera2 controls — the eight the operator actually
tunes — plus a shared/per-camera advanced-mode switch.

Shared mode (default): all three cameras get the same controls. The
ratio math (S766+S770)/(2·S762) only makes sense when channels are
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
RESOLUTIONS: list[tuple[int, int]] = [
    (4056, 3040),   # full sensor
    (2028, 1520),   # 2x2 binned, faster, smaller
    (1332, 990),    # crop, fastest
]

DEFAULT_RESOLUTION = (4056, 3040)


def _default_settings() -> dict:
    return {
        "shared": {name: spec["default"] for name, spec in CONTROL_SCHEMA.items()},
        "resolution": list(DEFAULT_RESOLUTION),
        "advanced_mode": False,
        "per_camera": {"0": {}, "1": {}, "2": {}},
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
    if (w, h) not in RESOLUTIONS:
        supported = ", ".join(f"{a}x{b}" for a, b in RESOLUTIONS)
        raise ValueError(f"resolution {w}x{h} not supported (try {supported})")
    return [w, h]


class SettingsStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
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
