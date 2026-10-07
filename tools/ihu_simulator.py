"""Reference IHU client: python -m tools.ihu_simulator --interface vcan0."""
import argparse
import json
import math
import socket
import struct
import threading
import time

from pi.shared.can_protocol import (Function, REQUEST_ID, REPLY_ID, REQUEST_MASK,
                                    encode_request, decode_reply)

FRAME = struct.Struct("=IB3x8s")


class IhuClient:
    def __init__(self, sock, timeout=30, output=print):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive")
        self.sock, self.timeout, self.output = sock, timeout, output
        self._pending = threading.Lock()
        self.uncertain = False

    def _display(self, direction, ident, payload):
        self.output(f"{direction} {ident:03X} DLC={len(payload)} {payload.hex(' ').upper()}")

    def execute(self, function, interval=None):
        payload = encode_request(function, interval)
        if not self._pending.acquire(blocking=False):
            raise RuntimeError("one outstanding command only")
        sent = False
        try:
            if self.uncertain:
                raise RuntimeError("previous outcome unknown; stop and reconcile before a new session")
            raw = FRAME.pack(REQUEST_ID, len(payload), payload)
            sent = True
            if self.sock.send(raw) != len(raw):
                raise OSError("short CAN write")
            self._display("TX", REQUEST_ID, payload)
            deadline = time.monotonic() + self.timeout
            accepted, reports = False, {}
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("outcome unknown; no automatic retry")
                self.sock.settimeout(remaining)
                data = self.sock.recv(FRAME.size)
                if len(data) != FRAME.size:
                    raise ValueError("invalid classic CAN frame size")
                ident, length, content = FRAME.unpack(data)
                if ident != REPLY_ID:
                    continue
                if length > 8:
                    raise ValueError("invalid classic DLC")
                content = content[:length]
                self._display("RX", ident, content)
                decoded = decode_reply(content)
                self.output(json.dumps(decoded))
                kind = decoded["kind"]
                if kind != "verification":
                    if not accepted or function != Function.GET_STATUS or kind in reports:
                        raise ValueError("unexpected or duplicate status report")
                    reports[kind] = decoded
                    continue
                subtype = decoded["subtype"]
                if subtype == 2 and not accepted:
                    return {"outcome": "rejected"}
                if subtype == 1 and not accepted:
                    accepted = True
                    continue
                if subtype in (7, 8) and accepted:
                    if subtype == 7 and function == Function.GET_STATUS and set(reports) != {"timer", "operation"}:
                        raise ValueError("completion arrived without both status reports")
                    return {"outcome": "success" if subtype == 7 else "failure", "reports": reports}
                raise ValueError("unexpected verification sequence")
        except socket.timeout as exc:
            self.uncertain = True
            raise TimeoutError("outcome unknown; no automatic retry") from exc
        except (Exception, KeyboardInterrupt):
            if sent:
                self.uncertain = True
            raise
        finally:
            self._pending.release()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", default="vcan0")
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args(argv)
    if not hasattr(socket, "AF_CAN"):
        parser.error("SocketCAN requires Linux")
    try:
        with socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW) as sock:
            sock.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FILTER,
                            struct.pack("=II", REPLY_ID, REQUEST_MASK))
            sock.bind((args.interface,))
            client = IhuClient(sock, args.timeout)
            while True:
                print("\n1. Capture now\n2. Set timer interval\n3. Start timer\n4. Stop timer\n5. Get status\n6. Quit")
                choice = input("> ").strip()
                if choice == "6":
                    return 0
                try:
                    function = Function(int(choice))
                    interval = int(input("Interval (5..86400 seconds): ")) if function == Function.SET_TIMER_INTERVAL else None
                    print(client.execute(function, interval))
                except ValueError as exc:
                    print(f"Error: {exc}")
                    if client.uncertain:
                        print("Outcome unknown. Closing session; reconcile Jetson state before sending again.")
                        return 1
    except (OSError, RuntimeError) as exc:
        print(f"CAN session ended: {exc}")
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nStopped. An interrupted command may still finish on Jetson.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
