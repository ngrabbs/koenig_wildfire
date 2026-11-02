"""Fixed generated recipes only. No files/images/arrays supplied by callers."""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import cv2
import numpy as np
from .immutable import FrozenArray
from .runtime import canonical, digest, file_digest
from .errors import ContractViolation
from .constants import PRODUCER_VERSION, MAX_INPUT_BYTES, MAX_WIDTH, MAX_HEIGHT, MAX_PIXELS

PIPELINE_CASES = (
 "P01_IDENTITY64", "P02_INTEGER_PLUS1", "P03_INTEGER_MINUS2", "P04_FRACTIONAL",
 "P05_FLAT64", "P06_SATURATED64", "P07_COLOR128", "P08_WORKWIDTH1152",
 "P09_MISSING_CORE", "P10_TRUNCATED_SOURCE", "P11_KNOWN_EXCESS",
 "P12_BAD_MAPPING", "P13_BAD_EXPOSURE", "P14_BOUNDARY128")
UNIT_CASES = (
 "U01_ZERO_EXCESS", "U02_KNOWN_EXCESS", "U03_NET_ZERO", "U04_NET_NEGATIVE",
 "U05_BELOW_P_FLOOR", "U06_UNRESOLVED", "U07_INCOMPLETE",
 "U08_OVERLAPPING_BACKGROUND", "U09_IMMUTABLE")
CASE_IDS = PIPELINE_CASES + UNIT_CASES

@dataclass(frozen=True)
class Payload:
    case_id: str
    mode: str
    event_id: str
    channels: tuple
    exposures: tuple
    compressed: tuple
    aligned_unit: tuple
    gain: tuple
    dark: tuple
    source: FrozenArray
    background: FrozenArray
    common_unit: object
    source_complete: bool
    emitting_box: tuple
    scenario_shifts: tuple
    interpolation_pad: int
    selection_id: str
    producer_sha256: str
    custody_sha256: str
    input_bytes: int

def _stamp(payload):
    """Digest only immutable data minted here, not caller labels."""
    return digest(canonical({
        "case": payload.case_id, "mode": payload.mode, "event": payload.event_id,
        "channels": payload.channels, "exposures": payload.exposures,
        "compressed": [digest(b) for b in payload.compressed],
        "unit": [a.descriptor("GENERATED_ALIGNED_DN") for a in payload.aligned_unit],
        "gain": [a.descriptor("GENERATED_GAIN") for a in payload.gain],
        "dark": [a.descriptor("GENERATED_DARK") for a in payload.dark],
        "source": payload.source.descriptor("BOOLEAN"),
        "background": payload.background.descriptor("BOOLEAN"),
        "common_unit": payload.common_unit.descriptor("BOOLEAN") if payload.common_unit else None,
        "source_complete": payload.source_complete, "emitting_box":payload.emitting_box,
        "scenario_shifts":payload.scenario_shifts,"interpolation_pad":payload.interpolation_pad,
        "selection": payload.selection_id,
        "producer": PRODUCER_VERSION, "producer_sha256": payload.producer_sha256,
        "input_bytes":payload.input_bytes}))

def verify_payload(payload):
    if type(payload) is not Payload or payload.case_id not in CASE_IDS:
        raise ContractViolation("UNAUTHORIZED_INPUT_ORIGIN")
    if _stamp(payload) != payload.custody_sha256:
        raise ContractViolation("SOURCE_IDENTITY_CHANGED")
    if file_digest(Path(__file__)) != payload.producer_sha256:
        raise ContractViolation("SOURCE_IDENTITY_CHANGED", "producer")
    return payload

