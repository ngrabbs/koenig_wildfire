"""Stored-triplet correction and measurements; no validated fire decision.

PAYLOAD_CALIBRATION_PATH is explicit. Metadata compatibility is reported as
unverified: matching ports and dimensions cannot prove unchanged optics.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, Sequence

from .store import FrameRef

CALIBRATION_SHAPE = (1088, 1456)
CALIBRATION_WAVELENGTHS = {0: 770, 1: 750, 2: 780}
EDGE_EXCLUSION_FRACTION = 0.10


@dataclass(frozen=True)
class CalibrationConfig:
    correction_path: str | Path | None = None


@dataclass(frozen=True)
class ProcessingResult:
    code: str
    reason: str
    calibration_path: str | None = None
    processing_status: str = "WAITING_FOR_CALIBRATION"
    provenance: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    registration: dict | None = None
    decision: Literal["NOT_CALIBRATED"] = field(default="NOT_CALIBRATED", init=False)


class CalibrationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_calibration(raw: bytes) -> dict:
    """Validate independently of the hash, without enabling pickle."""
    import numpy as np

    required = {"ports", "has_dark", *(f"{kind}_{p}" for kind in ("gain", "dark") for p in range(3))}
    try:
        with np.load(io.BytesIO(raw), allow_pickle=False) as archive:
            missing = required - set(archive.files)
            if missing:
                raise CalibrationError("CALIBRATION_MISSING_KEYS", f"Missing keys: {sorted(missing)}")
            arrays = {key: archive[key] for key in archive.files}
    except CalibrationError:
        raise
    except Exception as exc:
        raise CalibrationError("CALIBRATION_UNREADABLE", f"Cannot safely load archive: {exc}") from exc
    ports = arrays["ports"]
    if ports.shape != (3,) or ports.dtype.kind not in "iu" or set(ports.tolist()) != {0, 1, 2}:
        raise CalibrationError("CALIBRATION_PORTS_INVALID", "Expected exactly ports 0, 1, 2")
    dark = arrays["has_dark"]
    if dark.shape != (3,) or dark.dtype.kind != "b" or not dark.all():
        raise CalibrationError("CALIBRATION_DARK_MISSING", "Measured dark data required for all three ports")
    for key, array in arrays.items():
        if array.dtype.kind not in "biuf":
            raise CalibrationError("CALIBRATION_DTYPE_INVALID", f"{key}: expected real numeric/bool data")
        if not np.isfinite(array).all():
            raise CalibrationError("CALIBRATION_NONFINITE", f"{key}: non-finite values")
    for port in range(3):
        for kind in ("gain", "dark"):
            key = f"{kind}_{port}"
            if arrays[key].shape != CALIBRATION_SHAPE:
                raise CalibrationError("CALIBRATION_SHAPE_INVALID", f"{key}: expected {CALIBRATION_SHAPE}")
            if arrays[key].dtype.kind not in "iuf":
                raise CalibrationError("CALIBRATION_DTYPE_INVALID", f"{key}: expected real numeric map")
        if not (arrays[f"gain_{port}"] > 0).all():
            raise CalibrationError("CALIBRATION_GAIN_INVALID", f"gain_{port}: gains must be strictly positive")
    return {"sha256": hashlib.sha256(raw).hexdigest(), "shape": list(CALIBRATION_SHAPE),
            "ports": ports.tolist(), "has_dark": dark.tolist(), "validation": "STRUCTURALLY_VALID"}


def calibrated_region_mask(shape: tuple[int, int]):
    """Exclude ceil(10%) of every edge in original sensor coordinates."""
    import math
    import numpy as np

    h, w = shape
    y, x = math.ceil(h * EDGE_EXCLUSION_FRACTION), math.ceil(w * EDGE_EXCLUSION_FRACTION)
    mask = np.zeros((h, w), dtype=bool)
    mask[y:h-y, x:w-x] = True
    return mask, {"edge_fraction": EDGE_EXCLUSION_FRACTION,
                  "bounds_xyxy": [x, y, w-x, h-y], "rounding": "ceil", "pixels": int(mask.sum())}


class ProcessingService:
    def __init__(self, calibration: CalibrationConfig | None = None) -> None:
        self.calibration = calibration if calibration is not None else CalibrationConfig()

    def process(self, frames: Sequence[FrameRef], *, source: str = "simulation") -> ProcessingResult:
        provenance = {"source": source, "input_files": [str(f.path) for f in frames],
                      "compatibility": "ACQUISITION_METADATA_UNVERIFIED",
                      "unverified": ["exposure", "gain", "focus", "orientation", "physical_optical_configuration"],
                      "detection_threshold": "NOT_VALIDATED"}

        def result(code, reason, status="WAITING_FOR_CALIBRATION", path=None, **extra):
            return ProcessingResult(code, reason, str(path) if path is not None else None,
                                    status, provenance, **extra)

        if (len(frames) != 3 or len({f.stem for f in frames}) != 1
                or len({f.port for f in frames}) != 3
                or {f.port: f.wavelength_nm for f in frames} != CALIBRATION_WAVELENGTHS):
            return result("INCOMPATIBLE_TRIPLET", "Need one event with ports 0=770, 1=750, 2=780 nm", "PROCESSING_FAILED")
        if source != "simulation":
            return result("INCOMPATIBLE_SOURCE", "Only stored original-camera triplets are eligible", "SKIPPED_INCOMPATIBLE_SOURCE")
        raw = self.calibration.correction_path
        if raw is None:
            return result("CALIBRATION_NOT_CONFIGURED", "PAYLOAD_CALIBRATION_PATH is unset")
        if not isinstance(raw, (str, Path)) or not str(raw).strip() or "\x00" in str(raw):
            return result("INVALID_CALIBRATION_CONFIG", "Expected an absolute .npz path")
        path = None
        try:
            path = Path(raw).expanduser()
            if not path.is_absolute() or path.suffix.lower() != ".npz":
                return result("INVALID_CALIBRATION_CONFIG", "Expected an absolute .npz path")
            if not path.is_file():
                return result("CALIBRATION_PATH_UNAVAILABLE", "Calibration file is missing or not a file", path=path)
            archive_bytes = path.read_bytes()
        except (OSError, RuntimeError, ValueError) as exc:
            return result("CALIBRATION_PATH_UNAVAILABLE", str(exc), path=path)
        provenance["calibration_sha256"] = hashlib.sha256(archive_bytes).hexdigest()
        try:
            provenance["calibration"] = validate_calibration(archive_bytes)
        except CalibrationError as exc:
            return result(exc.code, str(exc), path=path)
        except ImportError as exc:
            return result("PROCESSING_DEPENDENCY_UNAVAILABLE", str(exc), "PROCESSING_FAILED", path)
        try:
            import cv2
            import numpy as np
            from tools import flat_field, register_triplets, k_index
        except (ImportError, SystemExit) as exc:
            return result("PROCESSING_DEPENDENCY_UNAVAILABLE", str(exc), "PROCESSING_FAILED", path)

        stage, registration = "INPUT", None
        try:
            with TemporaryDirectory(prefix="payload-processing-") as work:
                work = Path(work)
                inputs, corrected, aligned = (work / name for name in ("raw", "corrected", "aligned"))
                for directory in (inputs, corrected, aligned):
                    directory.mkdir()
                # Use exactly the bytes just validated, even if the source changes.
                correction = work / "calibration.npz"
                correction.write_bytes(archive_bytes)
                base_mask, provenance["mask"] = calibrated_region_mask(CALIBRATION_SHAPE)
                provenance["port_wavelengths"] = dict(CALIBRATION_WAVELENGTHS)
                masks = {}
                provenance["input_sha256"] = {}
                for frame in frames:
                    content = frame.path.read_bytes()
                    provenance["input_sha256"][frame.path.name] = hashlib.sha256(content).hexdigest()
                    dest = inputs / frame.path.name
                    dest.write_bytes(content)
                    image = cv2.imread(str(dest), cv2.IMREAD_UNCHANGED)
                    if image is None:
                        return result("INPUT_UNREADABLE", frame.path.name, "PROCESSING_FAILED", path)
                    if image.shape[:2] != CALIBRATION_SHAPE:
                        return result("INPUT_SHAPE_INCOMPATIBLE", f"{frame.path.name}: {image.shape}", "PROCESSING_FAILED", path)
                    if image.dtype != np.uint8 or image.ndim not in (2, 3):
                        return result("INPUT_ENCODING_UNSUPPORTED", "Expected original uint8 image, not corrected data", "PROCESSING_FAILED", path)
                    peak = image.max(axis=2) if image.ndim == 3 else image
                    masks[frame.port] = base_mask & (peak < 254)
                stage = "FLAT_FIELD"
                if flat_field.apply(inputs, correction, corrected) != 0:
                    raise RuntimeError("Flat-field tool returned failure")
                paths = {f.port: corrected / (f.path.stem + ".png") for f in frames}
                for port, output in paths.items():
                    image = cv2.imread(str(output), cv2.IMREAD_UNCHANGED) if output.is_file() else None
                    if image is None or image.shape != CALIBRATION_SHAPE or image.dtype != np.uint16:
                        raise RuntimeError(f"Missing or invalid corrected output for cam{port}")
                    masks[port] &= image < 255 * flat_field.OUTPUT_SCALE
                stage = "REGISTRATION"
                reg = register_triplets.process_triplet(frames[0].stem, paths, CALIBRATION_WAVELENGTHS,
                                                       aligned, reference=0, write_composite=False, crop=False)
                registration = asdict(reg)
                if reg.status != "ok" or len(reg.shifts) != 3 or {s.port for s in reg.shifts} != {0, 1, 2}:
                    raise RuntimeError(f"Unreliable registration: {reg.status}: {reg.note}")
                common = np.ones(CALIBRATION_SHAPE, dtype=bool)
                for shift in reg.shifts:
                    if not shift.reliable or not np.isfinite([shift.dx, shift.dy, shift.ncc]).all():
                        raise RuntimeError("Invalid registration transform/quality")
                    warped = register_triplets.translate(masks[shift.port].astype(np.float32), shift.dx, shift.dy)
                    # Reported shifts round to .01 px. Erode one pixel to cover
                    # that rounding and interpolation support conservatively.
                    safe = cv2.erode((warped >= 1.0).astype(np.uint8), np.ones((3, 3), np.uint8))
                    common &= safe.astype(bool)
                provenance["mask"].update(common_pixels=int(common.sum()), alignment_guard_pixels=1,
                                            saturation_excluded=True)
                if not common.any():
                    return result("NO_CALIBRATED_OVERLAP", "No common calibrated pixels", "PROCESSING_FAILED", path, registration=registration)
                signals = {}
                for f in frames:
                    output = aligned / f"{f.stem}_cam{f.port}_{f.wavelength_nm}nm_aligned.png"
                    image = cv2.imread(str(output), cv2.IMREAD_UNCHANGED) if output.is_file() else None
                    if image is None or image.shape != CALIBRATION_SHAPE or image.dtype != np.uint16:
                        raise RuntimeError(f"Missing or invalid aligned output for cam{f.port}")
                    signals[f.port] = k_index.read_channel(output)
                stage = "INDEX"
                cont = k_index.continuum(signals[1], signals[2], 750, 770, 780)
                delta, valid = k_index.k_index(signals[0], cont)
                valid &= common & np.isfinite(delta)
                count = int(valid.sum())
                if not count:
                    return result("NO_VALID_INDEX_PIXELS", "No finite index above the existing signal floor", "PROCESSING_FAILED", path, registration=registration)
                values = delta[valid]
                means = {p: float(s[valid].mean(dtype=np.float64)) for p, s in signals.items()}
                metrics = {"median_delta77": float(np.median(values)), "p99_delta77": float(np.percentile(values, 99)),
                           "fraction_above_0_05": float((values > .05).mean()), "valid_pixel_count": count,
                           "common_region_pixel_count": int(common.sum()), "valid_pixel_fraction": count / int(common.sum()),
                           "valid_full_frame_fraction": count / int(common.size),
                           "diagnostic_ratio_770_750": means[0] / means[1] if means[1] > 0 else None,
                           "diagnostic_ratio_770_780": means[0] / means[2] if means[2] > 0 else None}
                provenance["diagnostics"] = "0.05 is descriptive, not a fire threshold; ratios use means over identical valid pixels."
                provenance["outputs"] = "Corrected/aligned uint16 PNGs verified; temporary products removed after metrics."
                return result("ACQUISITION_METADATA_UNVERIFIED", "Measurements computed; acquisition metadata and detection threshold remain unverified",
                              "PROCESSED_WITH_LIMITATIONS", path, metrics=metrics, registration=registration)
        except (Exception, SystemExit) as exc:
            return result(f"{stage}_FAILED", str(exc), "PROCESSING_FAILED", path, registration=registration)
