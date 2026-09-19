"""External trigger output for synchronising the three sensors.

    python3 -m pi.daemon.trigger --selftest      # 1 Hz, verify wiring
    python3 -m pi.daemon.trigger --pulse         # one pulse
    python3 -m pi.daemon.trigger --burst 10 --hz 5

READ THIS BEFORE BUILDING THE HARDWARE
--------------------------------------
This module drives the wire. It does not, on its own, give simultaneous
three-channel capture, and on the current hardware nothing can. Two separate
obstacles sit in front of that, and they are worth knowing before any
soldering happens.

**The multiplexer.** The payload is a Raspberry Pi 4, which has one CSI-2
receiver, behind an Arducam 4-port multiplexer. One sensor is connected to
the Pi at a time. Trigger all three together and all three do expose
together - but two of them read out into a mux port that is not routed
anywhere, and that data is gone. Simultaneous *exposure* is easy; it is
simultaneous *readout* that the hardware cannot do. Fixing it means one CSI
receiver per camera: three CM4s or Pi Zero 2s sharing this trigger line, or
some other multi-receiver arrangement. That is an architecture change, not
a wiring change.

**The driver.** The in-tree imx296 driver on this Pi exposes exposure,
gain, blanking and flips, and no trigger control of any kind. External
trigger mode needs InnoMaker's patched driver from
github.com/INNO-MAKER/cam-imx296raw-trigger. Until that is installed the
sensors ignore the pin entirely.

What this module is good for today is verifying a trigger harness before
either of those is solved - build the wiring, run --selftest, confirm on a
scope or an LED that every camera's trigger input sees a clean edge.

Wiring, for when it is time
---------------------------
One output drives all three XTRIG inputs in parallel. They are high-impedance
CMOS inputs drawing microamps, so fan-out is not a concern.

    BCM 21 (pin 40) ┬── 100R ── XTRIG cam0
                    ├── 100R ── XTRIG cam1
                    └── 100R ── XTRIG cam2

    GND (pin 39) ───┴── common ground to all three camera boards

Pins 39 and 40 are the last pair on the header, so the harness is two
adjacent wires off the corner.

- **Common ground is mandatory.** Without a shared return the input sees an
  undefined voltage and may trigger on noise or not at all. If a separate
  microcontroller generates the pulse, its ground must tie to the Pi's too.
- **No extra supply.** The cameras take power over their ribbons through the
  mux; the trigger line carries signal only.
- **3.3 V logic.** A 5 V Arduino over-drives the input and can damage it.
  Use a 3.3 V board - Pro Mini 3V3, Pico, ESP32 - or a level shifter.
- The series resistors damp reflections and limit current into a mis-wired
  input. 100R is a reasonable default; they are not strictly required on
  short leads.

A separate microcontroller is optional. Since a single edge reaches all three
sensors, whatever jitter the Pi's scheduler adds shifts all of them by the
same amount and cannot desynchronise them. A microcontroller buys precise
*period* for free-running capture, not better simultaneity.
"""

from __future__ import annotations

import argparse
import logging
import threading
import time

log = logging.getLogger(__name__)

# BCM 21 is header pin 40 - the corner. Pin 39 beside it is ground, so the
# whole harness is two adjacent pins, and being on the end it is findable by
# feel and unlikely to end up under a HAT.
#
# BCM 16 (pin 36) is the tested alternative; pass --pin 16. Its nearest
# ground is pin 34, one pin further along.
#
# Do NOT use 4, 17 or 18: the multiplexer overlay claims those as its
# camera-select lines (mux-gpios in payload-mux-4port), and BCM 6 is taken
# too. Claiming one fails with "GPIO busy" if you are lucky, and fights the
# mux if you are not.
DEFAULT_PIN = 21
DEFAULT_WIDTH_US = 100    # comfortably above the sensor's minimum
DEFAULT_ACTIVE_HIGH = True


class TriggerUnavailable(RuntimeError):
    """No usable GPIO backend, or the pin could not be claimed."""


