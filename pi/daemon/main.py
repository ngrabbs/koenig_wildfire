"""Capture daemon — Flask HTTP service on 127.0.0.1:8001.

Endpoints (Phase 4):
  GET    /healthz           liveness probe
  POST   /capture           run one three-channel burst, return per-channel metadata
  GET    /images            list image ids (newest first)
  GET    /images/<id>       serve the JPEG bytes
  DELETE /images/<id>       delete one
  DELETE /images            delete all
  GET    /disk              {total, used, free} bytes
  GET    /settings          full settings JSON
  PUT    /settings          partial update, validates, persists
"""
from __future__ import annotations
from flask import Flask, jsonify, request, send_file, abort
from pathlib import Path
import os

from .camera import Cameras
from .store import ImageStore
from ..shared.settings import SettingsStore

STORE_ROOT = os.environ.get("KOENIG_STORE", str(Path.home() / "koenig_images"))
SETTINGS_PATH = os.environ.get("KOENIG_SETTINGS", str(Path.home() / ".koenig" / "settings.json"))
LISTEN_HOST = os.environ.get("KOENIG_DAEMON_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("KOENIG_DAEMON_PORT", "8001"))

app = Flask(__name__)
store = ImageStore(STORE_ROOT)
settings = SettingsStore(SETTINGS_PATH)
cameras = Cameras(default_resolution=settings.resolution())


@app.get("/healthz")
def healthz():
    return jsonify(ok=True, store=str(store.root), settings=str(settings.path))


@app.post("/capture")
def capture():
    results = cameras.capture_burst(
        path_for=store.burst_path_fn("jpg"),
        controls_for=settings.controls_for,
        resolution=settings.resolution(),
    )
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
    return jsonify(updated)


def main():
    app.run(host=LISTEN_HOST, port=LISTEN_PORT)


if __name__ == "__main__":
    main()
