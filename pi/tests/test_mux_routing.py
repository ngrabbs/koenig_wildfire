"""Regression test: holding all three cameras open breaks mux routing.

    ssh pi@<payload>
    cd ~/code/koenig_wildfire
    sudo systemctl stop payload-daemon
    python3 pi/tests/test_mux_routing.py
    sudo systemctl start payload-daemon

Runs on the payload only - it needs the real cameras.

THE ANSWER IS ALREADY KNOWN: it fails. Measured 19 Sep 2026. This exists so
the failure can be reproduced rather than re-argued, and so that any future
change to the capture loop can be checked the right way.

The temptation is strong. The payload opens and closes one camera per
channel, and that costs about 1.4 s of a 2.1 s triplet - close() alone is
roughly half - while a repeat grab from an already-open camera is 60 ms.
Holding all three open and starting them one at a time measures as a 2.2x
speedup, 2.93 s down to 1.34 s.

It is fake. The cycle is faster because it reads one camera three times and
skips two mux switches:

    captured   vs ref0   vs ref1   vs ref2   verdict
        cam0     0.164   -0.290    1.000   WRONG - looks like cam2
        cam1     0.164   -0.290    1.000   WRONG - looks like cam2
        cam2     0.160   -0.290    0.999   OK

    cam0 vs cam1: 1.000   <- identical frames

Opening a Picamera2 enables its video-mux link; start() is not what does it.
The last camera opened wins and every read comes from it. This is the same
duplicate-frame bug found in August, which shipped precisely because timings
looked excellent while it was happening.

The structure of this test is the point: capture a known-good reference set
one-at-a-time, then correlate every candidate frame against it. A frame that
best-matches the wrong reference is the bug. Never accept a capture-loop
change on timing evidence alone.
"""
import logging
import time

import numpy as np
from picamera2 import Picamera2

logging.getLogger("picamera2").setLevel(logging.ERROR)

SIZE = (1456, 1088)
CTRL = {"ExposureTime": 20000, "AnalogueGain": 1.0, "AeEnable": False}
PORTS = (0, 1, 2)


def cfg(cam):
    cam.configure(cam.create_still_configuration(
        main={"size": SIZE, "format": "RGB888"}))
    cam.set_controls(CTRL)


def gray_small(a):
    g = a.astype(np.float32).mean(axis=2) if a.ndim == 3 else a.astype(np.float32)
    return g[::8, ::8]


def ncc(a, b):
    a = a - a.mean()
    b = b - b.mean()
    d = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / d) if d > 0 else 0.0


def reference():
    """One camera at a time - the way the payload does it today."""
    frames, t0 = {}, time.monotonic()
    for p in PORTS:
        cam = Picamera2(camera_num=p)
        cfg(cam)
        cam.start()
        frames[p] = gray_small(cam.capture_array())
        cam.stop()
        cam.close()
    return frames, time.monotonic() - t0


def all_open():
    """All three open; start, grab and stop them one at a time."""
    cams = {}
    try:
        t_open = time.monotonic()
        for p in PORTS:
            cams[p] = Picamera2(camera_num=p)
            cfg(cams[p])
        t_open = time.monotonic() - t_open

        frames, per = {}, {}
        t0 = time.monotonic()
        for p in PORTS:
            t1 = time.monotonic()
            cams[p].start()
            frames[p] = gray_small(cams[p].capture_array())
            cams[p].stop()
            per[p] = time.monotonic() - t1
        t_cycle = time.monotonic() - t0
        return frames, t_open, t_cycle, per
    finally:
        for c in cams.values():
            try:
                c.close()
            except Exception:
                pass


def main():
    print("reference pass (one at a time, known good)...")
    ref, t_ref = reference()
    print(f"  {t_ref:.3f} s for three channels\n")

    print("all-open pass (three open, started one at a time)...")
    try:
        test, t_open, t_cycle, per = all_open()
    except Exception as exc:
        print(f"  FAILED to even run: {exc}")
        return 1
    print(f"  opening all three: {t_open:.3f} s  (one-off, could be done at boot)")
    print(f"  the capture cycle itself: {t_cycle:.3f} s  "
          + "  ".join(f"cam{p}={per[p]:.3f}" for p in PORTS))
    print()

    print("ROUTING CHECK - each captured frame vs every reference frame")
    print("the best match must be the camera it claims to be\n")
    print(f"{'captured':>10} " + " ".join(f"{'vs ref%d' % p:>9}" for p in PORTS)
          + "   verdict")
    ok = True
    for p in PORTS:
        scores = [ncc(test[p], ref[q]) for q in PORTS]
        best = int(np.argmax(scores))
        good = best == p
        ok &= good
        print(f"{'cam%d' % p:>10} " + " ".join(f"{s:9.3f}" for s in scores)
              + f"   {'OK' if good else 'WRONG - looks like cam%d' % best}")

    # If routing breaks by duplication, the captured frames are identical to
    # each other. Worth reporting separately - it is the signature we saw.
    dup = [(a, b, ncc(test[a], test[b]))
           for a in PORTS for b in PORTS if a < b]
    print("\ncaptured frames vs each other (near 1.000 = duplicates):")
    for a, b, s in dup:
        print(f"  cam{a} vs cam{b}: {s:.3f}")

    print()
    if ok and all(s < 0.98 for _, _, s in dup):
        print(f"PASS - routing held. Cycle {t_cycle:.3f} s against "
              f"{t_ref:.3f} s one-at-a-time.")
    else:
        print("FAIL - routing broke. Holding cameras open is not safe; "
              "the payload must keep opening one at a time.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
