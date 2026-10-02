"""On-screen HUD: what the tracker sees, at a glance.

The overlay is the whole interface (the packaged exe has no terminal), so
it has to answer three questions quickly:

* **Is it tracking?** A status pill plus a face outline that only appears
  when a face is actually found.
* **Which way is each axis going?** Three meters with the value, a centre
  tick and an explicit sign legend (``yaw  +12.3°   + = your left``). The
  legend is the point: sign conventions are the number one source of
  "the camera moves the wrong way" confusion.
* **Is it calibrated?** The wizard replaces the bottom of the HUD with the
  active step, progress pips and a live target dot.

Two layouts: the full view over the camera image, and ``compact`` - a
small always-on-top panel with just the meters, for keeping the numbers in
your peripheral vision while a game has the focus.

Everything is drawn with OpenCV primitives on purpose: no GUI toolkit, no
extra dependency, and it keeps working inside the frozen build.
"""

from __future__ import annotations

import math
import sys

import cv2
import numpy as np

from .calibrate import CalibrationWizard
from .pose import HeadPose

# --- palette (OpenCV is BGR) ---------------------------------------------
_PANEL = (34, 29, 44)
_PANEL_SOFT = (46, 40, 58)
_TEXT = (240, 242, 248)
_DIM = (150, 150, 168)
_OK = (120, 226, 148)
_WARN = (84, 172, 240)
_GOLD = (198, 168, 104)
_FACE = (160, 228, 196)

_FONT = cv2.FONT_HERSHEY_DUPLEX
_FONT_SMALL = cv2.FONT_HERSHEY_SIMPLEX

# --- MediaPipe canonical face-mesh contours -------------------------------
# Drawn as closed polylines rather than 478 dots: it reads as a face, and
# it costs ~120 line segments instead of 478 circles per frame.
FACE_OVAL = (10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397,
             365, 379, 378, 400, 377, 152, 148, 176, 149, 150, 136, 172, 58,
             132, 93, 234, 127, 162, 21, 54, 103, 67, 109)
EYE_R = (33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160,
         161, 246)
EYE_L = (263, 249, 390, 373, 374, 380, 381, 382, 362, 398, 384, 385, 386,
         387, 388, 466)
BROWS = ((276, 283, 282, 295, 285, 300, 293, 334, 296, 336),
         (46, 53, 52, 65, 55, 70, 63, 105, 66, 107))
LIPS_OUTER = (61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270,
              269, 267, 0, 37, 39, 40, 185)
LIPS_INNER = (78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308, 415, 310,
              311, 312, 13, 82, 81, 80, 191)
IRIS_R = (468, 469, 470, 471, 472)     # centre point + ring
IRIS_L = (473, 474, 475, 476, 477)


# ---------------------------------------------------------------- helpers
def clamp_frac(value: float, full: float) -> float:
    """Map ``value`` onto -1..1 against ``full`` (0 or tiny -> 0)."""
    if not full or abs(full) < 1e-9:
        return 0.0
    return max(-1.0, min(1.0, value / full))


def meter_fill(x: int, y: int, w: int, h: int, frac: float) -> tuple[int, int, int, int]:
    """Filled part of a centre-anchored meter, as an x1y1x2y2 rectangle."""
    frac = clamp_frac(frac, 1.0)
    half = w // 2
    if frac >= 0:
        return x + half, y, x + half + int(round(frac * half)), y + h
    left = x + half - int(round(-frac * half))
    return left, y, x + half, y + h


def round_rect(img, p1, p2, r, color, thickness=-1) -> None:
    cv2.rectangle(img, (p1[0] + r, p1[1]), (p2[0] - r, p2[1]), color, thickness)
    cv2.rectangle(img, (p1[0], p1[1] + r), (p2[0], p2[1] - r), color, thickness)
    for cx, cy in ((p1[0] + r, p1[1] + r), (p2[0] - r, p1[1] + r),
                   (p1[0] + r, p2[1] - r), (p2[0] - r, p2[1] - r)):
        cv2.circle(img, (cx, cy), r, color, thickness)


def put(img, text: str, org, scale: float, color, thickness: int = 1,
        font=_FONT) -> None:
    cv2.putText(img, text, org, font, scale, color, thickness, cv2.LINE_AA)


def put_right(img, text: str, org_right, scale: float, color,
              thickness: int = 1, font=_FONT) -> None:
    (tw, _), _ = cv2.getTextSize(text, font, scale, thickness)
    put(img, text, (org_right[0] - tw, org_right[1]), scale, color, thickness,
        font)


