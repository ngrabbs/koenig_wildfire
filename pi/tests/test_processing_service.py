"""Archive validation and ordered processing tests; synthetic fixtures are test-only."""
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path
import unittest
import io
import contextlib
import hashlib
from tempfile import TemporaryDirectory
import cv2
import numpy as np
from unittest.mock import patch

from pi.daemon.processing_service import (CalibrationConfig, ProcessingService,
    CalibrationError, validate_calibration, calibrated_region_mask, CALIBRATION_SHAPE)
from pi.daemon.store import parse_capture_filename
from tools import flat_field, register_triplets, k_index
from pi.daemon.capture_service import CaptureRequest, CaptureService
from pi.daemon.store import ImageStore
from pi.shared.settings import SettingsStore

ARCHIVE = Path(__file__).resolve().parents[2] / "calibration" / "correction_20261003.npz"


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
        with patch.object(Path, "is_file", return_value=True), patch.object(Path, "read_bytes", return_value=b"not an archive"):
            result = ProcessingService(CalibrationConfig(self.path)).process(self.frames)
        self.check_waiting(result, "CALIBRATION_UNREADABLE")
        with self.assertRaises(FrozenInstanceError):
            result.decision = "anything else"

    def test_rejects_incomplete_or_mixed_events(self):
        other = parse_capture_filename("20260924_120001_123_cam2_780nm.jpg")
        for frames in (self.frames[:1], self.frames[:2] + [other], [self.frames[0]] * 3):
            with self.subTest(frames=frames):
                result = ProcessingService().process(frames)
                self.assertEqual(result.code, "INCOMPATIBLE_TRIPLET")
                self.assertEqual(result.processing_status, "PROCESSING_FAILED")


class ArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = ARCHIVE.read_bytes()
        with np.load(io.BytesIO(cls.raw), allow_pickle=False) as data:
            cls.arrays = {k: data[k] for k in data.files}

    def test_valid_supplied_archive(self):
        metadata = validate_calibration(self.raw)
        self.assertEqual(metadata["sha256"], "0649ecedd1d68e31f2cc58bd4808946c8a360d157b22db5debfe2b227df58b51")
        self.assertEqual(metadata["shape"], [1088, 1456])
        self.assertEqual(metadata["has_dark"], [True] * 3)

    def altered(self, key, value, code):
        arrays = dict(self.arrays)
        if value is None:
            arrays.pop(key)
        else:
            arrays[key] = value
        stream = io.BytesIO()
        np.savez(stream, **arrays)
        with self.assertRaises(CalibrationError) as caught:
            validate_calibration(stream.getvalue())
        self.assertEqual(caught.exception.code, code)

    def test_missing_keys(self):
        for key in ("ports", "gain_0", "dark_2", "has_dark"):
            with self.subTest(key=key):
                self.altered(key, None, "CALIBRATION_MISSING_KEYS")

    def test_incorrect_ports(self):
        for ports in ([0, 1, 1], [0, 1, 3], [0, 1]):
            self.altered("ports", np.array(ports), "CALIBRATION_PORTS_INVALID")

    def test_incorrect_shapes(self):
        for key in ("gain_1", "dark_0"):
            self.altered(key, np.ones((10, 20)), "CALIBRATION_SHAPE_INVALID")

    def test_nonfinite(self):
        for key, value in (("gain_1", np.nan), ("dark_2", np.inf)):
            array = self.arrays[key].copy()
            array[0, 0] = value
            self.altered(key, array, "CALIBRATION_NONFINITE")

    def test_nonpositive_gain(self):
        for value in (0, -1):
            array = self.arrays["gain_0"].copy()
            array[0, 0] = value
            self.altered("gain_0", array, "CALIBRATION_GAIN_INVALID")

    def test_missing_measured_dark(self):
        self.altered("has_dark", np.array([True, False, True]), "CALIBRATION_DARK_MISSING")

    def test_object_archive_cannot_load_pickle(self):
        self.altered("gain_0", np.array([{"unsafe": "object"}], dtype=object), "CALIBRATION_UNREADABLE")

    def test_numeric_dtype_validation(self):
        self.altered("gain_0", np.full(CALIBRATION_SHAPE, True), "CALIBRATION_DTYPE_INVALID")

    def test_other_structurally_valid_hash_is_allowed(self):
        stream = io.BytesIO()
        np.savez(stream, **{**self.arrays, "target": np.float32(123)})
        metadata = validate_calibration(stream.getvalue())
        self.assertNotEqual(metadata["sha256"], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(metadata["validation"], "STRUCTURALLY_VALID")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.frames = [parse_capture_filename(self.root / f"20261003_120000_123_cam{p}_{w}nm.png")
                       for p, w in enumerate((770, 750, 780))]
        self.service = ProcessingService(CalibrationConfig(ARCHIVE))
        # Textured original images that become almost identical after the real
        # supplied correction. This exercises the real registration algorithm.
        rng = np.random.default_rng(10)
        texture = cv2.GaussianBlur(rng.uniform(35, 165, CALIBRATION_SHAPE).astype(np.float32), (3, 3), 0)
        with np.load(ARCHIVE, allow_pickle=False) as data:
            for f in self.frames:
                raw = np.clip(texture / data[f"gain_{f.port}"] + data[f"dark_{f.port}"], 0, 253).astype(np.uint8)
                self.assertTrue(cv2.imwrite(str(f.path), raw))

    def run_pipeline(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.service.process(self.frames, source="simulation")

    def test_success_real_tools_and_isolation(self):
        before = {f.path: hashlib.sha256(f.path.read_bytes()).hexdigest() for f in self.frames}
        seen = []
        original_apply = flat_field.apply
        def apply(*args):
            seen.append(args[0].parent)
            return original_apply(*args)
        service = CaptureService(settings=SettingsStore(self.root / "settings.json"),
                                 store=ImageStore(self.root), live_capture=lambda **kw: self.fail("No live capture"),
                                 busy_error=RuntimeError, processor=self.service)
        with patch.object(flat_field, "apply", side_effect=apply), contextlib.redirect_stdout(io.StringIO()):
            response = service.run(CaptureRequest(source="simulation", simulation_stem=self.frames[0].stem))
        self.assertEqual(response.status, "success", response)
        self.assertEqual(response.source, "simulation")
        self.assertEqual(response.decision, "NOT_CALIBRATED")
        self.assertEqual(len(response.to_dict()["captures"]), 3)
        result = response.events[0].processing
        self.assertEqual(result.processing_status, "PROCESSED_WITH_LIMITATIONS", result)
        self.assertEqual(result.decision, "NOT_CALIBRATED")
        self.assertEqual(result.registration["status"], "ok")
        self.assertGreater(result.metrics["valid_pixel_count"], 0)
        self.assertLess(abs(result.metrics["median_delta77"]), .03)
        self.assertAlmostEqual(result.metrics["diagnostic_ratio_770_750"], 1, delta=.03)
        self.assertAlmostEqual(result.metrics["diagnostic_ratio_770_780"], 1, delta=.03)
        self.assertLess(result.metrics["valid_full_frame_fraction"], .64)
        self.assertEqual(result.provenance["compatibility"], "ACQUISITION_METADATA_UNVERIFIED")
        self.assertTrue(all(not p.exists() for p in seen))
        self.assertEqual(before, {f.path: hashlib.sha256(f.path.read_bytes()).hexdigest() for f in self.frames})

    def test_incompatible_dimensions(self):
        cv2.imwrite(str(self.frames[0].path), np.zeros((1080, 1920), np.uint8))
        self.assertEqual(self.run_pipeline().code, "INPUT_SHAPE_INCOMPATIBLE")

    def test_no_live_processing(self):
        result = self.service.process(self.frames, source="live")
        self.assertEqual(result.processing_status, "SKIPPED_INCOMPATIBLE_SOURCE")

    def test_wrong_mapping(self):
        from dataclasses import replace
        frames = [replace(self.frames[0], wavelength_nm=750), replace(self.frames[1], wavelength_nm=770), self.frames[2]]
        self.assertEqual(self.service.process(frames).code, "INCOMPATIBLE_TRIPLET")

    def test_flat_failure_and_missing_output(self):
        for behavior in ({"side_effect": RuntimeError("flat failed")}, {"return_value": 0}):
            with patch.object(flat_field, "apply", **behavior), patch.object(register_triplets, "process_triplet") as register:
                result = self.run_pipeline()
            self.assertEqual(result.code, "FLAT_FIELD_FAILED")
            register.assert_not_called()

    def test_registration_failure_prevents_index(self):
        failed = register_triplets.TripletResult(self.frames[0].stem, 0, [], "low-confidence", "not enough texture")
        with patch.object(register_triplets, "process_triplet", return_value=failed), patch.object(k_index, "k_index") as index:
            result = self.run_pipeline()
        self.assertEqual(result.code, "REGISTRATION_FAILED")
        self.assertEqual(result.registration["status"], "low-confidence")
        index.assert_not_called()

    def test_no_overlap(self):
        shifts = [register_triplets.ChannelShift(p, w, p * 2000, 0, 1, 1, 20, 1, True)
                  for p, w in enumerate((770, 750, 780))]
        reg = register_triplets.TripletResult(self.frames[0].stem, 0, shifts, "ok")
        with patch.object(register_triplets, "process_triplet", return_value=reg):
            result = self.run_pipeline()
        self.assertEqual(result.code, "NO_CALIBRATED_OVERLAP")

    def test_corrected_input_is_not_corrected_again(self):
        cv2.imwrite(str(self.frames[0].path), np.full(CALIBRATION_SHAPE, 100, np.uint16))
        self.assertEqual(self.run_pipeline().code, "INPUT_ENCODING_UNSUPPORTED")

    def test_missing_aligned_output(self):
        shifts = [register_triplets.ChannelShift(p, w, 0, 0, 1, 1, 20, 1, True)
                  for p, w in enumerate((770, 750, 780))]
        reg = register_triplets.TripletResult(self.frames[0].stem, 0, shifts, "ok")
        with patch.object(register_triplets, "process_triplet", return_value=reg), patch.object(k_index, "k_index") as index:
            result = self.run_pipeline()
        self.assertEqual(result.code, "REGISTRATION_FAILED")
        index.assert_not_called()

    def test_no_valid_index_pixels(self):
        with patch.object(k_index, "k_index", return_value=(np.zeros(CALIBRATION_SHAPE), np.zeros(CALIBRATION_SHAPE, bool))):
            result = self.run_pipeline()
        self.assertEqual(result.code, "NO_VALID_INDEX_PIXELS")
        self.assertEqual(result.metrics, {})


class NumericTests(unittest.TestCase):
    def test_region_mask(self):
        mask, info = calibrated_region_mask((1088, 1456))
        self.assertEqual(info["bounds_xyxy"], [146, 109, 1310, 979])
        self.assertEqual(int(mask.sum()), 1164 * 870)
        self.assertFalse(mask[:109].any())
        self.assertFalse(mask[:, :146].any())
        self.assertTrue(mask[109:979, 146:1310].all())

    def test_decode_uint8_and_dark_uint16(self):
        with TemporaryDirectory() as directory:
            for dtype, scale in ((np.uint8, 1), (np.uint16, 256)):
                path = Path(directory) / f"{dtype.__name__}.png"
                data = np.array([[0, 1, 10, 200]], dtype=dtype)
                cv2.imwrite(str(path), data)
                np.testing.assert_allclose(k_index.read_channel(path), data.astype(np.float32) / scale)

    def test_index_formula_unchanged(self):
        low, high, on = np.array([30.]), np.array([60.]), np.array([75.])
        cont = k_index.continuum(low, high, 750, 770, 780)
        np.testing.assert_allclose(cont, (low + 2 * high) / 3)
        delta, valid = k_index.k_index(on, cont)
        np.testing.assert_allclose(delta, [0.5])
        self.assertTrue(valid.all())


if __name__ == "__main__":
    unittest.main()
