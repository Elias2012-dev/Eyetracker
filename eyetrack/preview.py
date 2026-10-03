"""Live camera preview for the settings window.

Picking a camera from a list of names is a guess. This shows you the frame,
at the resolution tracking will actually use, before you commit to it.

Encoding frames for Tkinter normally means Pillow. We deliberately avoid
that: the packaged exe would then need Pillow as a real dependency, and Tk
8.6 reads binary PPM (P6) natively. The extra cost is one copy of the pixel
bytes - and only at preview resolution, never at tracking resolution.
"""

from __future__ import annotations

from typing import Optional, Tuple

import cv2
import numpy as np

# Preview is for framing your face, not for judging quality, so it stays
# small. This is also the label size the window reserves for it.
PREVIEW_MAX_W = 320
PREVIEW_MAX_H = 240


def fit_size(width: int, height: int,
             max_w: int = PREVIEW_MAX_W,
             max_h: int = PREVIEW_MAX_H) -> Tuple[int, int]:
    """Largest aspect-preserving box for ``width`` x ``height`` that fits.

    Never upscales: a 160x120 camera should stay 160x120 rather than being
    smeared up to fill the frame.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"bad frame size {width}x{height}")
    if max_w <= 0 or max_h <= 0:
        raise ValueError(f"bad target size {max_w}x{max_h}")
    scale = min(max_w / width, max_h / height, 1.0)
    return max(1, min(max_w, round(width * scale))), \
           max(1, min(max_h, round(height * scale)))


def frame_to_ppm(frame: np.ndarray, *,
                 max_w: int = PREVIEW_MAX_W,
                 max_h: int = PREVIEW_MAX_H,
                 mirror: bool = False) -> Tuple[bytes, int, int]:
    """Encode a BGR frame as a binary PPM (P6) blob for ``tk.PhotoImage``.

    Returns the payload and the pixel dimensions it encodes. Pure function so
    the encoding is testable without Tkinter or a camera.
    """
    if frame is None or getattr(frame, "size", 0) == 0:
        raise ValueError("empty frame")
    height, width = frame.shape[:2]
    out_w, out_h = fit_size(width, height, max_w, max_h)
    if (out_w, out_h) != (width, height):
        frame = cv2.resize(frame, (out_w, out_h), interpolation=cv2.INTER_AREA)
    if mirror:
        frame = frame[:, ::-1]
    header = f"P6\n{out_w} {out_h}\n255\n".encode("ascii")
    # OpenCV hands back BGR; PPM is RGB.
    return header + cv2.cvtColor(np.ascontiguousarray(frame), cv2.COLOR_BGR2RGB).tobytes(), \
        out_w, out_h


class CameraPreview:
    """A camera held open only for the settings-window picture.

    The device is exclusive on Windows, so this must never be open while
    :mod:`eyetrack.session` is tracking: the window closes the preview before
    starting and reopens it after stopping.
    """

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.cap = None
        self.error = ""

    # -- lifecycle ----------------------------------------------------
    def open(self) -> bool:
        """Open the configured camera. Soft-fails instead of raising."""
        self.close()
        from .session import open_camera

        try:
            self.cap = open_camera(self.cfg)
        except SystemExit as exc:      # the tracking opener's failure mode
            self.error = str(exc)
            return False
        except Exception as exc:       # pragma: no cover - driver oddity
            self.error = f"{type(exc).__name__}: {exc}"
            return False
        self.error = ""
        return True

    def read(self) -> Optional[np.ndarray]:
        """Next frame, or ``None`` if the camera stalled or was closed."""
        if self.cap is None:
            return None
        try:
            ok, frame = self.cap.read()
        except Exception as exc:       # pragma: no cover - driver oddity
            self.error = f"{type(exc).__name__}: {exc}"
            return None
        return frame if ok and frame is not None else None

    def close(self) -> None:
        if self.cap is not None:
            try:
                self.cap.release()
            finally:
                self.cap = None

    @property
    def opened(self) -> bool:
        return self.cap is not None