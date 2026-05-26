"""Web UI — Flask on 0.0.0.0:8000. Talks to the capture daemon at
127.0.0.1:8001 and proxies images so the daemon stays loopback-only.
"""
from __future__ import annotations
from flask import Flask, render_template, redirect, request, url_for, Response, abort, flash, stream_with_context
import json
import os
import urllib.error
import urllib.request

from ..shared.settings import CONTROL_SCHEMA, RESOLUTIONS, BURST_COUNT_MAX, ROTATIONS_ALLOWED

DAEMON_URL = os.environ.get("KOENIG_DAEMON_URL", "http://127.0.0.1:8001")
LISTEN_HOST = os.environ.get("KOENIG_WEBUI_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("KOENIG_WEBUI_PORT", "8000"))

app = Flask(__name__)
# flash() needs a secret. UI is single-Pi local-network only, fixed value is fine.
app.secret_key = os.environ.get("KOENIG_WEBUI_SECRET", "koenig-wildfire-ui")

CAMERA_PORTS = [0, 1, 2]
CONTROL_NAMES = list(CONTROL_SCHEMA.keys())
BOOL_CONTROLS = {n for n, spec in CONTROL_SCHEMA.items() if spec["type"] is bool}

# Display wavelength per port. Matches CHANNELS in pi/daemon/camera.py.
PORT_WAVELENGTH = {0: 762, 1: 766, 2: 770}


def _request(path: str, method: str = "GET", body_bytes: bytes | None = None,
             content_type: str | None = None):
    headers = {}
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(
        f"{DAEMON_URL}{path}",
        method=method,
        data=body_bytes if body_bytes is not None else (b"" if method in ("POST", "PUT") else None),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.read(), r.headers.get("Content-Type", "application/json"), r.status
    except urllib.error.HTTPError as e:
        return e.read(), e.headers.get("Content-Type", "application/json"), e.code


def daemon_json(path: str, method: str = "GET", body=None):
    body_bytes = json.dumps(body).encode() if body is not None else None
    raw, _, status = _request(path, method, body_bytes,
                              content_type="application/json" if body is not None else None)
    if status >= 400:
        raise RuntimeError(f"daemon {method} {path}: {status} {raw[:200]!r}")
    return json.loads(raw)


def _form_to_controls(form, prefix=""):
    """Pull a controls dict out of a form, given an optional field-name prefix.

    Bool controls: the field is present in the form only when the checkbox is
    checked. Numeric controls: parsed by the daemon-side validator, we pass
    strings through.
    """
    out: dict = {}
    for name in CONTROL_NAMES:
        field = f"{prefix}{name}"
        if name in BOOL_CONTROLS:
            out[name] = field in form
        elif field in form:
            value = form.get(field, "").strip()
            if value != "":
                out[name] = value  # daemon coerces + validates
    return out


def _split_interval(seconds: int) -> tuple[int, str]:
    """Pick a friendly (value, unit) display for an interval given in seconds."""
    if seconds and seconds % 60 == 0:
        return seconds // 60, "minutes"
    return seconds, "seconds"


@app.get("/")
def index():
    images = daemon_json("/images")["images"]
    try:
        current_settings = daemon_json("/settings")
    except RuntimeError:
        current_settings = None
    interval_value, interval_unit = (60, "seconds")
    if current_settings:
        interval_value, interval_unit = _split_interval(
            current_settings.get("timer", {}).get("interval_seconds", 60)
        )
    return render_template(
        "index.html",
        images=images,
        settings=current_settings,
        control_schema=CONTROL_SCHEMA,
        control_names=CONTROL_NAMES,
        bool_controls=BOOL_CONTROLS,
        resolutions=RESOLUTIONS,
        rotations_allowed=ROTATIONS_ALLOWED,
        camera_ports=CAMERA_PORTS,
        port_wavelengths=PORT_WAVELENGTH,
        burst_count_max=BURST_COUNT_MAX,
        interval_value=interval_value,
        interval_unit=interval_unit,
    )


@app.post("/capture")
def capture():
    raw, _, status = _request("/capture", "POST")
    if status == 409:
        flash("Capture is already in progress — try again in a moment.", "error")
    elif status >= 400:
        flash(f"Capture failed (HTTP {status}).", "error")
    return redirect(url_for("index"))


@app.post("/delete/<image_id>")
def delete(image_id: str):
    daemon_json(f"/images/{image_id}", "DELETE")
    return redirect(url_for("index"))


@app.post("/clear")
def clear():
    daemon_json("/images", "DELETE")
    return redirect(url_for("index"))


@app.post("/settings")
def update_settings():
    form = request.form
    patch: dict = {
        "shared": _form_to_controls(form, prefix="shared_"),
        "advanced_mode": "advanced_mode" in form,
        "per_camera": {
            str(port): _form_to_controls(form, prefix=f"cam{port}_")
            for port in CAMERA_PORTS
        },
    }
    if "resolution" in form:
        patch["resolution"] = form["resolution"]
    if "burst_count" in form and form["burst_count"].strip():
        patch["burst_count"] = form["burst_count"]
    rotations: dict[str, str] = {}
    for port in CAMERA_PORTS:
        key = f"rotation_{port}"
        if key in form and form[key].strip():
            rotations[str(port)] = form[key]
    if rotations:
        patch["rotations"] = rotations
    if "timer_value" in form and form["timer_value"].strip():
        try:
            value = int(form["timer_value"])
            unit = form.get("timer_unit", "seconds")
            seconds = value * (60 if unit == "minutes" else 1)
            patch["timer"] = {
                "enabled": "timer_enabled" in form,
                "interval_seconds": seconds,
            }
        except ValueError:
            flash("Timer interval must be a whole number.", "error")
            return redirect(url_for("index"))

    try:
        daemon_json("/settings", "PUT", body=patch)
        flash("Settings saved.", "ok")
    except RuntimeError as e:
        flash(f"Save failed: {e}", "error")
    return redirect(url_for("index"))


@app.get("/img/<image_id>")
def img(image_id: str):
    raw, ctype, status = _request(f"/images/{image_id}")
    if status >= 400:
        abort(status)
    return Response(raw, mimetype=ctype)


@app.get("/focus/<int:port>")
def focus_page(port: int):
    if port not in CAMERA_PORTS:
        abort(404)
    raw, _, status = _request(f"/focus/{port}/start", "POST")
    if status == 409:
        flash("Another capture or focus is already running.", "error")
        return redirect(url_for("index"))
    if status >= 400:
        flash(f"Could not start focus mode (HTTP {status}).", "error")
        return redirect(url_for("index"))
    return render_template(
        "focus.html",
        port=port,
        wavelength=PORT_WAVELENGTH.get(port, "?"),
    )


@app.get("/focus/stream")
def focus_stream():
    """Proxy the daemon's long-lived MJPEG stream to the browser. When the
    browser disconnects, our generator stops, urlopen() closes, the daemon's
    generator hits GeneratorExit and stops focus → camera released."""
    req = urllib.request.Request(f"{DAEMON_URL}/focus/stream")
    try:
        resp = urllib.request.urlopen(req, timeout=None)
    except urllib.error.HTTPError as e:
        abort(e.code)

    ctype = resp.headers.get("Content-Type", "multipart/x-mixed-replace; boundary=frame")

    def gen():
        try:
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                resp.close()
            except Exception:
                pass

    return Response(stream_with_context(gen()), mimetype=ctype)


@app.post("/focus/stop")
def focus_stop():
    _request("/focus/stop", "POST")
    return redirect(url_for("index"))


@app.post("/system/reboot")
def system_reboot():
    _request("/system/reboot", "POST")
    return render_template("system_status.html", action="reboot")


@app.post("/system/shutdown")
def system_shutdown():
    _request("/system/shutdown", "POST")
    return render_template("system_status.html", action="shutdown")


def main():
    app.run(host=LISTEN_HOST, port=LISTEN_PORT, threaded=True)


if __name__ == "__main__":
    main()
