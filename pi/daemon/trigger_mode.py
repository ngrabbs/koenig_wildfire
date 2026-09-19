"""Put an IMX296 into external-trigger mode, or take it out again.

    sudo python3 -m pi.daemon.trigger_mode show
    sudo python3 -m pi.daemon.trigger_mode on  --cam 1
    sudo python3 -m pi.daemon.trigger_mode off --cam 1

No patched kernel driver is needed. Trigger mode on this sensor is six I2C
register writes, from InnoMaker's documented sequence
(github.com/INNO-MAKER/cam-imx296raw-trigger). The in-tree driver never sees
it and does not need to.

WHAT TRIGGER MODE ACTUALLY DOES
-------------------------------
One pulse on XTR produces **one exposure, which is then read out
immediately**. The sensor does not hold the frame waiting to be collected.

This matters because the obvious plan - trigger all three at once, then cycle
the multiplexer round to pick up three stored frames - cannot work. The
IMX296 is a global-shutter sensor: every pixel transfers its charge to a
shielded storage node at the same instant, which is what makes the shutter
global, and readout then streams row by row out of those storage nodes. The
storage node holds charge for the duration of that readout, not until
somebody asks for it. It is a stage in the readout path, not a frame buffer,
and there is no command to read a frame that was exposed earlier.

So on the current hardware, triggering all three together means all three
dump onto their MIPI outputs simultaneously, the mux forwards one, and the
other two are lost. Simultaneity needs one CSI receiver per camera. This
module is the piece that will make that work when the receivers exist, and
in the meantime it is worth having for two reasons: it de-risks that
architecture cheaply, and an armed sensor that fires on demand may allow a
much shorter gap between channels than the present open/configure/close
cycle.

STATUS AS OF 19 SEP 2026 - HALF WORKING
---------------------------------------
Arming works and is verified. An armed sensor holds off frames indefinitely;
disarmed, the same camera returns a frame in 0.10 s. That is the sensor
genuinely honouring trigger mode, reproduced across many runs. It needed no
patched kernel driver, which was the part expected to be hard.

**No pulse has yet produced a frame.** Driving XTR does not release an armed
sensor. Ruled out so far, all with the controls passing in the same run:

    polarity      active-high and active-low        both: no frame
    pulse width   100 us, 1 ms, 10 ms, 50 ms        all:  no frame

A 500x spread of widths in both polarities says the failure is upstream of
anything software reaches. The unverified hop is the wire between the Pi
header and the camera's XTR pad - a read-back on the GPIO confirms the Pi end
drives correctly, but an unconnected far end looks exactly the same. Check
continuity from header pin 40 to XTR and pin 39 to the board ground, with
power off, and confirm which pad is XTR against 1-4Images/Conection.png in
InnoMaker's repo. The trigger and strobe headers sit next to each other and
have already been confused once.

A trap worth knowing, because it produced a false positive here: opening the
camera clears the trigger register - 0x30AE reads 0x01 after arming and 0x00
after start(). The sensor nevertheless stays gated, so the register is not
authoritative once the pipeline is up. What that does mean is that a capture
run before an arming will have disarmed the sensor, and the next capture then
free-runs. A "triggered" frame arriving in the same ~0.1 s as an untriggered
one is not a triggered frame. Re-arm immediately before every attempt, and
treat free-run latency as the signature of an unarmed sensor.

CAUTION
-------
A sensor in trigger mode produces NO frames until pulsed. Enable it on a
camera whose XTR line is not actually wired and every capture from that
channel will time out. `off` restores normal free-running behaviour, and so
does a power cycle.

The stream must be stopped when these registers are written - InnoMaker are
explicit about it and the mode does not latch otherwise. Stop the daemon
first:  sudo systemctl stop payload-daemon
"""

from __future__ import annotations

import argparse
import sys
import time

try:
    from smbus2 import SMBus, i2c_msg
except ImportError:
    sys.exit("needs smbus2:  sudo apt install python3-smbus2")

