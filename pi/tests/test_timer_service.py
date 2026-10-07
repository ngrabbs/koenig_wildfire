import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from apscheduler.schedulers.background import BackgroundScheduler
from pi.daemon.timer_service import TimerService
from pi.shared.settings import SettingsStore


class TimerTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.settings = SettingsStore(Path(temp.name) / 'settings.json')
        self.scheduler = BackgroundScheduler()
        self.scheduler.start()
        self.addCleanup(self.scheduler.shutdown)
        self.ticks = []
        self.timer = TimerService(self.settings, self.scheduler, lambda: self.ticks.append(1))

    def test_lifecycle(self):
        self.timer.reconcile()
        self.timer.set_interval(5)
        self.assertFalse(self.timer.snapshot()['active'])
        self.timer.start()
        job = self.scheduler.get_job(self.timer.JOB_ID)
        original_time = job.next_run_time
        self.assertEqual(self.ticks, [])
        self.timer.start()
        self.assertIs(job, self.scheduler.get_job(self.timer.JOB_ID))
        self.assertEqual(original_time, job.next_run_time)
        self.timer.set_interval(60)
        self.assertGreater(self.scheduler.get_job(self.timer.JOB_ID).next_run_time, original_time)
        self.timer.stop(); self.timer.stop()
        self.assertFalse(self.timer.snapshot()['active'])
        self.assertFalse(SettingsStore(self.settings.path).timer()['enabled'])

    def test_restart_restores_persisted_timer(self):
        self.timer.start()
        self.scheduler.remove_job(self.timer.JOB_ID)
        restored = TimerService(SettingsStore(self.settings.path), self.scheduler, lambda: None)
        restored.reconcile()
        self.assertTrue(restored.snapshot()['active'])

    def test_save_failure_does_not_publish_or_schedule(self):
        self.timer.set_interval(60)
        before = self.settings.snapshot(); disk = self.settings.path.read_bytes()
        with patch.object(Path, 'replace', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError): self.timer.start()
        self.assertEqual(before, self.settings.snapshot())
        self.assertEqual(disk, self.settings.path.read_bytes())
        self.assertFalse(self.timer.snapshot()['active'])

    def test_scheduler_failures_are_visible_and_retryable(self):
        with patch.object(self.scheduler, 'add_job', side_effect=RuntimeError('add failed')):
            with self.assertRaises(RuntimeError): self.timer.start()
        self.assertTrue(self.timer.snapshot()['enabled'])
        self.assertFalse(self.timer.snapshot()['active'])
        self.timer.start()
        with patch.object(self.scheduler, 'remove_job', side_effect=RuntimeError('remove failed')):
            with self.assertRaises(RuntimeError): self.timer.stop()
        self.assertFalse(self.timer.snapshot()['enabled'])
        self.assertTrue(self.timer.snapshot()['active'])
        self.timer.stop()
        self.assertFalse(self.timer.snapshot()['active'])

    def test_invalid_interval_preserves_state(self):
        before = self.settings.snapshot()
        for value in (4, 86401, True, 5.5):
            with self.assertRaises(ValueError): self.timer.set_interval(value)
        self.assertEqual(before, self.settings.snapshot())


    def test_failed_interval_change_can_be_reconciled(self):
        self.timer.start()
        with patch.object(self.scheduler, 'add_job', side_effect=RuntimeError('reschedule failed')):
            with self.assertRaises(RuntimeError): self.timer.set_interval(30)
        self.assertEqual(self.settings.timer()['interval_seconds'], 30)
        self.assertEqual(self.scheduler.get_job(self.timer.JOB_ID).trigger.interval.total_seconds(), 60)
        self.timer.start()
        self.assertEqual(self.scheduler.get_job(self.timer.JOB_ID).trigger.interval.total_seconds(), 30)

    def test_real_tick_is_owned_by_scheduler(self):
        import threading
        event = threading.Event()
        self.timer.tick = event.set
        self.timer.set_interval(5)
        self.timer.start()
        self.assertFalse(event.wait(.05))
        self.assertTrue(event.wait(7))
        self.timer.stop()

    def test_http_style_update_restarts_countdown(self):
        self.timer.start()
        old = self.scheduler.get_job(self.timer.JOB_ID)
        self.timer.update_settings({'shared': {'ExposureTime': 6000}}, restart=True)
        self.assertIsNot(old, self.scheduler.get_job(self.timer.JOB_ID))