def draw_meter(img, mx, x: int, y: int, w: int, *, label: str, value: float,
               full: float, legend: str, colour, tracking: bool,
               value_w: int = 66) -> None:
    """One axis row: label + sign legend above a centre-anchored bar.

    Shared by the full view and the compact panel so the two can never
    drift apart.

    ``mx`` maps a logical x through the mirror flip; the compact panel
    passes the identity. The numeric readout sits in a gutter to the
    right of the track instead of on top of it - printed over the fill it
    was unreadable exactly when the value mattered most.
    """
    put(img, label, (mx(x), y), 0.42, _DIM, 1, _FONT_SMALL)
    put_right(img, legend, (mx(x + w), y), 0.36, _DIM, 1, _FONT_SMALL)

    bar_w = w - value_w
    bar_y = y + 8
    round_rect(img, (mx(x), bar_y), (mx(x + bar_w), bar_y + 12), 6, _PANEL_SOFT)
    if tracking:
        x1, y1, x2, y2 = meter_fill(x, bar_y, bar_w, 12, clamp_frac(value, full))
        round_rect(img, (mx(x1), y1), (mx(x2), y2), 6, colour)
    # centre tick sits proud of the track so "zero" is findable at a glance
    cv2.line(img, (mx(x + bar_w // 2), bar_y - 2),
             (mx(x + bar_w // 2), bar_y + 14), _TEXT, 1, cv2.LINE_AA)
    put_right(img, f"{value:+5.1f}°", (mx(x + w), bar_y + 11), 0.4,
              colour if tracking else _DIM, 1, _FONT_SMALL)


# ------------------------------------------------------------------ class
class Overlay:
    def __init__(self, window: str = "eyetracker", show_mesh: bool = True,
                 top_most: bool = False, compact: bool = False,
                 yaw_range: float = 40.0, pitch_range: float = 30.0,
                 roll_range: float = 20.0) -> None:
        self.window = window
        self.show_mesh = show_mesh
        self.top_most = top_most
        self.compact = compact
        self.yaw_range = yaw_range
        self.pitch_range = pitch_range
        self.roll_range = roll_range
        self._opened = False
        self._topmost_applied = False

    # ------------------------------------------------------------------
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
        """Render one frame; returns the key code from waitKey (or -1)."""
        if self.compact or frame is None:
            img = self._compact_canvas(pose_out, tracking, fps, status)
        else:
            img = cv2.flip(frame, 1) if mirror else frame
            img = self._compose(img, pose, pose_out, tracking, fps, mirror,
                                wizard, status)

        cv2.imshow(self.window, img)
        self._opened = True
        self._apply_window_props()
        return cv2.waitKey(1) & 0xFF

    def _apply_window_props(self) -> None:
        """Keep the HUD above the game window in the always-on-top mode."""
        if not self.top_most or self._topmost_applied:
            return
        try:
            if sys.platform == "win32":
                cv2.setWindowProperty(self.window, cv2.WND_PROP_TOPMOST, 1)
        except cv2.error:
            pass  # not supported on this backend; harmless
        self._topmost_applied = True

    # ==================================================================
    # full view
    def _compose(self, img, pose, pose_out, tracking, fps, mirror, wizard,
                 status) -> np.ndarray:
        h, w = img.shape[:2]
        flip = mirror  # every x coordinate below is pre-mirrored through this

        def mx(x: int) -> int:
            return w - x if flip else x

        # --- translucent layer, blended once -----------------------------
        layer = img.copy()
        head = 78
        round_rect(layer, (mx(12), 10), (mx(w - 12), head), 16, _PANEL)
        panel_w = 300
        meters_h = 150
        round_rect(layer, (mx(12), head + 14), (mx(12 + panel_w), head + 14 + meters_h),
                   16, _PANEL)
        trans_y = head + 26 + meters_h
        round_rect(layer, (mx(12), trans_y), (mx(12 + panel_w), trans_y + 96),
                   16, _PANEL)
        cv2.addWeighted(layer, 0.62, img, 0.38, 0, dst=img)

        # --- header -------------------------------------------------------
        colour = _OK if tracking else _WARN
        put(img, "EYETRACKER", (mx(28), 44), 0.62, _TEXT, 1)
        pill_w = 118 if tracking else 132
        round_rect(img, (mx(150), 22), (mx(150 + pill_w), 56), 17, _PANEL_SOFT)
        cv2.circle(img, (mx(168), 39), 6, colour, -1)
        put(img, "TRACKING" if tracking else "NO FACE",
            (mx(182), 45), 0.5, colour, 1, _FONT_SMALL)
        put(img, f"{fps:4.1f} fps", (mx(150 + pill_w + 14), 45), 0.5, _DIM, 1,
            _FONT_SMALL)
        if tracking:
            put_right(img, "yaw {:+6.1f}   pitch {:+6.1f}   roll {:+6.1f}".format(
                pose_out["yaw"], pose_out["pitch"], pose_out["roll"]),
                (mx(w - 28), 45), 0.58, _TEXT, 1, _FONT_SMALL)

        # --- meters -------------------------------------------------------
        x0, y0, mw = 34, head + 40, panel_w - 44
        put(img, "HEAD POSE", (mx(x0), y0 - 12), 0.44, _DIM, 1, _FONT_SMALL)
        rows = (
            ("YAW", pose_out["yaw"], self.yaw_range, "+ = your left", _OK),
            ("PITCH", pose_out["pitch"], self.pitch_range, "+ = up", _GOLD),
            ("ROLL", pose_out["roll"], self.roll_range, "+ = tilt left", _GOLD),
        )
        for i, (label, value, full, legend, colour) in enumerate(rows):
            draw_meter(img, mx, x0, y0 + i * 38, mw, label=label, value=value,
                       full=full, legend=legend, colour=colour,
                       tracking=tracking)

        # --- translation ---------------------------------------------------
        put(img, "POSITION", (mx(x0), trans_y + 26), 0.44, _DIM, 1, _FONT_SMALL)
        for i, (label, value) in enumerate((("X", pose_out["x"]), ("Y", pose_out["y"]),
                                            ("Z", pose_out["z"]))):
            y = trans_y + 44 + i * 18
            put(img, label, (mx(x0), y), 0.4, _DIM, 1, _FONT_SMALL)
            bx = x0 + 20
            bw = mw - 78
            round_rect(img, (mx(bx), y - 7), (mx(bx + bw), y + 1), 4, _PANEL_SOFT)
            if tracking:
                x1, y1, x2, y2 = meter_fill(bx, y - 7, bw, 8, clamp_frac(value, 25.0))
                round_rect(img, (mx(x1), y1), (mx(x2), y2), 4, _GOLD)
            put_right(img, f"{value:+5.1f} cm", (mx(x0 + mw), y), 0.38,
                      _DIM, 1, _FONT_SMALL)

        # --- attitude widget -----------------------------------------------
        self._draw_attitude(img, w - 116, 196, 76, pose_out, tracking, mirror)

        # --- face ----------------------------------------------------------
        if pose.detected and pose.landmarks is not None:
            self._draw_face(img, pose, mirror, tracking)

        # --- wizard / status ------------------------------------------------
        if wizard is not None and not wizard.done and not wizard.cancelled:
            self._draw_wizard(img, w, h, wizard, mirror)
        elif wizard is not None and wizard.done:
            put(img, "calibration saved - press C to redo", (mx(w // 2), h - 26),
                0.52, _OK, 1)
        elif status:
            tw = cv2.getTextSize(status, _FONT, 0.52, 1)[0][0]
            round_rect(img, (mx(w // 2 - tw // 2 - 16), h - 52),
                       (mx(w // 2 + tw // 2 + 16), h - 10), 12, _PANEL)
            put(img, status, (mx(w // 2 - tw // 2), h - 22), 0.52, _TEXT, 1)

        if not tracking:
            put(img, "no face - light your face and look at the camera",
                (mx(w // 2), 40), 0.5, _WARN, 1, _FONT_SMALL)

        put(img, "C calibrate   R recentre   Q quit", (mx(28), h - 18), 0.44,
            _DIM, 1, _FONT_SMALL)
        return img

    # ==================================================================
    # compact HUD (no camera image, small, always-on-top friendly)
    def _compact_canvas(self, pose_out, tracking, fps, status) -> np.ndarray:
        w, h = 460, 250
        img = np.full((h, w, 3), _PANEL[0], np.uint8)
        img[:] = (26, 22, 34)
        colour = _OK if tracking else _WARN

        cv2.circle(img, (30, 34), 6, colour, -1)
        put(img, "TRACKING" if tracking else "NO FACE", (44, 40), 0.5, colour,
            1, _FONT_SMALL)
        put_right(img, f"{fps:4.1f} fps", (w - 18, 40), 0.45, _DIM, 1, _FONT_SMALL)

        x0, mw = 26, w - 190
        identity = lambda v: v  # compact panel is never mirrored
        for i, (label, value, full, legend, col) in enumerate((
                ("YAW", pose_out["yaw"], self.yaw_range, "+ left", _OK),
                ("PITCH", pose_out["pitch"], self.pitch_range, "+ up", _GOLD),
                ("ROLL", pose_out["roll"], self.roll_range, "+ tilt", _GOLD))):
            draw_meter(img, identity, x0, 76 + i * 40, mw, label=label,
                       value=value, full=full, legend=legend, colour=col,
                       tracking=tracking)

        self._draw_attitude(img, w - 78, 118, 52, pose_out, tracking, False)
        put(img, "x {:+5.1f}  y {:+5.1f}  z {:+5.1f} cm".format(
            pose_out["x"], pose_out["y"], pose_out["z"]),
            (x0, h - 24), 0.42, _DIM, 1, _FONT_SMALL)
        if status:
            put_right(img, status, (w - 18, h - 24), 0.4, _OK, 1, _FONT_SMALL)
        return img

    # ==================================================================
    def _draw_attitude(self, img, cx, cy, r, pose_out, tracking, mirror) -> None:
        """Ring + tilting horizon (roll) + a dot for where you are looking."""
        cv2.circle(img, (cx, cy), r, _PANEL_SOFT, 1, cv2.LINE_AA)
        cv2.circle(img, (cx, cy), r - 1, _PANEL, 1, cv2.LINE_AA)
        for gx in (cx - r + 6, cx + r - 6):      # tick marks
            cv2.line(img, (gx, cy - 4), (gx, cy + 4), _DIM, 1, cv2.LINE_AA)

        roll = pose_out["roll"] if tracking else 0.0
        span = int(r * 0.8)
        half = int(span * math.cos(math.radians(roll)))
        lift = int(span * math.sin(math.radians(roll)))
        # Screen y grows downwards, and a mirror flips x: both cancel for the
        # horizon, so roll reads as "the line follows your head".
        cv2.line(img, (cx - half, cy + lift), (cx + half, cy + lift),
                 _GOLD if tracking else _DIM, 2, cv2.LINE_AA)

        dx = int(clamp_frac(pose_out["yaw"] / 40.0, 1.0) * (r - 10))
        dy = int(-clamp_frac(pose_out["pitch"] / 40.0, 1.0) * (r - 10))
        if mirror:
            dx = -dx
        cv2.circle(img, (cx + dx, cy + dy), 5, _OK if tracking else _DIM, -1,
                   cv2.LINE_AA)

    def _draw_wizard(self, img, w, h, wizard, mirror) -> None:
        step = wizard.step
        total = len(wizard.captures) + (1 if step is not None else 0)
        box_h = 92
        top = h - box_h - 46
        layer = img.copy()
        round_rect(layer, (w // 2 - 250, top), (w // 2 + 250, top + box_h), 14,
                   _PANEL)
        cv2.addWeighted(layer, 0.75, img, 0.25, 0, dst=img)

        title = "CALIBRATION" if step else "DONE"
        put(img, title, (w // 2 - 228, top + 26), 0.48, _DIM, 1, _FONT_SMALL)
        prompt = wizard.prompt
        put(img, prompt, (w // 2 - 228, top + 56), 0.62, _TEXT, 1)

        # progress pips
        for i in range(5):
            px = w // 2 + 180 + i * 16
            done = i < total
            cv2.circle(img, (px, top + 52), 5, _OK if done else _PANEL_SOFT,
                       -1, cv2.LINE_AA)

        if step is not None and step.dot is not None:
            dx = int(step.dot[0] * w)
            dy = int(top + 14 + step.dot[1] * (box_h - 28))
            cv2.circle(img, (dx, dy), 15, _OK, 2, cv2.LINE_AA)
            cv2.circle(img, (dx, dy), 4, _OK, -1, cv2.LINE_AA)

        put(img, "SPACE capture   ESC cancel", (w // 2 - 228, top + 82), 0.42,
            _DIM, 1, _FONT_SMALL)

    # ------------------------------------------------------------------
    def _draw_face(self, img, pose: HeadPose, mirror: bool, tracking: bool) -> None:
        lms = pose.landmarks
        if lms is None:
            return

        def pt(i: int) -> tuple[int, int]:
            x = int(np.clip(lms[i][0], 0, 1) * img.shape[1])
            y = int(np.clip(lms[i][1], 0, 1) * img.shape[0])
            return (img.shape[1] - x if mirror else x, y)

        def poly(indices, closed=True, colour=_FACE, thick=1):
            pts = np.array([pt(i) for i in indices], np.int32)
            # Soft glow: a wide dim pass under a thin bright one.
            cv2.polylines(img, [pts], closed, colour, thick + 2, cv2.LINE_AA)
            cv2.polylines(img, [pts], closed, colour, thick, cv2.LINE_AA)

        if self.show_mesh:
            # Dense mesh as faint dots, every other point to keep it cheap.
            for i in range(0, len(lms), 2):
                x, y = pt(i)
                cv2.circle(img, (x, y), 1, (90, 120, 110), -1)

        poly(FACE_OVAL, True, _FACE, 1)
        for brow in BROWS:
            poly(brow, False, _FACE, 1)
        poly(EYE_R, True)
        poly(EYE_L, True)
        poly(LIPS_OUTER, True, _FACE, 1)
        poly(LIPS_INNER, True, (150, 200, 180), 1)
        for iris in (IRIS_R, IRIS_L):
            poly(iris, True, _GOLD, 1)

        # Nose bridge marker: makes head rotation obvious at a glance.
        p0, p1 = pt(168), pt(6)
        cv2.line(img, p0, p1, _OK if tracking else _DIM, 1, cv2.LINE_AA)