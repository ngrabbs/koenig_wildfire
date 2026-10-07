"""Koenig classic-CAN wire contract. F0 is PROVISIONAL, not SpaceCAN standard."""
from dataclasses import dataclass
from enum import IntEnum, IntFlag

NODE_ID = 0x20
REQUEST_ID = 0x2A0
REPLY_ID = 0x320
REQUEST_MASK = 0xC00007FF
PROVISIONAL_KOENIG_STATUS_SERVICE = 0xF0
MIN_INTERVAL = 5
MAX_INTERVAL = 86400


class Function(IntEnum):
    CAPTURE_NOW = 1
    SET_TIMER_INTERVAL = 2
    START_TIMER = 3
    STOP_TIMER = 4
    GET_STATUS = 5


class TimerFlag(IntFlag):
    ENABLED = 1
    ACTIVE = 2
    BUSY = 4
    FOCUS = 8


class Source(IntEnum):
    NONE = 0
    LIVE = 1
    SIMULATION = 2


class Result(IntEnum):
    NONE = 0
    SUCCESS = 1
    BUSY = 2
    ERROR = 3


class Processing(IntEnum):
    UNAVAILABLE = 0
    INCOMPLETE_TRIPLET = 1
    WAITING_FOR_CALIBRATION = 2
    PROCESSED_WITH_LIMITATIONS = 3
    FAILED = 4
    INCOMPATIBLE_SOURCE = 5
    MIXED = 6


class Decision(IntEnum):
    NOT_CALIBRATED = 0


SOURCE_CODES = {"live": Source.LIVE, "simulation": Source.SIMULATION}
RESULT_CODES = {"success": Result.SUCCESS, "busy": Result.BUSY, "error": Result.ERROR}
PROCESSING_CODES = {
    "SKIPPED_INCOMPLETE_TRIPLET": Processing.INCOMPLETE_TRIPLET,
    "WAITING_FOR_CALIBRATION": Processing.WAITING_FOR_CALIBRATION,
    "PROCESSED_WITH_LIMITATIONS": Processing.PROCESSED_WITH_LIMITATIONS,
    "PROCESSING_FAILED": Processing.FAILED,
    "SKIPPED_INCOMPATIBLE_SOURCE": Processing.INCOMPATIBLE_SOURCE,
}
DECISION_CODES = {"NOT_CALIBRATED": Decision.NOT_CALIBRATED}
VERIFICATION_NAMES = {1: "acceptance success", 2: "acceptance failure",
                      7: "completion success", 8: "completion failure"}


def encode_u24(value: int) -> bytes:
    if type(value) is not int or not 0 <= value <= 0xFFFFFF:
        raise ValueError("expected unsigned 24-bit integer")
    return value.to_bytes(3, "big")


def decode_u24(data: bytes) -> int:
    if len(data) != 3:
        raise ValueError("u24 requires exactly three bytes")
    return int.from_bytes(data, "big")


def validate_interval(seconds: int) -> int:
    if type(seconds) is not int or not MIN_INTERVAL <= seconds <= MAX_INTERVAL:
        raise ValueError("interval must be 5..86400 integer seconds")
    return seconds


@dataclass(frozen=True)
class Command:
    function: Function
    interval: int | None = None


def parse_request(payload: bytes) -> Command:
    if len(payload) < 5 or payload[:4] != bytes.fromhex("00000801"):
        raise ValueError("expected Service 08/01 invocation")
    function = Function(payload[4])
    if function == Function.SET_TIMER_INTERVAL:
        if len(payload) != 8:
            raise ValueError("SET_TIMER_INTERVAL requires DLC 8")
        return Command(function, validate_interval(decode_u24(payload[5:])))
    if len(payload) != 5:
        raise ValueError("command requires DLC 5")
    return Command(function)


def encode_request(function: Function, interval: int | None = None) -> bytes:
    function = Function(function)
    data = bytes((0, 0, 8, 1, function))
    if function == Function.SET_TIMER_INTERVAL:
        data += encode_u24(validate_interval(interval))
    elif interval is not None:
        raise ValueError("unexpected argument")
    return data


CAPTURE_NOW = encode_request(Function.CAPTURE_NOW)


def encode_status(timer: dict, capture: dict, focus: bool) -> tuple[bytes, bytes]:
    flags = (int(bool(timer["enabled"])) | (int(bool(timer["active"])) << 1)
             | (int(bool(capture["busy"])) << 2) | (int(bool(focus)) << 3))
    last = capture["last"]
    source, status, processing, decision = Source.NONE, Result.NONE, Processing.UNAVAILABLE, Decision.NOT_CALIBRATED
    if last is not None:
        source = SOURCE_CODES.get(last.source, Source.NONE)
        status = RESULT_CODES[last.status]
        codes = {PROCESSING_CODES[e.processing_status] for e in last.events}
        processing = next(iter(codes)) if len(codes) == 1 else Processing.MIXED if codes else Processing.UNAVAILABLE
        decision = DECISION_CODES[last.decision]
    return (bytes((0, 0, PROVISIONAL_KOENIG_STATUS_SERVICE, 1, flags))
            + encode_u24(validate_interval(timer["interval_seconds"])),
            bytes((0, 0, PROVISIONAL_KOENIG_STATUS_SERVICE, 2, source, status, processing, decision)))


def decode_reply(payload: bytes) -> dict:
    if len(payload) == 6 and payload[:3] == b"\0\0\1" and payload[4:] == b"\x08\x01":
        if payload[3] not in VERIFICATION_NAMES:
            raise ValueError("unknown verification subtype")
        return {"kind": "verification", "subtype": payload[3], "meaning": VERIFICATION_NAMES[payload[3]]}
    if len(payload) != 8 or payload[:3] != bytes((0, 0, PROVISIONAL_KOENIG_STATUS_SERVICE)):
        raise ValueError("unsupported reply")
    if payload[3] == 1:
        flags = payload[4]
        if flags & 0xF0:
            raise ValueError("reserved flag bits set")
        return {"kind": "timer", "enabled": bool(flags & TimerFlag.ENABLED),
                "active": bool(flags & TimerFlag.ACTIVE), "busy": bool(flags & TimerFlag.BUSY),
                "focus": bool(flags & TimerFlag.FOCUS),
                "interval_seconds": validate_interval(decode_u24(payload[5:]))}
    if payload[3] == 2:
        return {"kind": "operation", "source": Source(payload[4]).name,
                "result": Result(payload[5]).name, "processing": Processing(payload[6]).name,
                "decision": Decision(payload[7]).name}
    raise ValueError("unknown status subtype")
