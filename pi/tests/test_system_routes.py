"""Reboot and shutdown must not fire twice when the page is refreshed.

Regression test for a field bug: the reboot and shutdown handlers used to
render their status page directly from the POST, which left the browser
sitting on the POST URL. The natural thing to do while waiting - refresh to
see whether the Pi is back - re-submitted the form and rebooted it again.
The status page even said "refresh this page". An operator could get stuck
cycling the payload with no obvious way out.

Runs with pytest, or directly:  python pi/tests/test_system_routes.py
No hardware needed - the daemon call is stubbed out and counted.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from pi.webui import app as webui  # noqa: E402


def _client_counting_daemon_calls():
    """Test client whose daemon requests are recorded instead of sent."""
    calls: list[tuple[str, str]] = []

    def fake_request(path, method="GET", body_bytes=None, content_type=None):
        calls.append((method, path))
        return b"{}", "application/json", 200

    webui._request = fake_request
    return webui.app.test_client(), calls


def test_reboot_is_not_repeated_by_refreshing():
    client, calls = _client_counting_daemon_calls()

    posted = client.post("/system/reboot")
    assert posted.status_code == 303, (
        "reboot must redirect, not render from the POST - rendering leaves the "
        "browser on the POST URL where a refresh re-submits it")
    target = posted.headers["Location"]

    for _ in range(3):
        assert client.get(target).status_code == 200

    assert calls == [("POST", "/system/reboot")], (
        f"the Pi was rebooted {len(calls)} times, expected once")


def test_shutdown_is_not_repeated_by_refreshing():
    client, calls = _client_counting_daemon_calls()

    posted = client.post("/system/shutdown")
    assert posted.status_code == 303
    target = posted.headers["Location"]

    for _ in range(3):
        client.get(target)

    assert calls == [("POST", "/system/shutdown")], (
        f"the Pi was shut down {len(calls)} times, expected once")


def test_status_page_does_not_tell_the_operator_to_refresh():
    client, _ = _client_counting_daemon_calls()
    body = client.get("/system/status?action=reboot").get_data(as_text=True)
    assert "refresh this page" not in body.lower(), (
        "the page must not instruct the action that used to re-trigger it")


def test_unknown_action_falls_back_safely():
    client, _ = _client_counting_daemon_calls()
    body = client.get("/system/status?action=<script>alert(1)</script>")
    assert body.status_code == 200
    assert "<script>" not in body.get_data(as_text=True)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL  {name}\n      {exc}")
    print("\nall passed" if not failures else f"\n{failures} failed")
    raise SystemExit(1 if failures else 0)
