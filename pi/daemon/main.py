"""Capture daemon — Flask HTTP service on 127.0.0.1:8001.

Endpoints (Phase 4):
  GET    /healthz           liveness probe
  POST   /capture           run burst_count consecutive 3-channel bursts
  GET    /images            list image ids (newest first)
  GET    /images/<id>       serve the JPEG bytes
  DELETE /images/<id>       delete one
  DELETE /images            delete all
  GET    /disk              {total, used, free} bytes
  GET    /settings          full settings JSON
  PUT    /settings          partial update, validates, persists, reschedules timer

A background APScheduler runs the timer when settings.timer.enabled is true,
firing capture_bursts(burst_count) at settings.timer.interval_seconds. If a
capture is already in flight (manual or scheduled), the new attempt returns
BusyError (HTTP 409 for manual; silently dropped for scheduled).
"""
from __future__ import annotations
import atexit
import logging
import os
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify, request, send_file, abort

from .camera import BusyError, Cameras
from .store import ImageStore
from ..shared.settings import SettingsStore

STORE_ROOT = os.environ.get("KOENIG_STORE", str(Path.home() / "koenig_images"))
SETTINGS_PATH = os.environ.get("KOENIG_SETTINGS", str(Path.home() / ".koenig" / "settings.json"))
LISTEN_HOST = os.environ.get("KOENIG_DAEMON_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("KOENIG_DAEMON_PORT", "8001"))

log = logging.getLogger("koenig.daemon")

app = Flask(__name__)
store = ImageStore(STORE_ROOT)
settings = SettingsStore(SETTINGS_PATH)
cameras = Cameras(default_resolution=settings.resolution())


def _run_one_capture_cycle():
    """One full capture event: burst_count bursts at current settings."""
    n = settings.burst_count()
    return cameras.capture_bursts(
        n=n,
        path_fn_factory=lambda: store.burst_path_fn("jpg"),
        controls_for=settings.controls_for,
        resolution=settings.resolution(),
    )


# ---------- Scheduler ----------------------------------------------------
scheduler = BackgroundScheduler(daemon=True)
scheduler.start()
atexit.register(lambda: scheduler.shutdown(wait=False))

_TIMER_JOB_ID = "koenig-timer"


def _timer_tick():
    try:
        _run_one_capture_cycle()
    except BusyError:
        log.info("timer tick dropped: capture already in progress")
    except Exception:
        log.exception("timer tick failed")


def _apply_timer():
    """Sync the scheduler to current settings.timer."""
    cfg = settings.timer()
    try:
        scheduler.remove_job(_TIMER_JOB_ID)
    except Exception:
        pass
    if cfg.get("enabled") and cfg.get("interval_seconds", 0) > 0:
        scheduler.add_job(
            _timer_tick,
            "interval",
            seconds=cfg["interval_seconds"],
            id=_TIMER_JOB_ID,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )


_apply_timer()


# ---------- HTTP endpoints -----------------------------------------------
@app.get("/healthz")
def healthz():
    return jsonify(
        ok=True,
        store=str(store.root),
        settings=str(settings.path),
        timer_active=scheduler.get_job(_TIMER_JOB_ID) is not None,
    )


@app.post("/capture")
def capture():
    try:
        results = _run_one_capture_cycle()
    except BusyError as e:
        return jsonify(error=str(e), busy=True), 409
    return jsonify(captures=[
        {
            "port": r.port,
            "wavelength_nm": r.wavelength_nm,
            "id": r.path.name,
            "bytes": r.path.stat().st_size,
        }
        for r in results
    ])


@app.get("/images")
def list_images():
    return jsonify(images=store.list())


@app.get("/images/<image_id>")
def get_image(image_id):
    try:
        path = store.path_for(image_id)
    except (ValueError, FileNotFoundError):
        abort(404)
    return send_file(str(path), mimetype="image/jpeg")


@app.delete("/images/<image_id>")
def delete_image(image_id):
    try:
        store.delete(image_id)
    except (ValueError, FileNotFoundError):
        abort(404)
    return jsonify(deleted=image_id)


@app.delete("/images")
def clear_images():
    return jsonify(deleted=store.clear())


@app.get("/disk")
def disk():
    return jsonify(store.disk_usage())


@app.get("/settings")
def get_settings():
    return jsonify(settings.snapshot())


@app.put("/settings")
def update_settings():
    patch = request.get_json(force=True, silent=True) or {}
    try:
        updated = settings.update(patch)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    _apply_timer()
    return jsonify(updated)


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    app.run(host=LISTEN_HOST, port=LISTEN_PORT)


if __name__ == "__main__":
    main()
