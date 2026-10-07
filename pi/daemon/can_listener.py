"""Koenig SpaceCAN command adapter for Linux SocketCAN on vcan0.

Wire format follows python-spacecan 0.8.0 Packet.split(), ServicePacket and
FunctionManagementServiceResponder (https://pypi.org/project/spacecan/0.8.0/).
Only unsegmented classic standard-ID frames are supported. Acceptance means
packet validation, not camera reservation. Busy/failed execution gets a
completion failure. This serial proof of concept has no command queue,
retries, simulation commands, or science decisions.
"""
from __future__ import annotations

import logging
import socket
import struct
import threading
from typing import Callable

from .capture_service import CaptureRequest, CaptureResponse

from ..shared.can_protocol import (NODE_ID, REQUEST_ID, REPLY_ID, REQUEST_MASK,
                                   CAPTURE_NOW, Function, parse_request)

log = logging.getLogger("payload.can")
CAN_FRAME = struct.Struct("=IB3x8s")  # Linux struct can_frame, classic CAN
CAN_FILTER = struct.Struct("=II")
# Compare the standard ID and EFF/RTR flags. CAN_ERR_FLAG is not part of
# this exact-ID filter mask; handle_frame still rejects flagged IDs.


def handle_frame(
    can_id: int, payload: bytes,
    capture: Callable[[CaptureRequest], CaptureResponse],
    send: Callable[[int, bytes], None],
    *, timer=None, status=None,
) -> None:
    """Validate one frame, send verification, and invoke the existing owner."""
    if can_id != REQUEST_ID:
        return
    # No assembler: ignore truncated/segmented packets with no usable source
    # service/subtype to echo in a Service 01 verification report.
    if not 4 <= len(payload) <= 8 or payload[:2] != b"\x00\x00":
        return

    def reply(subtype):
        send(REPLY_ID, bytes((0, 0, 1, subtype)) + payload[2:4])

    try:
        command = parse_request(payload)
    except ValueError:
        reply(2)
        return
    reply(1)  # send before capture; a send failure must not start capture
    try:
        completed = True
        if command.function == Function.CAPTURE_NOW:
            result = capture(CaptureRequest(source="live", caller="can"))
            completed = result.status == "success"
            if not completed:
                log.warning("CAN capture %s: %s", result.status, result.failure)
        elif command.function == Function.SET_TIMER_INTERVAL:
            timer.set_interval(command.interval)
        elif command.function == Function.START_TIMER:
            timer.start()
        elif command.function == Function.STOP_TIMER:
            timer.stop()
        elif command.function == Function.GET_STATUS:
            reports = status()
            for report in reports:
                send(REPLY_ID, report)
    except Exception:
        log.exception("CAN command execution failed")
        completed = False
    reply(7 if completed else 8)


class CanListener:
    """One background receiver sharing the daemon's CaptureService.run method.

    Serial handling keeps this PoC small. Frames arriving during capture remain
    in the bounded kernel receive buffer and are handled afterward; there is
    no immediate CAN-side cancellation or guarantee of a response deadline.
    """

    def __init__(self, capture: Callable[[CaptureRequest], CaptureResponse], *, timer=None, status=None):
        self.capture = capture
        self.timer = timer
        self.status = status
        self._stop = threading.Event()
        self._socket = None
        self._thread = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("CAN listener already started")
        if not hasattr(socket, "AF_CAN"):
            raise OSError("SocketCAN requires Linux")
        sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        try:
            sock.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FILTER,
                            CAN_FILTER.pack(REQUEST_ID, REQUEST_MASK))
            sock.settimeout(0.2)
            sock.bind(("vcan0",))
            self._socket = sock
            self._thread = threading.Thread(target=self._receive, args=(sock,),
                                            name="payload-can", daemon=True)
            self._thread.start()
        except Exception:
            self._socket = None
            self._thread = None
            sock.close()
            raise
        log.info("SpaceCAN listening on vcan0: request 0x%03X, reply 0x%03X", REQUEST_ID, REPLY_ID)

    def _receive(self, sock) -> None:
        def send(can_id, payload):
            frame = CAN_FRAME.pack(can_id, len(payload), payload)
            if sock.send(frame) != len(frame):
                raise OSError("short SocketCAN write")

        while not self._stop.is_set():
            try:
                raw = sock.recv(CAN_FRAME.size)
                if self._stop.is_set():
                    break
                if len(raw) != CAN_FRAME.size:
                    continue
                can_id, length, data = CAN_FRAME.unpack(raw)
                if length <= 8:
                    handle_frame(can_id, data[:length], self.capture, send,
                                 timer=self.timer, status=self.status)
            except socket.timeout:
                continue
            except OSError:
                if not self._stop.is_set():
                    log.exception("SocketCAN receive/reply failed; listener stopping")
                break
        sock.close()

    def stop(self) -> None:
        self._stop.set()
        if self._socket is not None:
            self._socket.close()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            if self._thread.is_alive():
                log.warning("CAN capture still finishing at shutdown")