SENSOR_ADDR = 0x1A

# Camera port -> muxed I2C bus, as the PCA9544 registers them. Confirmed
# against dmesg: "imx296 23-001a", "24-001a", "25-001a".
PORT_BUS = {0: 23, 1: 24, 2: 25}

REG_STANDBY = 0x3000
REG_HOLD = 0x300A
REG_ENABLE = 0x300B
REG_TRIGGER = 0x30AE


def read_reg(bus: int, reg: int) -> int:
    with SMBus(bus) as b:
        w = i2c_msg.write(SENSOR_ADDR, [(reg >> 8) & 0xFF, reg & 0xFF])
        r = i2c_msg.read(SENSOR_ADDR, 1)
        b.i2c_rdwr(w, r)
        return list(r)[0]


def write_reg(bus: int, reg: int, val: int) -> None:
    with SMBus(bus) as b:
        b.i2c_rdwr(i2c_msg.write(
            SENSOR_ADDR, [(reg >> 8) & 0xFF, reg & 0xFF, val & 0xFF]))


def set_trigger(bus: int, enable: bool) -> None:
    """InnoMaker's sequence. The standby toggle in the middle is what commits
    the mode change; the register-hold pair makes the whole thing atomic."""
    v = 0x01 if enable else 0x00
    write_reg(bus, REG_HOLD, 0x01)
    write_reg(bus, REG_STANDBY, 0x01)
    time.sleep(0.01)
    write_reg(bus, REG_STANDBY, 0x00)
    time.sleep(0.01)
    write_reg(bus, REG_ENABLE, v)
    write_reg(bus, REG_TRIGGER, v)
    write_reg(bus, REG_HOLD, 0x00)


def show(ports) -> int:
    print(f"{'camera':>14} {'bus':>5} {'standby':>8} {'hold':>6} "
          f"{'enable':>7} {'TRIGGER':>8}")
    bad = 0
    for p in ports:
        bus = PORT_BUS[p]
        try:
            sb = read_reg(bus, REG_STANDBY)
            hd = read_reg(bus, REG_HOLD)
            en = read_reg(bus, REG_ENABLE)
            tg = read_reg(bus, REG_TRIGGER)
        except Exception as exc:
            print(f"{'cam%d' % p:>14} {bus:>5}   unreadable: {exc}")
            bad += 1
            continue
        state = "ARMED - needs XTR pulses" if tg else "free-run"
        hexes = [f"0x{v:02X}" for v in (sb, hd, en, tg)]
        print(f"{'cam%d' % p:>14} {bus:>5} {hexes[0]:>8} {hexes[1]:>6} "
              f"{hexes[2]:>7} {hexes[3]:>8}  {state}")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("on", "off", "show"))
    ap.add_argument("--cam", type=int, choices=(0, 1, 2), action="append",
                    help="camera port; repeatable. Default: all three for "
                         "'show', required for 'on'/'off'")
    args = ap.parse_args()

    if args.action == "show":
        return show(args.cam or sorted(PORT_BUS))

    if not args.cam:
        return ap.error("'on' and 'off' need at least one --cam")

    want = args.action == "on"
    for p in args.cam:
        bus = PORT_BUS[p]
        try:
            set_trigger(bus, want)
            got = read_reg(bus, REG_TRIGGER)
        except Exception as exc:
            print(f"cam{p} (i2c-{bus}): FAILED - {exc}")
            return 1
        ok = bool(got) == want
        print(f"cam{p} (i2c-{bus}): trigger register now 0x{got:02X} "
              f"{'- OK' if ok else '- DID NOT TAKE'}")
        if not ok:
            print("  The stream was probably still running. Stop the daemon "
                  "and retry:  sudo systemctl stop payload-daemon")
            return 1
    if want:
        print("\nThese cameras will now produce NO frames until XTR is pulsed.")
        print("Pulse with:  python3 -m pi.daemon.trigger --selftest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
