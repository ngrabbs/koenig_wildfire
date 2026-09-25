"""Shared acquisition boundary with a fail-closed calibration gate.

Triplet validation covers grouping and spectral metadata, not image pixels
or radiometric calibration. The service never constructs a camera instance.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
import logging
from pathlib import Path
import threading
from typing import Callable, Literal
from uuid import uuid4

from .store import (FrameRef, ImageStore, TripletSelectionError,
                    parse_capture_filename, select_stored_triplet)
from ..shared.settings import SettingsStore
from .processing_service import ProcessingResult, ProcessingService

log = logging.getLogger("payload.capture_service")


@dataclass(frozen=True)
class CaptureRequest:
    source: Literal["live", "simulation"] = "live"
    caller: Literal["http", "timer", "can"] = "http"
    simulation_stem: str | None = None


@dataclass(frozen=True)
class CaptureFailure:
    stage: str
    code: str
    message: str


@dataclass(frozen=True)
class CaptureImage:
    port: int
    wavelength_nm: int
    id: str
    bytes: int


@dataclass(frozen=True)
class CaptureEvent:
    stem: str
    captures: list[CaptureImage]
    processing_status: Literal["WAITING_FOR_CALIBRATION", "SKIPPED_INCOMPLETE_TRIPLET"]
    processing: ProcessingResult | None = None


@dataclass(frozen=True)
class CaptureResponse:
    request_id: str
    source: str
    caller: str
    status: Literal["success", "busy", "error"]
    events: list[CaptureEvent] = field(default_factory=list)
    failure: CaptureFailure | None = None
    decision: Literal["NOT_CALIBRATED"] = field(default="NOT_CALIBRATED", init=False)

    def to_dict(self) -> dict:
        payload = asdict(self)
        # Keep the existing HTTP capture list and busy/error fields available.
        payload["captures"] = [asdict(image) for event in self.events for image in event.captures]
        if self.failure:
            payload["error"] = self.failure.message
        if self.status == "busy":
            payload["busy"] = True
        return payload


class CaptureService:
    def __init__(
        self, *, settings: SettingsStore, store: ImageStore,
        live_capture: Callable, busy_error: type[Exception],
        simulation_root: Path | None = None,
        processor: ProcessingService | None = None,
    ):
        self.settings = settings
        self.store = store
        self.live_capture = live_capture
        self.busy_error = busy_error
        self.simulation_root = simulation_root if simulation_root is not None else store.root
        self._busy = threading.Lock()
        self.processor = processor if processor is not None else ProcessingService()

    def run(self, request: CaptureRequest) -> CaptureResponse:
        request_id = uuid4().hex

        def result(status, *, events=None, stage="request", code="", message=""):
            return CaptureResponse(
                request_id, request.source, request.caller, status, events or [],
                CaptureFailure(stage, code, message) if code else None,
            )

        if request.source not in ("live", "simulation") or request.caller not in ("http", "timer", "can"):
            return result("error", code="INVALID_REQUEST", message="unknown source or caller")
        if ((request.source == "simulation" and not isinstance(request.simulation_stem, str))
                or (request.source == "live" and request.simulation_stem is not None)):
            return result("error", code="INVALID_REQUEST", message="simulation requires an explicit stem; live does not accept one")
        if not self._busy.acquire(blocking=False):
            return result("busy", stage="acquire", code="BUSY", message="capture already in progress")
        stage = "acquire"
        try:
            if request.source == "simulation":
                frames = select_stored_triplet(self.simulation_root, request.simulation_stem)
            else:
                frames = self._acquire_live(self.settings.snapshot())
            stage = "validate"
            if not frames:
                raise ValueError("capture returned no images")
            groups = defaultdict(list)
            for frame in frames:
                groups[frame.stem].append(frame)
            events = []
            for stem, group in groups.items():
                ports = [f.port for f in group]
                waves = [f.wavelength_nm for f in group]
                if len(set(ports)) != len(ports) or len(set(waves)) != len(waves):
                    raise TripletSelectionError("AMBIGUOUS_TRIPLET", "duplicate camera or wavelength")
                ready = len(group) == 3 and set(waves) == {750, 770, 780}
                images = [CaptureImage(f.port, f.wavelength_nm, f.path.name, f.path.stat().st_size)
                          for f in group]
                stage = "processing"
                processing = self.processor.process(group) if ready else None
                events.append(CaptureEvent(
                    stem, images, processing.processing_status if processing is not None
                    else "SKIPPED_INCOMPLETE_TRIPLET", processing))
                stage = "validate"
            return result("success", events=events)
        except self.busy_error as exc:
            return result("busy", stage="acquire", code="BUSY", message=str(exc))
        except TripletSelectionError as exc:
            return result("error", stage="validate", code=exc.code, message=str(exc))
        except Exception as exc:
            log.exception("capture request %s failed during %s", request_id, stage)
            return result("error", stage=stage, code="CAPTURE_FAILED", message=str(exc))
        finally:
            self._busy.release()

    def _acquire_live(self, snapshot: dict) -> list[FrameRef]:
        def controls_for(port):
            controls = dict(snapshot["shared"])
            if snapshot["advanced_mode"]:
                controls.update(snapshot["per_camera"].get(str(port), {}))
            return controls

        results = self.live_capture(
            n=snapshot["burst_count"],
            path_fn_factory=lambda: self.store.burst_path_fn(
                "jpg", lambda port: snapshot["wavelengths"][str(port)]),
            controls_for=controls_for,
            resolution=tuple(snapshot["resolution"]),
            rotation_for=lambda port: snapshot["rotations"].get(str(port), 0),
        )
        # Filenames carry the applied mapping, unlike the backend's static label.
        return [parse_capture_filename(item.path) for item in results]
