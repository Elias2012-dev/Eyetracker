"""Automatic camera detection for the first run.

Opening a camera successfully proves nothing: a Windows virtual camera that
isn't being streamed will happily open and then hand back no frames at all.
On the development machine, index 0 (the "Camo" virtual camera) opened in
0.6s and then failed every single ``read()``.

So detection here means: *hands back real pictures of a face*. Anything
weaker picks a black or frozen camera and sends the user straight into
the calibration wizard with nothing to calibrate against.

That makes this the slowest part of a cold start, which is why the probe
is bounded per camera and skipped entirely once a working camera is
already configured.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import cv2
import numpy as np

# How many cameras to consider. DirectShow typically reports a handful;
# past this the probe stops being useful and starts being slow.
MAX_CANDIDATES = 8

# A capture that yields nothing for this long is treated as dead. Windows
# virtual cameras can block for seconds before failing, so be generous -
# but not so generous that a broken device stalls the first run.
OPEN_TIMEOUT_S = 6.0

# Frames to pull before deciding. A single frame can arrive during
# warm-up (black, or half-initialised), so require a couple.
WARMUP_FRAMES = 3


@dataclass
class CameraProbe:
    """What one candidate camera turned out to be."""

    index: int
    name: str = ""
    opened: bool = False          # VideoCapture reported success
    delivered: bool = False       # it actually returned usable frames
    face: bool = False            # a face was found in those frames
    mean_brightness: float = 0.0
    seconds: float = 0.0

    @property
    def usable(self) -> bool:
        """Can we actually track through this camera right now?"""
        return self.delivered and self.face


def _brightness(frame: np.ndarray) -> float:
    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(grey.mean())


def looks_like_black(frame: np.ndarray, threshold: float = 6.0) -> bool:
    """A camera that is open but unlit still returns frames.

    A dead virtual camera usually returns a uniformly dark or uniform frame
    rather than nothing at all, so brightness alone is not enough - but it
    is a cheap way to skip obvious duds before running the landmarker.
    """
    return _brightness(frame) < threshold


def probe(index: int, name: str = "", *, face_detector=None,
          width: int = 1280, height: int = 720, fps: int = 60,
          clock=None) -> CameraProbe:
    """Open one camera and decide whether it is worth tracking through.

    ``face_detector`` is any callable taking a BGR frame and returning
    truthy when a face is present. Injecting it keeps this testable without
    a webcam; in production the caller passes the MediaPipe estimator.

    ``clock`` is injectable for the same reason: a camera that never
    delivers would otherwise busy-wait against the wall clock for the full
    timeout, making the tests take minutes instead of milliseconds.
    """
    clock = clock or time.monotonic
    started = clock()
    probe = CameraProbe(index=index, name=name)
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        cap.release()
        probe.seconds = time.monotonic() - started
        return probe
    probe.opened = True

    try:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, fps)
    except cv2.error:
        pass

    best = 0.0
    got = 0
    deadline = started + OPEN_TIMEOUT_S
    try:
        while clock() < deadline and got < WARMUP_FRAMES:
            ok, frame = cap.read()
            if not ok or frame is None:
                # A camera that keeps failing would spin here; a real read()
                # blocks, but give the loop a way out regardless.
                if clock() - started > OPEN_TIMEOUT_S:
                    break
                continue
            got += 1
            best = max(best, _brightness(frame))
            if face_detector is not None and not looks_like_black(frame):
                if face_detector(frame):
                    probe.face = True
                    break
    finally:
        cap.release()
        probe.seconds = clock() - started

    probe.delivered = got > 0
    probe.mean_brightness = best
    return probe


def detect_camera(devices=None, *, face_detector=None, prefer_name: str = "",
                  width: int = 1280, height: int = 720, fps: int = 60,
                  on_progress=None, clock=None) -> CameraProbe | None:
    """Find a camera that shows a face. Returns ``None`` if none does.

    Order of preference:

    1. ``prefer_name`` - an exact/substring match on the configured name.
       A returning user keeps their camera even if the index moved.
    2. Any device that yields a face.
    3. Any device that at least delivers frames, so the wizard can start
       and the user can be asked to sit in frame rather than being told
       nothing was found.
    """
    if devices is None:
        from .cameras import list_devices
        devices = list_devices()

    candidates = list(devices)[:MAX_CANDIDATES]
    if not candidates:
        return None

    # A saved name wins outright: it is the only stable identifier, since
    # indices shuffle when devices are plugged in or out.
    ordered: list = []
    if prefer_name:
        needle = prefer_name.casefold()
        ordered = [d for d in candidates if d.name.casefold() == needle]
        ordered += [d for d in candidates
                    if needle in d.name.casefold() and d not in ordered]
    # Whatever we did not already queue goes second - including everything,
    # when no name was saved (the first-run case).
    ordered += [d for d in candidates if d not in ordered]

    fallback: CameraProbe | None = None
    for dev in ordered:
        if on_progress is not None:
            on_progress(dev)
        result = probe(dev.index, dev.name, face_detector=face_detector,
                       width=width, height=height, fps=fps, clock=clock)
        if result.usable:
            return result
        if result.delivered and fallback is None:
            fallback = result
    return fallback