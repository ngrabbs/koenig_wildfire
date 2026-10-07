# Koenig IHU ↔ Jetson interface — Phase 5

Audience: Austin / IHU implementation. Linux SocketCAN on `vcan0` only at
this stage; no physical-bus bitrate, wiring, or termination assumptions.
Jetson node ID is `0x20`. Request CAN ID is `0x2A0`; reply CAN ID is `0x320`.
These are 11-bit standard data frames, classic CAN (not CAN FD or RTR).
An IHU node ID is not assigned by this interface; do not infer one from 0x2A0.
The existing filter mask is `0xC00007FF`.

**Service `0xF0` is a PROVISIONAL KOENIG PROJECT ALLOCATION**, defined by
`PROVISIONAL_KOENIG_STATUS_SERVICE`. It is not a standardized SpaceCAN service.
Austin/IHU must confirm this allocation before freezing the interface.

## Commands

All bytes below are hexadecimal. Each row is one CAN frame, not a segmented
packet. Keep the exact unsegmented `00 00` prefix. Service 08, subtype 01 is
function invocation; byte 4 is the application function ID.

| Function | ID | Request bytes | DLC |
|---|---|---|---|
| CAPTURE_NOW | 01 | `00 00 08 01 01` | 5 |
| SET_TIMER_INTERVAL | 02 | `00 00 08 01 02 HH MM LL` | 8 |
| START_TIMER | 03 | `00 00 08 01 03` | 5 |
| STOP_TIMER | 04 | `00 00 08 01 04` | 5 |
| GET_STATUS | 05 | `00 00 08 01 05` | 5 |

Interval is unsigned 24-bit **big-endian seconds**:
`seconds = (HH << 16) | (MM << 8) | LL`. Accept 5 through 86,400 inclusive;
reject rather than clamp other values. 60 seconds = `00 00 3C`;
86,400 seconds = `01 51 80`. Arguments, padding, and extra bytes are forbidden
on the other commands. Frame DLC is significant.

```sh
cansend vcan0 2A0#0000080101
cansend vcan0 2A0#000008010200003C
cansend vcan0 2A0#0000080103
cansend vcan0 2A0#0000080104
cansend vcan0 2A0#0000080105
```

## Verification / lifecycle

Service-01 replies on 0x320 have DLC 6:

| Meaning | Bytes |
|---|---|
| Acceptance success | `00 00 01 01 08 01` |
| Acceptance failure | `00 00 01 02 08 01` |
| Completion success | `00 00 01 07 08 01` |
| Completion failure | `00 00 01 08 08 01` |

Validation precedes acceptance. If acceptance cannot be sent, execution does
not start. Accepted commands execute once in this listener and then send
completion. No duplicate suppression/retry protocol is provided. Execution,
persistence, and scheduler failures produce completion failure. Verification
does not contain detailed error codes; consult daemon logs.

GET_STATUS sends acceptance, timer report, operation report, completion.
Failure collecting or sending status may leave partial reports followed by
completion failure (or no completion if transport has failed).

CAPTURE_NOW remains one normal **live capture cycle**, including the current
burst count and settings, using the sole camera owner and shared CaptureService.
Acquisition failure/busy yields completion failure. Successful single-camera
acquisition yields completion success with processing skipped/incomplete.
Completion success never means fire detected. Decision remains NOT_CALIBRATED.

Wrong CAN ID, flagged frame, unsupported segmentation prefix, and an unusable
header shorter than four bytes are ignored. Classic payloads longer than eight
bytes are ignored. A usable header with unsupported service/subtype gets
acceptance failure echoing that received service/subtype in the final two bytes.
Service 08/01 with missing/unknown function, wrong DLC, extra bytes, or invalid
interval gets acceptance failure only. No execution follows rejection.

## Timer ownership and persistence

Jetson owns APScheduler and recurring capture. The IHU sends configuration,
not repeated capture triggers. HTTP and CAN settings changes use TimerService.
HTTP PUT /settings retains its existing restart-countdown behavior; CAN START
is separately idempotent.

- SET_TIMER_INTERVAL persists the interval, retaining enabled state. If enabled,
  it recreates the recurring job; the next tick is one new interval later.
- START_TIMER persists enabled=true. First tick occurs after one interval.
  Already-running with matching configuration is idempotent and retains its
  countdown. A missing/stale job is reconciled.
- STOP_TIMER persists enabled=false and removes future scheduling. Repeating
  STOP is successful. A dispatched capture may finish; STOP is not cancellation.
- Enabled configuration survives daemon restart/reboot. Startup recreates the
  job with a fresh countdown. Missed downtime captures are not replayed.
- Ticks call the same CaptureService as HTTP and CAN, with source live and caller
  timer. They emit **no unsolicited Service-01 replies**. Busy ticks are dropped.
  Scheduler uses coalescing and one concurrent instance per timer job.
