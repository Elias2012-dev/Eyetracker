"""A progress window for the moments before the HUD can exist.

Finding a camera on a fresh install takes a few seconds per device: Windows
opens the device, the driver warms up, and a virtual camera that never
delivers a frame has to be given up on. With no window up, that reads as a
hung app - which is exactly the impression the guided setup is meant to
remove.

So the setup phase gets its own tiny always-on-top window. It is drawn with
the same OpenCV primitives as the HUD, and it is a plain class rather than
a global so tests can render a frame without a display.
"""

from __future__ import annotations

import sys
import time

import cv2
import numpy as np

W, H = 560, 220
SPINNER = "|/-\\"

_BG = (18, 15, 24)
_PANEL = (30, 25, 39)
_PANEL_2 = (52, 45, 64)
_LINE = (84, 75, 99)
_TEXT = (243, 245, 250)
_MUTED = (166, 162, 182)
_OK = (122, 226, 148)
_WARN = (86, 170, 240)

_FONT = cv2.FONT_HERSHEY_DUPLEX
_FONT_SMALL = cv2.FONT_HERSHEY_SIMPLEX


def _text_w(text: str, scale: float, thickness: int = 1) -> int:
    return cv2.getTextSize(text, _FONT_SMALL, scale, thickness)[0][0]


def render(title: str, detail: str = "", *, frame: int = 0, done: bool = False,
           note: str = "") -> np.ndarray:
    """One splash frame. Pure: no window, no timing, no side effects."""
    img = np.full((H, W, 3), _BG, np.uint8)
    colour = _OK if done else _WARN

    # rounded card + a soft drop shadow, matching the HUD's look
    cv2.rectangle(img, (22, 26), (W - 18, H - 22), _BG, -1, cv2.LINE_AA)
    cv2.rectangle(img, (18, 22), (W - 22, H - 26), _PANEL, -1, cv2.LINE_AA)
    cv2.rectangle(img, (18, 22), (W - 22, H - 26), _LINE, 1, cv2.LINE_AA)

    mark = "+" if done else SPINNER[frame % len(SPINNER)]
    cv2.putText(img, mark, (44, 78), _FONT, 0.9, colour, 1, cv2.LINE_AA)
    cv2.putText(img, title, (90, 78), _FONT, 0.66, _TEXT, 1, cv2.LINE_AA)

    y = 122
    for line in (detail, note):
        if not line:
            continue
        scale = 0.54 if len(line) * 9.2 < W - 90 else 0.44
        cv2.putText(img, line, (46, y), _FONT_SMALL, scale, _MUTED, 1,
                    cv2.LINE_AA)
        y += 28

    # indeterminate bar: a marker sweeping the width while we work
    track = (46, H - 46, W - 46, H - 40)
    cv2.rectangle(img, track[:2], track[2:], _PANEL_2, -1, cv2.LINE_AA)
    if done:
        cv2.rectangle(img, track[:2], track[2:], _OK, -1, cv2.LINE_AA)
    else:
        span = track[2] - track[0]
        x = track[0] + int(span * 0.28 * ((frame % 40) / 40.0))
        cv2.rectangle(img, (x, track[1]), (x + int(span * 0.30), track[3]),
                      colour, -1, cv2.LINE_AA)
    return img


class Splash:
    """Shows setup progress; silently does nothing if there is no display.

    The silent path matters: a headless launch (a service, a CI smoke test,
    someone over SSH) must not die because a window could not be opened.
    """

    def __init__(self, window: str = "eyetrack-setup", enabled: bool = True) -> None:
        self.window = window
        self.enabled = enabled and sys.platform == "win32"
        self._open = False
        self._frame = 0
        self._t0 = time.monotonic()

    # ------------------------------------------------------------------
    def update(self, title: str, detail: str = "", note: str = "") -> None:
        if not self.enabled:
            return
        try:
            if not self._open:
                cv2.namedWindow(self.window, cv2.WINDOW_NORMAL)
                cv2.setWindowProperty(self.window, cv2.WND_PROP_TOPMOST, 1)
                self._open = True
            img = render(title, detail, frame=self._frame, note=note)
            cv2.imshow(self.window, img)
            cv2.waitKey(30)
            self._frame += 1
        except cv2.error:
            self.enabled = False
            self.close()

    def finish(self, title: str, detail: str = "") -> None:
        """Show the outcome briefly, then take the window down."""
        if not self.enabled:
            return
        try:
            cv2.imshow(self.window, render(title, detail, done=True))
            cv2.waitKey(900)
        except cv2.error:
            pass
        finally:
            self.close()

    def close(self) -> None:
        if self._open:
            try:
                cv2.destroyWindow(self.window)
            except cv2.error:
                pass
            self._open = False

    def __enter__(self) -> "Splash":
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False