def produce(case_id, monitor):
    """Private session call; writes no files. Never called during B2 preparation."""
    if type(case_id) is not str or case_id not in CASE_IDS:
        raise ContractViolation("UNAUTHORIZED_INPUT_ORIGIN", "unknown generated recipe")
    mode = "UNIT_MATH_ONLY" if case_id in UNIT_CASES else "GENERATED_PIPELINE"
    h, w = ((32, 32) if mode == "UNIT_MATH_ONLY" else
            (128, 1152) if case_id == "P08_WORKWIDTH1152" else
            (128, 128) if case_id in ("P07_COLOR128", "P14_BOUNDARY128") else (64, 64))
    if w > MAX_WIDTH or h > MAX_HEIGHT or h*w > MAX_PIXELS:
        raise ContractViolation("RESOURCE_OR_LIBRARY_FAILURE", "fixture dimensions")
    cy, cx = h//2, w//2
    r = np.zeros((h,w), bool)
    r[cy-4:cy+4, cx-4:cx+4] = True
    b = np.zeros((h,w), bool)
    b[cy-12:cy-8, cx-8:cx] = True
    complete = True
    emitting_box=(cx-4,cy-4,cx+3,cy+3)
    scenario_shifts=((0.0,0.0),(0.0,0.0),(0.0,0.0))
    interpolation_pad=0
    channels = ((0,770),(1,750),(2,780))
    exposures = (100.0,100.0,100.0)
    compressed, unit, common = (), (), None
    if mode == "UNIT_MATH_ONLY":
        planes = [np.full((h,w),16.0,np.float32) for _ in range(3)]
        if case_id=="U01_ZERO_EXCESS":
            for k in range(3):planes[k][r]=32.0
        if case_id not in ("U01_ZERO_EXCESS","U03_NET_ZERO"):
            for k in range(3):
                planes[k][r] = 48.0 if k == 0 else 32.0
        if case_id == "U04_NET_NEGATIVE":
            for k in range(3):
                planes[k][r] = 12.0 if k == 0 else 8.0
        if case_id == "U05_BELOW_P_FLOOR":
            planes = [np.full((h,w),2.0,np.float32) for _ in range(3)]
            for k in range(3):
                planes[k][r] = 4.0 if k == 0 else 3.0
        common = np.ones((h,w),bool)
        if case_id == "U07_INCOMPLETE":
            common[cy,cx] = False
        if case_id == "U08_OVERLAPPING_BACKGROUND":
            b = r.copy()
        unit = tuple(FrozenArray(a) for a in planes)
        common = FrozenArray(common)
    else:
        # Closed deterministic spatial recipe; no external image seed.
        y,x = np.indices((h,w),dtype=np.int32)
        base = (40 + ((17*x+29*y+(x*y)%31)%61)).astype(np.uint8)
        images = [base.copy() for _ in range(3)]
        emitter=np.zeros((h,w),bool)
        emitter[cy-2:cy+2,cx-2:cx+2]=True
        emitting_box=(cx-2,cy-2,cx+1,cy+1)
        if case_id=="P10_TRUNCATED_SOURCE":
            emitter=r.copy();emitting_box=(cx-4,cy-4,cx+3,cy+3)
        interpolation_pad=1
        for k in range(3):
            images[k][emitter] = np.minimum(images[k][emitter].astype(np.int16)+16,220).astype(np.uint8)
        if case_id == "P11_KNOWN_EXCESS":
            images[0][emitter] = np.minimum(images[0][emitter].astype(np.int16)+32,240).astype(np.uint8)
        if case_id == "P05_FLAT64":
            images = [np.full((h,w),64,np.uint8) for _ in range(3)]
        if case_id == "P06_SATURATED64":
            images = [np.full((h,w),255,np.uint8) for _ in range(3)]
        shifts = {"P02_INTEGER_PLUS1":((1,0),(0,-1)),
                  "P03_INTEGER_MINUS2":((-2,1),(2,-1)),
                  "P04_FRACTIONAL":((0.5,0),(-0.5,0.5)),
                  "P14_BOUNDARY128":((60,0),(0,0))}.get(case_id)
        if shifts:
            scenario_shifts=((0.0,0.0),)+tuple(tuple(z) for z in shifts)
            if any(float(z)!=int(z) for pair in shifts for z in pair):interpolation_pad=2
            for k,(dx,dy) in enumerate(shifts,1):
                monitor.count("producer_scenario_warp")
                images[k] = cv2.warpAffine(images[k],np.float32([[1,0,dx],[0,1,dy]]),
                                           (w,h),flags=cv2.INTER_LINEAR)
        if case_id == "P09_MISSING_CORE":
            # Lossless generated PNG is deliberately NOT an allowed replacement decoder.
            # Use the generated JPEG observation; oracle checks decoded clipping/support.
            images[1][cy-1:cy+2,cx-1:cx+2] = 255
        if case_id == "P10_TRUNCATED_SOURCE":
            r[:,cx+3:] = False
            complete = False
        if case_id == "P12_BAD_MAPPING":
            channels = ((0,770),(1,780),(2,780))
        if case_id == "P13_BAD_EXPOSURE":
            exposures = (100.0,200.0,100.0)
        encoded = []
        for a in images:
            if case_id == "P07_COLOR128":
                a = np.stack((a,np.minimum(a.astype(np.int16)+7,255).astype(np.uint8),
                              np.maximum(a.astype(np.int16)-5,0).astype(np.uint8)),axis=2)
            monitor.count("producer_jpeg_encode")
            ok, data = cv2.imencode(".jpg",a,[cv2.IMWRITE_JPEG_QUALITY,100])
            if not ok:
                raise ContractViolation("RESOURCE_OR_LIBRARY_FAILURE", "generated encoding")
            encoded.append(bytes(data))
        compressed = tuple(encoded)
    gain = tuple(FrozenArray(np.ones((h,w),np.float32)) for _ in range(3))
    dark = tuple(FrozenArray(np.zeros((h,w),np.float32)) for _ in range(3))
    source, background = FrozenArray(r), FrozenArray(b)
    size = sum(len(z) for z in compressed)+sum(a.nbytes for a in unit+gain+dark)
    size += source.nbytes+background.nbytes+(common.nbytes if common else 0)
    if size > MAX_INPUT_BYTES:
        raise ContractViolation("RESOURCE_OR_LIBRARY_FAILURE", "generated input cap")
    selection = digest(canonical({"case":case_id,"rule":"FIXED_CENTER_8x8_BG_4x8_V1",
             "source":source.descriptor("BOOLEAN"),"background":background.descriptor("BOOLEAN"),
             "selection_basis":"GENERATED_TRUTH_BEFORE_SPECTRAL_MEASUREMENT"}))
    fields = dict(case_id=case_id,mode=mode,event_id="GEN_B3_"+case_id,channels=channels,
                  exposures=exposures,compressed=compressed,aligned_unit=unit,gain=gain,dark=dark,
                  source=source,background=background,common_unit=common,source_complete=complete,emitting_box=emitting_box,scenario_shifts=scenario_shifts,
                  interpolation_pad=interpolation_pad,selection_id=selection,producer_sha256=file_digest(Path(__file__)),
                  custody_sha256="",input_bytes=size)
    provisional = Payload(**fields)
    fields["custody_sha256"] = _stamp(provisional)
    return Payload(**fields)

def custody_record(payload):
    verify_payload(payload)
    return {"origin":"GENERATED_ONLY","mode":payload.mode,"case_id":payload.case_id,
            "producer_version":PRODUCER_VERSION,"producer_sha256":payload.producer_sha256,
            "custody_sha256":payload.custody_sha256,"event_id":payload.event_id,
            "channels":[list(x) for x in payload.channels],
            "compressed_sha256":[digest(x) for x in payload.compressed],
            "calibration_id":digest(canonical({"gain":[a.descriptor("GENERATED_GAIN") for a in payload.gain],
                         "dark":[a.descriptor("GENERATED_DARK") for a in payload.dark]})),
            "calibration_origin":"GENERATED_MAPS_NOT_PHASE4_ARCHIVE",
            "selection_id":payload.selection_id,"input_bytes":payload.input_bytes,
            "source_geometry_model":{"emitting_box":list(payload.emitting_box),
                "scenario_shifts":[list(x) for x in payload.scenario_shifts],
                "interpolation_pad":payload.interpolation_pad,
                "source_complete_declared":payload.source_complete,
                "claim_scope":"GENERATED_PRE_ENCODING_ONLY"},
            "physical_evidence":"UNKNOWN"}
