"""Capture daemon — Flask HTTP service on 127.0.0.1:8001.

Endpoints (Phase 2):
  GET    /healthz           liveness probe
  POST   /capture           take one picture, return its id
  GET    /images            list image ids (newest first)
  GET    /images/<id>       serve the JPEG bytes
  DELETE /images/<id>       delete one
  DELETE /images            delete all
  GET    /disk              {total, used, free} bytes
"""
from __future__ import annotations
from flask import Flask, jsonify, send_file, abort
from pathlib import Path
import os

from .camera import Camera
from .store import ImageStore

STORE_ROOT = os.environ.get("KOENIG_STORE", str(Path.home() / "koenig_images"))
LISTEN_HOST = os.environ.get("KOENIG_DAEMON_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("KOENIG_DAEMON_PORT", "8001"))

app = Flask(__name__)
store = ImageStore(STORE_ROOT)
camera = Camera()


@app.get("/healthz")
def healthz():
    return jsonify(ok=True, store=str(store.root))


@app.post("/capture")
def capture():
    path = store.next_path("jpg")
    camera.capture(path)
    return jsonify(id=path.name, bytes=path.stat().st_size)


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


def main():
    app.run(host=LISTEN_HOST, port=LISTEN_PORT)


if __name__ == "__main__":
    main()
