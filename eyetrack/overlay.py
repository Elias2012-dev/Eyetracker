"""OpenCV preview window: mesh, pose read-outs, wizard prompts."""

from __future__ import annotations

import cv2
import numpy as np

from .calibrate import CalibrationWizard
from .pose import EYE_INNER_L, EYE_INNER_R, EYE_OUTER_L, EYE_OUTER_R, HeadPose

_GREEN = (80, 220, 80)
_RED = (60, 60, 230)
_YELLOW = (60, 210, 255)
_WHITE = (240, 240, 240)
_DIM = (170, 170, 170)


class Overlay:
    def __init__(self, window: str = "eyetrack", show_mesh: bool = True) -> None:
        self.window = window
        self.show_mesh = show_mesh
        self._opened = False

    def close(self) -> None:
        if self._opened:
            try:
                cv2.destroyWindow(self.window)
            except cv2.error:
                pass
            self._opened = False

    def window_closed(self) -> bool:
        """True when the user dismissed the preview window."""
        if not self._opened:
            return False
        try:
            return cv2.getWindowProperty(self.window, cv2.WND_PROP_VISIBLE) < 1
        except cv2.error:
            return True

    # ------------------------------------------------------------------
    def draw(self, frame: np.ndarray, pose: HeadPose, pose_out: dict[str, float],
             tracking: bool, fps: float, mirror: bool,
             wizard: CalibrationWizard | None = None,
             status: str = "") -> int:
        """Render the overlay; returns the key code from waitKey (or -1)."""
        img = cv2.flip(frame, 1) if mirror else frame
        h, w = img.shape[:2]

        if pose.detected and pose.landmarks is not None:
            self._draw_face(img, pose, mirror)

        colour = _GREEN if tracking else _RED
        cv2.rectangle(img, (0, 0), (w, 76), (30, 30, 30), -1)
        cv2.putText(img, f"FPS {fps:4.1f}   tracking: {'ON' if tracking else 'lost'}",
                    (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, colour, 1, cv2.LINE_AA)
        if tracking:
            txt = (f"yaw {pose_out['yaw']:+7.1f}  pitch {pose_out['pitch']:+7.1f}  "
                   f"roll {pose_out['roll']:+6.1f}   "
                   f"x {pose_out['x']:+5.1f}  y {pose_out['y']:+5.1f}  z {pose_out['z']:+5.1f} cm")
            cv2.putText(img, txt, (10, 48), cv2.FONT_HERSHEY_SIMPLEX, 0.5, _WHITE, 1, cv2.LINE_AA)
        if status:
            cv2.putText(img, status, (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.5, _YELLOW, 1, cv2.LINE_AA)

        # Where am I looking? Dot inside a circle, mapped from yaw/pitch.
        self._draw_gaze_widget(img, pose_out, tracking)

        # Wizard prompt + target dot.
        if wizard is not None and not wizard.done and not wizard.cancelled:
            step = wizard.step
            cv2.rectangle(img, (0, h - 64), (w, h), (30, 30, 30), -1)
            cv2.putText(img, wizard.prompt, (10, h - 38), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, _YELLOW, 2, cv2.LINE_AA)
            cv2.putText(img, "SPACE capture   ESC cancel", (10, h - 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, _DIM, 1, cv2.LINE_AA)
            if step is not None and step.dot is not None:
                dx = int(step.dot[0] * w)
                dy = int(step.dot[1] * h)
                if mirror:
                    dx = w - dx
                cv2.circle(img, (dx, dy), 14, _YELLOW, 2)
                cv2.circle(img, (dx, dy), 3, _YELLOW, -1)
        elif wizard is not None and wizard.done:
            cv2.rectangle(img, (0, h - 40), (w, h), (30, 30, 30), -1)
            cv2.putText(img, wizard.prompt, (10, h - 14), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, _GREEN, 2, cv2.LINE_AA)

        cv2.imshow(self.window, img)
        self._opened = True
        return cv2.waitKey(1) & 0xFF

    # ------------------------------------------------------------------
    def _draw_face(self, img: np.ndarray, pose: HeadPose, mirror: bool) -> None:
        h, w = img.shape[:2]
        lms = pose.landmarks
        assert lms is not None

        def pt(i: int) -> tuple[int, int]:
            x = int(np.clip(lms[i][0], 0, 1) * w)
            y = int(np.clip(lms[i][1], 0, 1) * h)
            if mirror:
                x = w - x
            return x, y

        if self.show_mesh:
            for i in range(0, len(lms), 4):  # sparse mesh points
                x, y = pt(i)
                cv2.circle(img, (x, y), 1, (60, 160, 60), -1)

        # Eye line + nose marker make the tracked geometry obvious.
        cv2.line(img, pt(EYE_OUTER_R), pt(EYE_OUTER_L), _GREEN, 1, cv2.LINE_AA)
        for idx in (EYE_OUTER_R, EYE_OUTER_L, EYE_INNER_R, EYE_INNER_L, 4):
            x, y = pt(idx)
            cv2.circle(img, (x, y), 3, _GREEN, -1)

    def _draw_gaze_widget(self, img: np.ndarray, pose_out: dict[str, float],
                          tracking: bool) -> None:
        h, w = img.shape[:2]
        cx, cy = w - 70, 110
        cv2.circle(img, (cx, cy), 45, (70, 70, 70), 1, cv2.LINE_AA)
        cv2.line(img, (cx - 45, cy), (cx + 45, cy), (70, 70, 70), 1, cv2.LINE_AA)
        cv2.line(img, (cx, cy - 45), (cx, cy + 45), (70, 70, 70), 1, cv2.LINE_AA)
        if tracking:
            # +/-40 deg maps to the widget radius.
            dx = int(np.clip(pose_out["yaw"] / 40.0, -1, 1) * 45)
            dy = int(np.clip(-pose_out["pitch"] / 40.0, -1, 1) * 45)
            cv2.circle(img, (cx + dx, cy + dy), 6, _GREEN, -1, cv2.LINE_AA)
