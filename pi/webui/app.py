"""Web UI — Flask on 0.0.0.0:8000. Talks to the capture daemon at
127.0.0.1:8001 and proxies images so the daemon stays loopback-only.
"""
from __future__ import annotations
from flask import Flask, render_template, redirect, url_for, Response, abort
import json
import os
import urllib.error
import urllib.request

DAEMON_URL = os.environ.get("KOENIG_DAEMON_URL", "http://127.0.0.1:8001")
LISTEN_HOST = os.environ.get("KOENIG_WEBUI_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("KOENIG_WEBUI_PORT", "8000"))

app = Flask(__name__)


def _request(path: str, method: str = "GET") -> tuple[bytes, str]:
    req = urllib.request.Request(
        f"{DAEMON_URL}{path}",
        method=method,
        data=b"" if method in ("POST", "PUT") else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read(), r.headers.get("Content-Type", "application/json")
    except urllib.error.HTTPError as e:
        abort(e.code)


def daemon_json(path: str, method: str = "GET"):
    body, _ = _request(path, method)
    return json.loads(body)


@app.get("/")
def index():
    images = daemon_json("/images")["images"]
    return render_template("index.html", images=images)


@app.post("/capture")
def capture():
    daemon_json("/capture", "POST")
    return redirect(url_for("index"))


@app.post("/delete/<image_id>")
def delete(image_id: str):
    daemon_json(f"/images/{image_id}", "DELETE")
    return redirect(url_for("index"))


@app.post("/clear")
def clear():
    daemon_json("/images", "DELETE")
    return redirect(url_for("index"))


@app.get("/img/<image_id>")
def img(image_id: str):
    body, ctype = _request(f"/images/{image_id}")
    return Response(body, mimetype=ctype)


def main():
    app.run(host=LISTEN_HOST, port=LISTEN_PORT)


if __name__ == "__main__":
    main()
