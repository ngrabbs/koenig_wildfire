"""Single owner of persisted timer configuration and scheduler transitions."""
import threading
from ..shared.can_protocol import validate_interval


class TimerService:
    JOB_ID = "payload-timer"

    def __init__(self, settings, scheduler, tick):
        self.settings, self.scheduler, self.tick = settings, scheduler, tick
        self._lock = threading.RLock()

    def _active(self):
        job = self.scheduler.get_job(self.JOB_ID)
        return job is not None and job.next_run_time is not None

    def snapshot(self):
        with self._lock:
            return {**self.settings.timer(), "active": self._active()}

    def _reconcile(self, restart=False):
        cfg = self.settings.timer()
        job = self.scheduler.get_job(self.JOB_ID)
        if not cfg["enabled"]:
            if job is not None:
                self.scheduler.remove_job(self.JOB_ID)
        elif (restart or not self._active()
              or job.trigger.interval.total_seconds() != cfg["interval_seconds"]):
            self.scheduler.add_job(self.tick, "interval", seconds=cfg["interval_seconds"],
                                   id=self.JOB_ID, coalesce=True, max_instances=1,
                                   replace_existing=True)

    def reconcile(self):
        with self._lock:
            self._reconcile()

    def update_settings(self, patch, *, restart=False):
        # Persist first: a save failure cannot change the scheduler. Scheduler
        # failure is surfaced; persisted intent remains visible for recovery.
        with self._lock:
            before = self.settings.timer()
            updated = self.settings.update(patch)
            self._reconcile(restart or before["interval_seconds"] != updated["timer"]["interval_seconds"])
            return updated

    def set_interval(self, seconds):
        return self.update_settings({"timer": {"interval_seconds": validate_interval(seconds)}}, restart=True)

    def start(self):
        return self.update_settings({"timer": {"enabled": True}})

    def stop(self):
        return self.update_settings({"timer": {"enabled": False}})
