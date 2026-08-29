"""Three-camera capture through the Arducam v2.2 mux.

Phase 3: each click captures one frame from each channel, sequentially.
Phase 4a: per-burst settings via picamera2.set_controls(); resolution
  changes trigger reconfigure.
Phase 4b: capture_bursts(n) runs N consecutive 3-channel bursts under
  one lock; non-blocking acquire raises BusyError so manual + timer
  paths can't stack.
Phase 5a: start_focus(port) / stop_focus() / iter_frames() implement a
  live MJPEG stream for one camera at a time. Uses the same lock —
  while focus is active, captures get BusyError.
Post-TL-002: _single_burst splits frame grab from JPEG encode so encoding
  no longer sits between two channels. Flight data showed 1-2 s between
  the first and last channel of a triplet, which is the dominant source of
  channel misregistration. See docs/channel_registration.md.

The kernel's video-mux + pca954x drivers handle the physical port
switching when we start a Picamera2 instance on a specific camera_num.
"""
from __future__ import annotations
from pathlib import Path
from typing import Callable, NamedTuple, Optional
import io
import logging
import threading


log = logging.getLogger("koenig.cameras")


class BusyError(RuntimeError):
    """Raised when a capture or focus is requested while one is already running."""


class Channel(NamedTuple):
    port: int
    wavelength_nm: int


# Port → intended filter wavelength. Without filters bolted on, all three
# channels see the same scene; the labels are aspirational. Move this into
# settings.json if/when filter mapping ever needs to vary per deployment.
CHANNELS: list[Channel] = [
    Channel(port=0, wavelength_nm=762),
    Channel(port=1, wavelength_nm=766),
    Channel(port=2, wavelength_nm=770),
]


def channel_for(port: int) -> Optional[Channel]:
    for ch in CHANNELS:
        if ch.port == port:
            return ch
    return None


class CaptureResult(NamedTuple):
    port: int
    wavelength_nm: int
    path: Path


ControlsFn = Callable[[int], dict]
RotationFn = Callable[[int], int]

# Default preview resolution for focus mode. High enough to see edge
# sharpness, low enough to stream smoothly over wifi.
FOCUS_PREVIEW_SIZE = (1332, 990)


def _encode_jpeg(frame, path: Path, quality: int = 90) -> None:
    """Write a captured array to JPEG.

    Kept separate so the grab loop stays free of encoding work — the point
    of splitting the two is to keep encode time out of the gap between
    channels, where it turns into inter-channel misregistration.
    """
    from PIL import Image
    img = Image.fromarray(frame)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.save(str(path), "JPEG", quality=quality, optimize=True)


def _transform_for(rotation: int):
    """Build a libcamera Transform for the given rotation in degrees.
    Only 0 and 180 are supported (hardware hflip/vflip)."""
    from libcamera import Transform
    if rotation == 180:
        return Transform(hflip=1, vflip=1)
    return Transform()


class _StreamingOutput(io.BufferedIOBase):
    """Picamera2 writes JPEG frames here; readers block on the condition
    until a new frame is available. Tracks an encoder-side frame count so
    we can tell from logs whether a black-screen incident is "encoder
    never produced frames" vs "frames produced but never delivered."
    """

    def __init__(self):
        super().__init__()
        self.frame: Optional[bytes] = None
        self.encoder_frame_count: int = 0
        self.condition = threading.Condition()

    def write(self, buf):  # picamera2 calls this for every JPEG frame
        with self.condition:
            self.frame = bytes(buf)
            self.encoder_frame_count += 1
            self.condition.notify_all()


