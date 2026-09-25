"""Phase 2 calibration gate. No science tools are imported or executed.

Configure PAYLOAD_CALIBRATION_PATH with an absolute path to an existing
flat/dark correction archive (.npz), or leave it unset. There is no default
archive and nothing is generated. File existence alone cannot establish
valid calibration: archive contents, measured dark provenance, channel
mapping and acquisition compatibility still require a future validator.
Until that validator exists even a present archive keeps the gate closed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Sequence

from .store import FrameRef


@dataclass(frozen=True)
class CalibrationConfig:
    correction_path: str | Path | None = None


@dataclass(frozen=True)
class ProcessingResult:
    code: str
    reason: str
    calibration_path: str | None = None
    processing_status: Literal["WAITING_FOR_CALIBRATION"] = field(
        default="WAITING_FOR_CALIBRATION", init=False)
    decision: Literal["NOT_CALIBRATED"] = field(default="NOT_CALIBRATED", init=False)


class ProcessingService:
    def __init__(self, calibration: CalibrationConfig | None = None) -> None:
        self.calibration = calibration if calibration is not None else CalibrationConfig()

    def process(self, frames: Sequence[FrameRef]) -> ProcessingResult:
        """Gate one validated event; never modify its frames or run algorithms."""
        if (len(frames) != 3 or len({f.stem for f in frames}) != 1
                or len({f.port for f in frames}) != 3
                or {f.wavelength_nm for f in frames} != {750, 770, 780}):
            raise ValueError("processing requires one validated 750/770/780 nm triplet")

        raw = self.calibration.correction_path
        if raw is None:
            return ProcessingResult(
                "CALIBRATION_NOT_CONFIGURED",
                "Valid flat/dark calibration unavailable: PAYLOAD_CALIBRATION_PATH is unset.")
        if not isinstance(raw, (str, Path)) or not str(raw).strip() or "\x00" in str(raw):
            return ProcessingResult("INVALID_CALIBRATION_CONFIG",
                                    "Calibration path must be a nonempty absolute .npz path.")
        try:
            path = Path(raw).expanduser()
            if not path.is_absolute() or path.suffix.lower() != ".npz":
                return ProcessingResult("INVALID_CALIBRATION_CONFIG",
                                        "Calibration path must be an absolute .npz path.")
            if not path.is_file():
                return ProcessingResult("CALIBRATION_PATH_UNAVAILABLE",
                                        "Configured flat/dark calibration file is missing or is not a file.",
                                        str(path))
        except (OSError, RuntimeError, ValueError) as exc:
            return ProcessingResult("CALIBRATION_PATH_UNAVAILABLE",
                                    f"Cannot access configured flat/dark calibration: {exc}")
        return ProcessingResult(
            "CALIBRATION_NOT_VERIFIED",
            "Archive is present, but valid flat/dark data and compatibility with this triplet "
            "have not been verified. Scientific processing remains disabled.",
            str(path))
