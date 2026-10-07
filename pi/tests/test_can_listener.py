"""SpaceCAN proof-of-concept tests, with an optional real Linux vcan0 test."""
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pi.daemon.can_listener import (CAN_FRAME, CAN_FILTER, CAPTURE_NOW,
                                    REQUEST_ID, REPLY_ID, REQUEST_MASK,
                                    CanListener, handle_frame)
from pi.daemon.capture_service import (CaptureRequest, CaptureResponse,
                                       CaptureFailure, CaptureService)
from pi.daemon.store import ImageStore
from pi.shared.settings import SettingsStore


def response(status="success"):
    return CaptureResponse("test", "live", "can", status,
                           failure=None if status == "success" else
                           CaptureFailure("acquire", status.upper(), status))


class PacketTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.capture = Mock(return_value=response())

    def send(self, can_id, data):
        self.sent.append((can_id, data))

    def test_valid_capture_and_success_reply_sequence(self):
        def capture(request):
            # Acceptance must be observable before execution begins.
            self.assertEqual(self.sent, [(0x320, bytes.fromhex("00 00 01 01 08 01"))])
            self.assertEqual(request, CaptureRequest(source="live", caller="can"))
            return response()
        self.capture.side_effect = capture
        handle_frame(0x2A0, bytes.fromhex("00 00 08 01 01"), self.capture, self.send)
        self.capture.assert_called_once()
        self.assertEqual(self.sent, [
            (0x320, bytes.fromhex("00 00 01 01 08 01")),
            (0x320, bytes.fromhex("00 00 01 07 08 01")),
        ])

    def test_wrong_can_id_and_flagged_frames_are_ignored(self):
        for can_id in (0x2A1, 0x320, 0x800002A0, 0x400002A0, 0x200002A0):
            handle_frame(can_id, CAPTURE_NOW, self.capture, self.send)
        self.capture.assert_not_called()
        self.assertEqual(self.sent, [])

    def test_wrong_payload_is_rejected_without_capture(self):
        for payload in (bytes.fromhex("0000080102"), bytes.fromhex("00000801"),
                        CAPTURE_NOW + b"\x00", bytes.fromhex("0000030101")):
            self.sent.clear()
            handle_frame(REQUEST_ID, payload, self.capture, self.send)
            self.assertEqual(self.sent, [(REPLY_ID, b"\x00\x00\x01\x02" + payload[2:4])])
        self.capture.assert_not_called()

    def test_incomplete_segmented_or_oversize_packets_are_ignored(self):
        for payload in (b"", b"\x00\x00\x08", bytes.fromhex("0100080101"),
                        bytes.fromhex("0001080101"), CAPTURE_NOW + b"\x00" * 4):
            handle_frame(REQUEST_ID, payload, self.capture, self.send)
        self.capture.assert_not_called()
        self.assertEqual(self.sent, [])

    def test_busy_capture_reports_completion_failure(self):
        self.capture.return_value = response("busy")
        handle_frame(REQUEST_ID, CAPTURE_NOW, self.capture, self.send)
        self.assertEqual([p.hex() for _, p in self.sent], ["000001010801", "000001080801"])
        self.capture.assert_called_once()

    def test_capture_failure_reports_completion_failure(self):
        self.capture.return_value = response("error")
        handle_frame(REQUEST_ID, CAPTURE_NOW, self.capture, self.send)
        self.assertEqual([p.hex() for _, p in self.sent], ["000001010801", "000001080801"])

    def test_unexpected_exception_reports_completion_failure(self):
        self.capture.side_effect = RuntimeError("camera error")
        with self.assertLogs("payload.can", level="ERROR"):
            handle_frame(REQUEST_ID, CAPTURE_NOW, self.capture, self.send)
        self.assertEqual([p.hex() for _, p in self.sent], ["000001010801", "000001080801"])

    def test_failed_acceptance_send_never_starts_capture(self):
        with self.assertRaises(OSError):
            handle_frame(REQUEST_ID, CAPTURE_NOW, self.capture, Mock(side_effect=OSError("bus down")))
        self.capture.assert_not_called()

    def test_shared_live_service_uses_current_settings_and_remains_uncalibrated(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SettingsStore(root / "settings.json")
            settings.update({"burst_count": 2, "shared": {"ExposureTime": 12000}})
            store = ImageStore(root / "images")
            results = []

            def existing_camera(**kwargs):
                self.assertEqual(kwargs["n"], 2)
                self.assertEqual(kwargs["controls_for"](0)["ExposureTime"], 12000)
                # Fake the backend's two independent burst events.
                frames = []
                for i in range(kwargs["n"]):
                    path = store.root / f"20260924_12000{i}_123_cam0_770nm.jpg"
                    path.write_bytes(b"frame")
                    frames.append(SimpleNamespace(path=path))
                return frames

            service = CaptureService(settings=settings, store=store,
                                     live_capture=existing_camera, busy_error=type("Busy", (Exception,), {}))

            def capture(request):
                result = service.run(request)
                results.append(result)
                return result

            handle_frame(REQUEST_ID, CAPTURE_NOW, capture, self.send)
            self.assertEqual(results[0].status, "success")
            self.assertEqual(results[0].caller, "can")
            self.assertEqual(results[0].decision, "NOT_CALIBRATED")
            self.assertEqual(len(results[0].events), 2)
            self.assertTrue(all(e.processing_status == "SKIPPED_INCOMPLETE_TRIPLET"
                                for e in results[0].events))


class ControlTests(unittest.TestCase):
    def test_timer_dispatch_and_status_sequence(self):
        from pi.shared.can_protocol import Function, encode_request
        timer = Mock()
        capture = Mock()
        reports = (bytes.fromhex('0000f0010300003c'), bytes.fromhex('0000f00200000000'))
        status = Mock(return_value=reports)
        for function in (Function.SET_TIMER_INTERVAL, Function.START_TIMER, Function.STOP_TIMER, Function.GET_STATUS):
            sent = []
            handle_frame(REQUEST_ID, encode_request(function, 60 if function == Function.SET_TIMER_INTERVAL else None),
                         capture, lambda ident, data: sent.append(data), timer=timer, status=status)
            expected = [bytes.fromhex('000001010801')]
            if function == Function.GET_STATUS: expected.extend(reports)
            expected.append(bytes.fromhex('000001070801'))
            self.assertEqual(sent, expected)
        timer.set_interval.assert_called_once_with(60)
        timer.start.assert_called_once_with()
        timer.stop.assert_called_once_with()
        capture.assert_not_called()

    def test_timer_execution_failure_and_acceptance_send_failure(self):
        timer = Mock()
        timer.start.side_effect = OSError('save failed')
        sent = []
        handle_frame(REQUEST_ID, bytes.fromhex('0000080103'), Mock(),
                     lambda ident, data: sent.append(data), timer=timer)
        self.assertEqual(sent, [bytes.fromhex('000001010801'), bytes.fromhex('000001080801')])
        timer.reset_mock()
        with self.assertRaises(OSError):
            handle_frame(REQUEST_ID, bytes.fromhex('0000080103'), Mock(),
                         Mock(side_effect=OSError('bus down')), timer=timer)
        timer.start.assert_not_called()

    def test_bad_controls_never_execute(self):
        timer = Mock()
        for text in ('00000801ff', '0000080102000004', '0000080102015181',
                     '000008010300', '000008010400', '000008010500'):
            sent = []
            handle_frame(REQUEST_ID, bytes.fromhex(text), Mock(),
                         lambda ident, data: sent.append(data), timer=timer)
            self.assertEqual(sent, [bytes.fromhex('000001020801')])
        self.assertEqual(timer.mock_calls, [])


class TransportTests(unittest.TestCase):
    def test_exact_standard_id_request_mask(self):
        self.assertEqual(REQUEST_MASK, 0xC00007FF)

    def test_socket_setup_and_stop(self):
        sock = Mock()
        listener = CanListener(Mock())
        # Patch Linux constants for the Windows unit-test host as well.
        with patch.multiple(socket, AF_CAN=29, CAN_RAW=1, SOL_CAN_RAW=101,
                            CAN_RAW_FILTER=1, create=True), \
                patch.object(socket, "socket", return_value=sock) as factory, \
                patch("pi.daemon.can_listener.threading.Thread") as thread:
            thread.return_value.is_alive.return_value = False
            listener.start()
            factory.assert_called_once_with(29, socket.SOCK_RAW, 1)
            sock.bind.assert_called_once_with(("vcan0",))
            sock.setsockopt.assert_called_once_with(101, 1, CAN_FILTER.pack(REQUEST_ID, 0xC00007FF))
            listener.stop()
            sock.close.assert_called_once()
            thread.return_value.join.assert_called_once_with(timeout=1.0)

    def test_bind_failure_closes_socket(self):
        sock = Mock()
        sock.bind.side_effect = OSError("vcan0 absent")
        with patch.multiple(socket, AF_CAN=29, CAN_RAW=1, SOL_CAN_RAW=101,
                            CAN_RAW_FILTER=1, create=True), \
                patch.object(socket, "socket", return_value=sock):
            with self.assertRaises(OSError):
                CanListener(Mock()).start()
        sock.close.assert_called_once()

    def test_classic_frame_receive_encoding(self):
        capture = Mock(return_value=response())
        listener = CanListener(capture)
        sock = Mock()
        sock.send.side_effect = lambda raw: len(raw)
        frames = iter([b"bad", CAN_FRAME.pack(REQUEST_ID, 9, CAPTURE_NOW),
                       CAN_FRAME.pack(REQUEST_ID, len(CAPTURE_NOW), CAPTURE_NOW)])

        def receive(_size):
            try:
                return next(frames)
            except StopIteration:
                listener._stop.set()
                raise socket.timeout()
        sock.recv.side_effect = receive
        listener._receive(sock)
        capture.assert_called_once_with(CaptureRequest(caller="can"))
        encoded = [CAN_FRAME.unpack(c.args[0]) for c in sock.send.call_args_list]
        self.assertEqual([(can_id, data[:n].hex()) for can_id, n, data in encoded],
                         [(0x320, "000001010801"), (0x320, "000001070801")])
        sock.close.assert_called_once()


@unittest.skipUnless(sys.platform.startswith("linux") and hasattr(socket, "AF_CAN"),
                     "real SocketCAN requires Linux with vcan0")
class VcanTests(unittest.TestCase):
    def test_real_vcan_request_and_replies(self):
        client = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        self.addCleanup(client.close)
        try:
            client.bind(("vcan0",))
        except OSError as exc:
            self.skipTest(f"vcan0 is unavailable: {exc}")
        client.settimeout(2)
        client.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FILTER,
                          CAN_FILTER.pack(REPLY_ID, REQUEST_MASK))
        capture = Mock(return_value=response())
        listener = CanListener(capture)
        listener.start()
        self.addCleanup(listener.stop)
        client.send(CAN_FRAME.pack(REQUEST_ID, 5, CAPTURE_NOW))
        received = []
        for _ in range(2):
            can_id, length, data = CAN_FRAME.unpack(client.recv(CAN_FRAME.size))
            received.append((can_id, data[:length].hex()))
        self.assertEqual(received, [(0x320, "000001010801"), (0x320, "000001070801")])
        capture.assert_called_once_with(CaptureRequest(caller="can"))

    def test_real_vcan_control_client_and_autonomous_timer(self):
        from apscheduler.schedulers.background import BackgroundScheduler
        from pi.daemon.timer_service import TimerService
        from pi.shared.can_protocol import Function, encode_status
        from tools.ihu_simulator import IhuClient
        import threading
        client_socket = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        self.addCleanup(client_socket.close)
        try:
            client_socket.bind(('vcan0',))
        except OSError as exc:
            self.skipTest(f'vcan0 is unavailable: {exc}')
        client_socket.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FILTER,
                                 CAN_FILTER.pack(REPLY_ID, REQUEST_MASK))
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        settings = SettingsStore(Path(temp.name) / 'settings.json')
        scheduler = BackgroundScheduler(); scheduler.start()
        self.addCleanup(scheduler.shutdown)
        tick = threading.Event()
        timer = TimerService(settings, scheduler, tick.set)
        status = lambda: encode_status(timer.snapshot(), {'busy': False, 'last': None}, False)
        listener = CanListener(Mock(return_value=response()), timer=timer, status=status)
        listener.start(); self.addCleanup(listener.stop)
        client = IhuClient(client_socket, timeout=2, output=lambda _: None)
        for f, interval in ((Function.SET_TIMER_INTERVAL, 5), (Function.START_TIMER, None)):
            self.assertEqual(client.execute(f, interval)['outcome'], 'success')
        self.assertTrue(client.execute(Function.GET_STATUS)['reports']['timer']['active'])
        self.assertTrue(tick.wait(7))
        client_socket.settimeout(.2)
        with self.assertRaises(socket.timeout): client_socket.recv(CAN_FRAME.size)
        self.assertEqual(client.execute(Function.STOP_TIMER)['outcome'], 'success')
        self.assertFalse(client.execute(Function.GET_STATUS)['reports']['timer']['active'])


if __name__ == "__main__":
    unittest.main()
