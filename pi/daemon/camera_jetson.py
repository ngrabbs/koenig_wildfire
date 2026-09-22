from __future__ import annotations

from pathlib import Path
from typing import Callable, NamedTuple, Optional
import logging
import threading

import cv2

log = logging.getLogger("payload.cameras")

class BusyError(RuntimeError):
    pass

class Channel(NamedTuple):
    port: int
    wavelength_nm: int

CHANNELS = [
    Channel(port=0, wavelength_nm=770),
]

class CaptureResult(NamedTuple):
    port: int
    wavelength_nm: int
    path: Path

ControlsFn = Callable[[int], dict]
RotationFn = Callable[[int], int]

class Cameras:
    def __init__(self, default_resolution=(1920, 1080)):
        self._default_resolution = default_resolution
        self._available_sizes = [
            (3840, 2160),
            (1920, 1080),
        ]
        self._busy = threading.Lock()
        self._focus_port = None
        self._focus_cap = None
        
    def available_sizes(self):
        return list(self._available_sizes)
        
    def _pipeline(self, width: int, height: int, fps: int):
        return (
            "nvarguscamerasrc sensor-id=0 ! "
            f"video/x-raw(memory:NVMM),width={width},height={height},framerate={fps}/1 ! "
            "nvvidconv ! "
            "video/x-raw,format=BGRx ! "
            "videoconvert ! "
            "video/x-raw,format=BGR ! "
            "appsink drop=true max-buffers=1 sync=false"
        )
    def _open(self, size):
        width, height = size
        
        fps = 60 if (width, height) == (1920, 1080) else 30
        
        pipeline = self._pipeline(width, height, fps)
        
        cap = cv2.VideoCapture(
            pipeline,
            cv2.CAP_GSTREAMER,
        )
        
        if not cap.isOpened():
            raise RuntimeError("Could not open Jetson IMX477 camera")
            
        return cap
        
    def _capture_one(self, size):
        cap = self._open(size)
        
        try:
            ok, frame = cap.read()
            
            if not ok or frame is None:
                raise RuntimeError("Failed to read frame from Jetson camera")
                
            return frame
            
        finally:
            cap.release()
            
    def capture_bursts(
        self,
        n: int,
        path_fn_factory,
        controls_for: Optional[ControlsFn] = None,
        resolution=None,
        rotation_for: Optional[RotationFn] = None,
    ):
        if n < 1:
            raise ValueError("n must be >= 1")
            
        if not self._busy.acquire(blocking=False):
            raise BusyError("capture already in progress")
            
        try:
            results = []
            
            size = resolution or self._default_resolution
            
            if tuple(size) not in self._available_sizes:
                size = self._available_sizes[0]
                
            for _ in range(n):
                path_for = path_fn_factory()
                channel = CHANNELS[0]
                
                frame = self._capture_one(size)
                
                rotation = (
                    rotation_for(channel.port)
                    if rotation_for is not None
                    else 0
                )
                
                if rotation == 180:
                    frame = cv2.rotate(
                        frame,
                        cv2.ROTATE_180,
                    )
                    
                path = path_for(channel)
                
                if not cv2.imwrite(str(path), frame):
                    raise RuntimeError(
                        f"Failed to save image to {path}"
                    )
                results.append(
                    CaptureResult(
                        channel.port,
                        channel.wavelength_nm,
                        path,
                    )
                )
                
            return results
        
        finally:
            self._busy.release()
            
    def focus_port(self):
        return self._focus_port
    
    def start_focus(
        self,
        port: int,
        controls=None,
        rotation: int = 0,
    ):
        if port != 0:
            raise ValueError("Jetson single-camera mode only supports port 0")
        
        if not self._busy.acquire(blocking=False):
            raise BusyError("capture or focus already in progress")
        
        try:
            self._focus_cap = self._open((1920, 1080))
            self._focus_port = 0
        except Exception:
            self._focus_cap = None
            self._focus_port = None
            self._busy.release()
            raise
    def iter_frames(self, timeout: float = 2.0):
        cap = self._focus_cap
        
        if cap is None:
            return
        
        while self._focus_cap is cap:
            ok, frame = cap.read()
            
            if not ok:
                break
                
            ok, encoded = cv2.imencode(
                ".jpg",
                frame,
                [cv2.IMWRITE_JPEG_QUALITY, 85],
            )
            
            if ok:
                yield encoded.tobytes()
                
        self.stop_focus()
        
    def stop_focus(self):
        cap = self._focus_cap
        
        if cap is None:
            return
            
        self._focus_cap = None
        self._focus_port = None
        
        cap.release()
        
        if self._busy.locked():
            self._busy.release()
            
    def close(self):
        self.stop_focus() 
             
        
        
                