class Cameras:
    """Sequential capture across the three mux channels.

    **Exactly one Picamera2 instance exists at a time.** This is not an
    optimisation, it is a correctness requirement of the video-mux. Holding
    three instances open simultaneously breaks mux routing: libcamera enables
    one video-mux link when the instances are configured and does not
    re-negotiate when a different instance starts, so every capture reads
    whichever link happens to be enabled — while still labelling the frame
    with the camera we asked for. The result is three identical images
    carrying three correct-looking per-channel filenames and EXIF paths,
    which is a silent, very convincing data-corruption bug.

    Measured on the rig with three IMX296 (NCC between channels of one
    capture):

        three instances open   +0.999  +0.958  +0.965   (all the same frame)
        one instance at a time -0.281  +0.515  -0.208   (genuinely different)

    So each capture opens the camera, grabs, and closes it again. That costs
    roughly 50 ms per channel in open+configure, which is the price of the
    frames actually being what they claim to be.

    The CSI controller is shared across mux ports, so only one camera can be
    streaming at a time regardless. Capture and focus use one shared lock
    (`_busy`) so they cannot run concurrently.
    """

    def __init__(self, default_resolution: tuple[int, int] = (4056, 3040)):
        from picamera2 import Picamera2  # lazy: dev hosts without picamera2 can still import
        self._Picamera2 = Picamera2
        # Per-port record of controls libcamera rejected, so the warning is
        # logged once rather than on every capture.
        self._dropped_controls: dict[int, set[str]] = {}
        self._size_warned: Optional[tuple] = None
        self._available_sizes: list[tuple[int, int]] = []
        self._default_resolution = default_resolution

        self._available_sizes = self._read_sensor_sizes()

        self._busy = threading.Lock()
        # Guards the focus session state transition. stop_focus can be called
        # concurrently by the /focus/stop handler and by the streaming
        # generator's cleanup when the client disconnects; without this both
        # would run the teardown and both would release _busy, freeing a lock
        # a later capture is holding ("release unlocked lock").
        self._focus_state_lock = threading.Lock()
        self._prime()
        # Focus session state. The focus camera is the one instance held open
        # for the duration of a focus session; nothing else may be open then.
        self._focus_cam = None
        self._focus_port: Optional[int] = None
        self._focus_output: Optional[_StreamingOutput] = None

    # ---------- open / close ----------
    def _open(self, port: int):
        """Open and return a Picamera2 for one port. Caller must close it.

        Never hold two of these at once — see the class docstring.
        """
        return self._Picamera2(camera_num=port)

    @staticmethod
    def _close(p) -> None:
        for step in ("stop_recording", "stop", "close"):
            try:
                getattr(p, step)()
            except Exception:
                pass

    # ---------- configuration helpers ----------
    def _configure_still(self, p, size: tuple[int, int], rotation: int) -> None:
        cfg = p.create_still_configuration(
            main={"size": self._usable_size(size)},
            transform=_transform_for(rotation),
        )
        p.configure(cfg)

    def _configure_video(self, p, size: tuple[int, int], rotation: int) -> None:
        cfg = p.create_video_configuration(
            main={"size": size},
            transform=_transform_for(rotation),
        )
        p.configure(cfg)

    def _prime(self) -> None:
        """Run one start/stop on each camera at daemon startup.

        The first camera use after a cold boot is unreliable: the pipeline
        occasionally comes up with `Failed to queue buffer 0: Broken pipe`,
        and the capture then blocks forever holding `_busy`. Every later
        request gets 409 and the operator has no way back except a reboot —
        in the field, with students who don't have ssh, that is the payload
        bricked for the session.

        Reproduced twice in five cold boots on 2026-08-29. It is the same
        family as the focus black-screen-on-first-use quirk, which commit
        986887b fixed the same way: do the flaky first use up front, at
        startup, where nobody is waiting on it and a failure is logged
        rather than hung on.

        Deliberately best-effort. If priming fails the daemon still starts —
        a camera that can't prime will report its own error on first capture,
        which is more useful than refusing to boot.
        """
        for ch in CHANNELS:
            p = None
            try:
                p = self._open(ch.port)
                self._configure_still(p, self._usable_size(self._default_resolution,
                                                           warn=False), 0)
                p.start()
                p.stop()
                log.info("primed camera %d", ch.port)
            except Exception:
                log.exception("failed to prime camera %d (continuing)", ch.port)
            finally:
                if p is not None:
                    self._close(p)

    def _read_sensor_sizes(self) -> list[tuple[int, int]]:
        """Sizes libcamera reports for the fitted cameras, largest first.

        The supported-resolution list used to be hardcoded to the IMX477's
        modes. That silently assumed one sensor: an IMX296 has exactly one
        mode, 1456x1088, which the hardcoded list rejected outright, so the
        settings API answered 400 and the sensor could not be used at all.
        """
        sizes: list[tuple[int, int]] = []
        p = None
        try:
            p = self._open(CHANNELS[0].port)
            for mode in p.sensor_modes:
                size = tuple(int(v) for v in mode["size"])
                if size not in sizes:
                    sizes.append(size)
        except Exception:
            log.exception("could not read sensor modes; "
                          "falling back to the configured resolution list")
            return []
        finally:
            if p is not None:
                self._close(p)
        sizes.sort(key=lambda wh: wh[0] * wh[1], reverse=True)
        log.info("sensor modes available: %s",
                 ", ".join(f"{w}x{h}" for w, h in sizes))
        return sizes

    def available_sizes(self) -> list[tuple[int, int]]:
        return list(self._available_sizes)

    def _usable_size(self, size: tuple[int, int], warn: bool = True) -> tuple[int, int]:
        """Clamp a requested size to something this sensor actually has.

        A settings.json written for one sensor should not stop the daemon
        working after a sensor swap. Falls back to the largest available
        mode and says so, rather than failing the capture.
        """
        if not self._available_sizes or tuple(size) in self._available_sizes:
            return size
        fallback = self._available_sizes[0]
        if warn and self._size_warned != (tuple(size), fallback):
            log.warning("resolution %dx%d is not a mode this sensor offers; "
                        "using %dx%d instead",
                        size[0], size[1], fallback[0], fallback[1])
            self._size_warned = (tuple(size), fallback)
        return fallback

    def _supported_controls(self, p, port: int, controls: dict) -> dict:
        """Drop controls this camera doesn't advertise.

        Not every sensor exposes every control, and passing an unsupported
        one makes picamera2 raise rather than ignore it, failing the whole
        capture. The IMX296 is the case that surfaced this: it is a
        monochrome sensor, so there is no Bayer array, no white balance and
        no saturation, and libcamera advertises neither AwbEnable nor
        Saturation. With settings.json carrying the IMX477 control set, every
        capture died with "Control AwbEnable is not advertised by libcamera".

        Filtering against what the camera actually offers keeps one settings
        file working across sensors, which matters because the shared/
        per-camera settings schema is sensor-agnostic by design.
        """
        try:
            available = set(p.camera_controls)
        except Exception:
            log.exception("could not read camera_controls for port %d; "
                          "passing controls through unfiltered", port)
            return controls

        dropped = {k for k in controls if k not in available}
        if dropped and self._dropped_controls.get(port) != dropped:
            log.warning("camera %d does not support %s - ignoring "
                        "(normal for a mono sensor)",
                        port, ", ".join(sorted(dropped)))
            self._dropped_controls[port] = dropped
        return {k: v for k, v in controls.items() if k in available}

    # ---------- capture ----------
    def _single_burst(
        self,
        path_for: Callable[[Channel], Path],
        controls_for: Optional[ControlsFn],
        resolution: Optional[tuple[int, int]],
        rotation_for: Optional[RotationFn],
    ) -> list[CaptureResult]:
        """Capture one frame per channel, in two phases.

        Phase 1 grabs all three frames back to back. Phase 2 encodes them to
        JPEG afterwards. The split matters because the three channels are the
        same scene at different wavelengths, and anything that happens between
        two grabs is time for the aircraft to move — misregistration we have
        to correct later. JPEG encoding is ~50 ms per frame at 2028x1520, so
        doing it inline put ~100 ms of pure CPU work in the middle of the
        window we are trying to shrink. Encoding after the last grab costs the
        same total time but keeps it out of the inter-channel gap.

        The grab MUST copy the frame out, not hold a capture_request() to save
        later. All three cameras share one CSI receiver, so a request held
        across another camera's start()/stop() no longer refers to its own
        frame: every channel ends up saving the last one captured, producing
        three identical images that still carry correct per-channel filenames
        and EXIF. capture_array() copies into numpy, which costs a little time
        but is the only safe option here.

        See docs/channel_registration.md.
        """
        # Phase 1 — grab. One camera open at a time; see the class docstring.
        grabbed: list[tuple[Channel, Path, "object"]] = []
        try:
            for ch in CHANNELS:
                desired_rot = rotation_for(ch.port) if rotation_for else 0
                size = self._usable_size(resolution or self._default_resolution)
                p = self._open(ch.port)
                try:
                    self._configure_still(p, size, desired_rot)
                    if controls_for is not None:
                        wanted = self._supported_controls(
                            p, ch.port, controls_for(ch.port))
                        if wanted:
                            p.set_controls(wanted)
                    p.start()
                    try:
                        frame = p.capture_array()
                    finally:
                        p.stop()
                finally:
                    self._close(p)
                grabbed.append((ch, path_for(ch), frame))

            # Phase 2 — encode. Off the inter-channel path.
            results: list[CaptureResult] = []
            for ch, path, frame in grabbed:
                _encode_jpeg(frame, path)
                results.append(CaptureResult(ch.port, ch.wavelength_nm, path))
            return results
        finally:
            grabbed.clear()

    def capture_bursts(
        self,
        n: int,
        path_fn_factory: Callable[[], Callable[[Channel], Path]],
        controls_for: Optional[ControlsFn] = None,
        resolution: Optional[tuple[int, int]] = None,
        rotation_for: Optional[RotationFn] = None,
    ) -> list[CaptureResult]:
        """Run N consecutive 3-channel bursts. path_fn_factory is called once
        per burst to produce a fresh (channel)->Path closure — that's how each
        burst gets a unique timestamp prefix instead of overwriting the last.
        Raises BusyError if another capture or focus is already running.
        """
        if n < 1:
            raise ValueError("n must be >= 1")
        if not self._busy.acquire(blocking=False):
            raise BusyError("capture already in progress")
        try:
            all_results: list[CaptureResult] = []
            for _ in range(n):
                path_for = path_fn_factory()
                all_results.extend(
                    self._single_burst(path_for, controls_for, resolution, rotation_for)
                )
            return all_results
        finally:
            self._busy.release()

    # ---------- focus ----------
    def focus_port(self) -> Optional[int]:
        return self._focus_port

    def start_focus(self, port: int, controls: Optional[dict] = None,
                    rotation: int = 0) -> None:
        """Acquire the camera lock and start an MJPEG stream from `port`.

        Raises BusyError if a capture or another focus is in progress, and
        ValueError if `port` isn't one of our channels.
        """
        if channel_for(port) is None:
            raise ValueError(f"unknown port: {port}")
        if not self._busy.acquire(blocking=False):
            raise BusyError("capture or focus already in progress")
        try:
            from picamera2.encoders import MJPEGEncoder
            from picamera2.outputs import FileOutput
            # Open fresh for this session. Nothing else may be open while a
            # focus stream runs - see the class docstring on mux routing.
            p = self._open(port)
            self._focus_cam = p
            self._configure_video(p, FOCUS_PREVIEW_SIZE, rotation)
            if controls:
                wanted = self._supported_controls(p, port, controls)
                if wanted:
                    p.set_controls(wanted)
            # Prime the camera with a throwaway start/stop. Without this, the
            # first start_recording after daemon boot occasionally fires up
            # the encoder but the IMX477 pipeline never produces frames
            # (= black focus stream). A regular Capture click fixed it
            # because capture does its own start/stop. This cycles the
            # pipeline once before the actual encoder run.
            try:
                p.start()
                p.stop()
            except Exception as e:
                log.warning("focus prime start/stop on port %d failed: %s", port, e)
            output = _StreamingOutput()
            p.start_recording(MJPEGEncoder(), FileOutput(output))
            log.info("focus started on port %d (%dx%d, rotation=%d)",
                     port, FOCUS_PREVIEW_SIZE[0], FOCUS_PREVIEW_SIZE[1], rotation)
            self._focus_port = port
            self._focus_output = output
        except Exception:
            if self._focus_cam is not None:
                self._close(self._focus_cam)
                self._focus_cam = None
            self._busy.release()
            raise

    def stop_focus(self) -> None:
        """End any active focus session, restore the camera, release the lock.
        Safe to call when no focus is active and safe to call multiple times."""
        # Claim the session inside the guard: whichever caller gets here
        # first takes ownership of the teardown and the single _busy release,
        # and any concurrent caller returns immediately.
        with self._focus_state_lock:
            if self._focus_port is None:
                return
            port = self._focus_port
            p = self._focus_cam
            self._focus_port = None
            self._focus_cam = None
            self._focus_output = None

        if p is None:
            self._busy.release()
            return
        try:
            p.stop_recording()
        except RuntimeError as e:
            # picamera2 stops the encoder internally when the consumer
            # disconnects; our explicit stop then sees "Encoder already
            # stopped". Make sure the camera itself stopped though.
            log.debug("stop_recording on port %d: %s", port, e)
            try:
                p.stop()
            except Exception:
                pass
        except Exception:
            log.exception("stop_recording on port %d failed", port)
        # Close it: leaving the focus instance open would keep its video-mux
        # link enabled and every later capture would read this camera.
        self._close(p)
        self._busy.release()

    def iter_frames(self, timeout: float = 2.0):
        """Yield raw JPEG bytes from the active focus stream. Stops when
        focus is no longer active or the consumer disconnects."""
        output = self._focus_output
        if output is None:
            return
        delivered = 0
        consecutive_silent = 0
        while True:
            with output.condition:
                got = output.condition.wait(timeout=timeout)
                # If stop_focus() ran, our captured output won't match the new state.
                if self._focus_output is not output:
                    log.info("focus stream ended after %d frames delivered (encoder produced %d)",
                             delivered, output.encoder_frame_count)
                    return
                if not got:
                    consecutive_silent += 1
                    if consecutive_silent in (1, 3, 6):
                        log.warning(
                            "focus stream stalled: %ds with no new frame "
                            "(encoder produced %d, delivered %d)",
                            int(consecutive_silent * timeout),
                            output.encoder_frame_count, delivered,
                        )
                    continue
                consecutive_silent = 0
                frame = output.frame
            if frame:
                delivered += 1
                yield frame

    def close(self) -> None:
        self.stop_focus()
        with self._busy:
            if self._focus_cam is not None:
                self._close(self._focus_cam)
                self._focus_cam = None