class Trigger:
    """A single GPIO output wired to every sensor's trigger input.

    Pulse width is generated in Python, so it is accurate to roughly a
    millisecond and no better. That is fine for a trigger, where the edge is
    what the sensor acts on. It would not be fine for a mode that derives
    exposure time from the pulse width - use the sensor's own exposure
    control for that, not this.
    """

    def __init__(self, pin: int = DEFAULT_PIN, active_high: bool = DEFAULT_ACTIVE_HIGH):
        self.pin = pin
        self.active_high = active_high
        self._lock = threading.Lock()
        self._dev = None
        self._backend = None
        self._open()

    def _open(self) -> None:
        try:
            from gpiozero import DigitalOutputDevice
        except ImportError as exc:
            raise TriggerUnavailable(
                "gpiozero is not installed; cannot drive a trigger pin") from exc
        try:
            self._dev = DigitalOutputDevice(
                self.pin, active_high=self.active_high, initial_value=False)
            self._backend = "gpiozero"
        except Exception as exc:
            raise TriggerUnavailable(
                f"could not claim BCM pin {self.pin}: {exc}") from exc
        log.info("trigger ready on BCM %d (active %s)",
                 self.pin, "high" if self.active_high else "low")

    def pulse(self, width_us: int = DEFAULT_WIDTH_US) -> None:
        """Emit one pulse. All three sensors see the same edge."""
        with self._lock:
            self._dev.on()
            time.sleep(width_us / 1_000_000.0)
            self._dev.off()

    def burst(self, count: int, hz: float, width_us: int = DEFAULT_WIDTH_US) -> int:
        """Emit `count` pulses at `hz`. Returns the number actually sent.

        Period is held against a fixed start time rather than by sleeping a
        fixed amount each time round, so the pulses do not drift.
        """
        if count <= 0 or hz <= 0:
            return 0
        period = 1.0 / hz
        t0 = time.monotonic()
        sent = 0
        for i in range(count):
            target = t0 + i * period
            delay = target - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.pulse(width_us)
            sent += 1
        return sent

    def close(self) -> None:
        with self._lock:
            if self._dev is not None:
                try:
                    self._dev.off()
                    self._dev.close()
                except Exception:
                    log.exception("closing trigger pin failed")
                self._dev = None

    def __enter__(self) -> "Trigger":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pin", type=int, default=DEFAULT_PIN,
                    help=f"BCM pin number (default {DEFAULT_PIN})")
    ap.add_argument("--width-us", type=int, default=DEFAULT_WIDTH_US,
                    help=f"pulse width in microseconds (default {DEFAULT_WIDTH_US})")
    ap.add_argument("--active-low", action="store_true",
                    help="idle high, pulse low")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--pulse", action="store_true", help="send one pulse and exit")
    g.add_argument("--burst", type=int, metavar="N", help="send N pulses")
    g.add_argument("--selftest", action="store_true",
                   help="pulse at 1 Hz until interrupted, for scoping the harness")
    ap.add_argument("--hz", type=float, default=5.0, help="burst rate (default 5)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        trig = Trigger(args.pin, active_high=not args.active_low)
    except TriggerUnavailable as exc:
        print(f"trigger unavailable: {exc}")
        return 1

    with trig:
        if args.selftest:
            print(f"pulsing BCM {args.pin} at 1 Hz, {args.width_us} us wide. Ctrl-C to stop.")
            print("Scope each camera's trigger input in turn: every one should show")
            print("the same edge. If one is missing or rounded off, that is the lead.")
            n = 0
            try:
                while True:
                    trig.pulse(args.width_us)
                    n += 1
                    print(f"  pulse {n}", end="\r", flush=True)
                    time.sleep(1.0)
            except KeyboardInterrupt:
                print(f"\nstopped after {n} pulses")
        elif args.burst:
            t0 = time.monotonic()
            sent = trig.burst(args.burst, args.hz, args.width_us)
            dt = time.monotonic() - t0
            print(f"sent {sent} pulses in {dt:.3f} s "
                  f"({sent / max(dt, 1e-9):.2f} Hz actual, {args.hz} requested)")
        else:
            trig.pulse(args.width_us)
            print(f"one pulse on BCM {args.pin}, {args.width_us} us")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
