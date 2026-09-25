"""Calibration-gate tests: no calibration archive is generated or substituted."""
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path
import unittest
from unittest.mock import patch

from pi.daemon.processing_service import CalibrationConfig, ProcessingService
from pi.daemon.store import parse_capture_filename


class ProcessingTests(unittest.TestCase):
    def setUp(self):
        self.frames = [parse_capture_filename(f"20260924_120000_123_cam{p}_{w}nm.jpg")
                       for p, w in enumerate((770, 750, 780))]
        self.path = Path.home() / "existing-calibration.npz"

    def check_waiting(self, result, code):
        self.assertEqual(result.code, code)
        self.assertEqual(result.processing_status, "WAITING_FOR_CALIBRATION")
        self.assertEqual(asdict(result)["decision"], "NOT_CALIBRATED")
        self.assertTrue(result.reason)

    def test_unconfigured_calibration(self):
        self.check_waiting(ProcessingService().process(self.frames), "CALIBRATION_NOT_CONFIGURED")

    def test_missing_calibration_path(self):
        with patch.object(Path, "is_file", return_value=False):
            result = ProcessingService(CalibrationConfig(self.path)).process(self.frames)
        self.check_waiting(result, "CALIBRATION_PATH_UNAVAILABLE")
        self.assertEqual(result.calibration_path, str(self.path))

    def test_malformed_configuration(self):
        for raw in ("", " ", 17, {}, [], "relative.npz", "bad\x00.npz", str(self.path.with_suffix(".txt"))):
            with self.subTest(raw=raw):
                result = ProcessingService(CalibrationConfig(raw)).process(self.frames)
                self.check_waiting(result, "INVALID_CALIBRATION_CONFIG")

    def test_inaccessible_calibration(self):
        with patch.object(Path, "is_file", side_effect=PermissionError("denied")):
            result = ProcessingService(CalibrationConfig(self.path)).process(self.frames)
        self.check_waiting(result, "CALIBRATION_PATH_UNAVAILABLE")

    def test_presence_alone_never_enables_processing(self):
        # Mock only existence; do not create an NPZ or claim it is valid calibration.
        with patch.object(Path, "is_file", return_value=True), patch.object(Path, "open") as opened:
            result = ProcessingService(CalibrationConfig(self.path)).process(self.frames)
        opened.assert_not_called()
        self.check_waiting(result, "CALIBRATION_NOT_VERIFIED")
        with self.assertRaises(FrozenInstanceError):
            result.decision = "anything else"

    def test_rejects_incomplete_or_mixed_events(self):
        other = parse_capture_filename("20260924_120001_123_cam2_780nm.jpg")
        for frames in (self.frames[:1], self.frames[:2] + [other], [self.frames[0]] * 3):
            with self.subTest(frames=frames), self.assertRaises(ValueError):
                ProcessingService().process(frames)


if __name__ == "__main__":
    unittest.main()