- Persistence happens before scheduler reconciliation. A failed save leaves
  old in-memory settings and scheduling intact. A scheduler failure is reported;
  persisted intent may differ from actual scheduling until retry/restart.
  In particular an old active interval may remain after a rescheduling failure.
  GET_STATUS reports the configured interval, not the old job's interval.
  Retry timer control to reconcile; do not treat completion failure as success.

## Status, DLC 8 per report

Reports are two independent unsegmented frames on 0x320:

```text
Timer:     00 00 F0 01 FF HH MM LL
Operation: 00 00 F0 02 CC SS PP DD
```

Timer flags FF:

| Bit | Meaning |
|---|---|
| 0 | Persisted timer enabled |
| 1 | Recurring scheduler job has a next run scheduled |
| 2 | CaptureService operation in progress |
| 3 | Focus session active |
| 4–7 | Reserved; zero |

HH MM LL is the configured interval, using the command's u24 encoding.
Busy is a snapshot, not a reservation; focus separately explains camera
unavailability. The daemon normally runs its scheduler continuously.

| Field | Codes |
|---|---|
| CC source | 00 none; 01 live; 02 simulation |
| SS result | 00 none; 01 success; 02 busy; 03 error |
| PP processing | 00 unavailable/no result; 01 incomplete triplet; 02 waiting for calibration; 03 processed with limitations; 04 processing failed; 05 incompatible source; 06 mixed event statuses |
| DD decision | 00 NOT_CALIBRATED; all others reserved |

Operation describes the latest finished CaptureService attempt from any caller,
including busy/error attempts. It is not the latest timer-control command.
Initially all four bytes are zero. A request failing source validation reports
source none. Requests without processing events report PP=00. Multiple events
with differing processing statuses report PP=06. This is in-memory history;
daemon restart clears it. GET_STATUS does not change that history.
There is no global live/simulation mode: CC is the last request's source.
CAN/timer always request live; HTTP can request simulation.
Reports are built from one collection of observations; timer, capture and focus
can change during collection. They are not a globally atomic reservation.

Example: enabled, active, idle timer at 60 seconds and last successful incomplete
live capture:

```text
00 00 F0 01 03 00 00 3C
00 00 F0 02 01 01 01 00
```

## IHU client rules / limitations

There are no transaction IDs or function IDs in verification replies. Use one
IHU controller and **one outstanding command**; do not interleave simulator and
manual cansend requests. The listener is serial: CAN capture blocks reception
of subsequent commands until completion; frames may wait in bounded kernel
buffers. GET_STATUS/STOP are not immediate during a CAN capture. HTTP/timer
activity can instead cause a CAN capture to return busy.

A timeout means outcome unknown, not cancellation or failure. Never retry
CAPTURE_NOW automatically. The simulator closes an uncertain session and refuses
further commands on it; closing/reopening alone cannot eliminate delayed replies.
Reconcile/quiesce the previous operation before beginning another session.
No delivery, exactly-once, response-deadline, or immediate-stop guarantee exists.

## Simulator and validation

From the repository root on Linux:

```sh
sudo modprobe vcan
sudo ip link add dev vcan0 type vcan   # only if absent
sudo ip link set vcan0 up
PAYLOAD_CAN_ENABLED=1 python3 -m pi.daemon.main
# Separate terminal:
python3 -m tools.ihu_simulator --interface vcan0 --timeout 30
```

Menu: capture, set interval, start, stop, status, quit. TX/RX show CAN ID, DLC,
hex bytes; replies also show decoded fields. Interface and timeout are selectable.
No camera or recurring timer is created by the client. Only run one daemon/camera
owner; if the service already runs, use it rather than starting another process.

Recommended Jetson validation after reviewing/transferring the exact patch:

1. Save current settings and inventory JPEGs; use one daemon owner with CAN enabled.
2. GET_STATUS; verify initial reports and unchanged capture history on repeated polls.
3. CAPTURE_NOW; verify exact acceptance/completion bytes, new JPEG, incomplete science.
4. Set 5 seconds; START; verify first capture after the interval and repeated captures
   after the simulator exits. Check no unsolicited verification frames.
5. Repeat START and verify countdown is not reset; change interval and verify reset.
6. STOP and verify no new ticks after any dispatched capture finishes; repeat STOP.
7. With a disposable settings file, restart enabled/disabled daemon configurations
   and verify persistence. Restore the prior settings and stop test scheduling.
8. Stop the daemon listener before running vcan regression tests (they bind their
   own listener and use fake acquisition). Run protocol, timer, simulator, CAN,
   CaptureService, ProcessingService and web UI tests plus `git diff --check`.

Keep science algorithms, calibration and camera backend unchanged. The provisional
status allocation and serial-command limitations must be reviewed with Austin.
