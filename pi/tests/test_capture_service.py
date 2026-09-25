"""Phase 1 acquisition tests; no camera, scheduler, or science dependencies.

Run: python -m unittest pi.tests.test_capture_service
"""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import atexit
import importlib.util
import os
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

from pi.daemon.capture_service import CaptureRequest, CaptureService
from pi.daemon.store import (ImageStore, TripletSelectionError,
                             parse_capture_filename, select_stored_triplet)
from pi.shared.settings import SettingsStore, set_supported_resolutions, supported_resolutions

STEM = "20260924_120000_123"
OTHER = "20260924_120001_123"


class FakeBusyError(RuntimeError):
    pass


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = ImageStore(self.root / "images")
        self.settings = SettingsStore(self.root / "settings.json")
        self.calls = []
        self.service = CaptureService(settings=self.settings, store=self.store,
                                      live_capture=self.capture, busy_error=FakeBusyError)

    def frame(self, port, wave, stem=STEM, ext="jpg"):
        path = self.store.root / f"{stem}_cam{port}_{wave}nm.{ext}"
        path.write_bytes(b"fixture: metadata-only validation")
        return path

    def triplet(self, stem=STEM):
        return [self.frame(p, w, stem) for p, w in enumerate((770, 750, 780))]

    def capture(self, **kwargs):
        self.calls.append(kwargs)
        path = kwargs["path_fn_factory"]()(SimpleNamespace(port=0, wavelength_nm=770))
        path.write_bytes(b"live frame")
        return [SimpleNamespace(path=path, port=0, wavelength_nm=770)]

    def test_parse_original_formats_and_mapping(self):
        for ext in ("jpg", "jpeg", "png", "tif", "tiff", "JPG"):
            ref = parse_capture_filename(Path(f"{STEM}_cam1_750nm.{ext}"))
            self.assertEqual((ref.stem, ref.port, ref.wavelength_nm), (STEM, 1, 750))

    def test_reject_invalid_filenames(self):
        for name in (f"{STEM}_cam0_770nm_aligned.png", f"{STEM}_composite.png",
                     f"{STEM}_cam3_770nm.jpg", f"{STEM}_cam0_077nm.jpg",
                     "20260230_120000_123_cam0_770nm.jpg",
                     f"{STEM}_cam0_770nm.jpg\n", "random.jpg"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                parse_capture_filename(name)

    def test_select_exact_event_and_ignore_derived_images(self):
        expected = self.triplet()
        self.triplet(OTHER)
        (self.store.root / f"{STEM}_cam0_770nm_aligned.png").write_bytes(b"derived")
        got = select_stored_triplet(self.store.root, STEM)
        self.assertEqual([f.path for f in got], expected)

    def test_missing_channel_cannot_be_borrowed_from_other_event(self):
        self.frame(0, 750)
        self.frame(1, 770)
        self.frame(2, 780, OTHER)
        with self.assertRaises(TripletSelectionError) as caught:
            select_stored_triplet(self.store.root, STEM)
        self.assertEqual(caught.exception.code, "INCOMPLETE_TRIPLET")

    def test_duplicate_extension_is_ambiguous(self):
        self.triplet()
        self.frame(0, 770, ext="png")
        with self.assertRaises(TripletSelectionError) as caught:
            select_stored_triplet(self.store.root, STEM)
        self.assertEqual(caught.exception.code, "AMBIGUOUS_TRIPLET")

    def test_duplicate_wavelength_is_ambiguous(self):
        for port, wave in enumerate((750, 770, 770)):
            self.frame(port, wave)
        with self.assertRaises(TripletSelectionError):
            select_stored_triplet(self.store.root, STEM)

    def test_invalid_or_traversing_stem(self):
        for stem in ("../" + STEM, "", "20260230_120000_123"):
            with self.subTest(stem=stem), self.assertRaises(TripletSelectionError):
                select_stored_triplet(self.store.root, stem)

    def test_live_success_preserves_capture_fields(self):
        result = self.service.run(CaptureRequest())
        self.assertEqual(result.status, "success")
        self.assertEqual(result.decision, "NOT_CALIBRATED")
        self.assertEqual(result.events[0].processing_status, "SKIPPED_INCOMPLETE_TRIPLET")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(set(result.to_dict()["captures"][0]), {"port", "wavelength_nm", "id", "bytes"})

    def test_live_uses_one_settings_snapshot_and_filename_wavelength(self):
        self.settings.update({"wavelengths": {"0": 750, "1": 770, "2": 780}})

        def changing_capture(**kwargs):
            self.settings.update({"shared": {"ExposureTime": 9000}, "rotations": {"0": 180},
                                  "wavelengths": {"0": 770, "1": 750, "2": 780}})
            self.assertEqual(kwargs["controls_for"](0)["ExposureTime"], 5000)
            self.assertEqual(kwargs["rotation_for"](0), 0)
            return self.capture(**kwargs)

        self.service.live_capture = changing_capture
        result = self.service.run(CaptureRequest())
        self.assertEqual(result.events[0].captures[0].wavelength_nm, 750)

    def test_simulation_success_does_not_capture_or_modify_inputs(self):
        paths = self.triplet()
        original = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
        self.settings.update({"burst_count": 5})
        result = self.service.run(CaptureRequest(source="simulation", simulation_stem=STEM))
        self.assertEqual(result.status, "success")
        self.assertEqual(result.decision, "NOT_CALIBRATED")
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0].processing_status, "READY_TRIPLET")
        self.assertEqual(self.calls, [])
        self.assertEqual(original, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths})

    def test_simulation_selection_error_is_structured(self):
        result = self.service.run(CaptureRequest(source="simulation", simulation_stem=STEM))
        self.assertEqual((result.status, result.failure.code), ("error", "INCOMPLETE_TRIPLET"))
        self.assertEqual(result.decision, "NOT_CALIBRATED")

    def test_backend_busy_and_error_release_service_lock(self):
        for exc, status in ((FakeBusyError("focus active"), "busy"), (RuntimeError("read failed"), "error")):
            def fail(**kwargs):
                raise exc
            self.service.live_capture = fail
            result = self.service.run(CaptureRequest())
            self.assertEqual(result.status, status)
            self.assertEqual(result.decision, "NOT_CALIBRATED")
            self.assertEqual(result.failure.message, str(exc))
            self.service.live_capture = self.capture
            self.assertEqual(self.service.run(CaptureRequest()).status, "success")

    def test_concurrent_request_is_busy(self):
        entered, release = threading.Event(), threading.Event()
        results = []

        def blocking_capture(**kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test timed out")
            return self.capture(**kwargs)

        self.service.live_capture = blocking_capture
        thread = threading.Thread(target=lambda: results.append(self.service.run(CaptureRequest())))
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            busy = self.service.run(CaptureRequest())
            self.assertEqual(busy.status, "busy")
            self.assertTrue(busy.to_dict()["busy"])
            self.assertEqual(busy.decision, "NOT_CALIBRATED")
        finally:
            release.set()
            thread.join(5)
        self.assertEqual(results[0].status, "success")

    def test_invalid_requests(self):
        for request in (CaptureRequest(source="unknown"), CaptureRequest(source="simulation"),
                        CaptureRequest(simulation_stem=STEM)):
            result = self.service.run(request)
            self.assertEqual(result.status, "error")
            self.assertEqual(result.decision, "NOT_CALIBRATED")
            self.assertEqual(self.calls, [])


class AdapterTests(unittest.TestCase):
    """Import the real Flask routes with camera and scheduler dependencies stubbed."""

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        original_sizes = supported_resolutions()
        self.addCleanup(set_supported_resolutions, original_sizes)
        self.camera = MagicMock()
        self.camera.available_sizes.return_value = [(1920, 1080)]
        self.constructor = MagicMock(return_value=self.camera)
        scheduler = MagicMock()
        stubs = {
            "pi.daemon.camera_jetson": SimpleNamespace(Cameras=self.constructor, BusyError=FakeBusyError),
            "apscheduler": SimpleNamespace(),
            "apscheduler.schedulers": SimpleNamespace(),
            "apscheduler.schedulers.background": SimpleNamespace(BackgroundScheduler=MagicMock(return_value=scheduler)),
        }
        path = Path(__file__).resolve().parents[1] / "daemon" / "main.py"
        spec = importlib.util.spec_from_file_location("pi.daemon.main", path)
        module = importlib.util.module_from_spec(spec)
        stubs["pi.daemon.main"] = module
        with patch.dict(sys.modules, stubs), patch.dict(os.environ, {
            "PAYLOAD_STORE": str(root / "images"), "PAYLOAD_SETTINGS": str(root / "settings.json"),
        }), patch.object(atexit, "register"):
            spec.loader.exec_module(module)
        self.daemon = module
        self.client = module.app.test_client()

        def capture(**kwargs):
            path = kwargs["path_fn_factory"]()(SimpleNamespace(port=0, wavelength_nm=770))
            path.write_bytes(b"frame")
            return [SimpleNamespace(path=path)]

        self.camera.capture_bursts.side_effect = capture

    def test_http_live_uses_existing_instance(self):
        response = self.client.post("/capture")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["decision"], "NOT_CALIBRATED")
        self.assertEqual(len(response.json["captures"]), 1)
        self.constructor.assert_called_once()
        self.camera.capture_bursts.assert_called_once()

    def test_http_busy_and_error(self):
        for error, code in ((FakeBusyError("busy"), 409), (RuntimeError("read failed"), 500)):
            self.camera.capture_bursts.side_effect = error
            response = self.client.post("/capture")
            self.assertEqual(response.status_code, code)
            self.assertEqual(response.json["decision"], "NOT_CALIBRATED")
            self.assertIn("error", response.json)
            if code == 409:
                self.assertTrue(response.json["busy"])

    def test_http_simulation(self):
        for port, wave in enumerate((750, 770, 780)):
            (self.daemon.store.root / f"{STEM}_cam{port}_{wave}nm.jpg").write_bytes(b"stored")
        response = self.client.post("/capture", json={"source": "simulation", "simulation_stem": STEM})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["events"][0]["processing_status"], "READY_TRIPLET")
        self.assertEqual(response.json["decision"], "NOT_CALIBRATED")
        self.camera.capture_bursts.assert_not_called()

    def test_http_invalid_input(self):
        for body in ([], {"source": "simulation"}, {"source": "invalid"}, {"extra": True}):
            response = self.client.post("/capture", json=body)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json["decision"], "NOT_CALIBRATED")
        response = self.client.post("/capture", data="{", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["decision"], "NOT_CALIBRATED")
        self.camera.capture_bursts.assert_not_called()

    def test_timer_uses_same_service(self):
        with patch.object(self.daemon.capture_service, "run", wraps=self.daemon.capture_service.run) as run:
            self.daemon._timer_tick()
        run.assert_called_once_with(CaptureRequest(caller="timer"))
        self.camera.capture_bursts.assert_called_once()
        self.constructor.assert_called_once()


if __name__ == "__main__":
    unittest.main()
