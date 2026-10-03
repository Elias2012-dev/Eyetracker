"""On-screen HUD: what the tracker sees, at a glance.

The overlay is the whole interface (the packaged exe has no terminal), so
it has to answer four questions quickly:

* **Is it tracking?** A status pill in the rail header plus a card in the
  middle of the frame when nothing is found - the two states have to look
  obviously different from across a desk.
* **Which way is each axis going?** Three meters with a large numeric
  readout in a gutter, a centre tick and an explicit sign legend
  (``+ = your left``). The legend is the point: sign conventions are the
  number one source of "the camera moves the wrong way" confusion.
* **Is the game getting it?** Output chips at the bottom of the rail show
  which sinks are live (TrackIR games, the Minecraft stream, the mouse).
* **Is it calibrated?** While the wizard runs it owns a full-width card
  along the bottom edge, with the active step, progress pips and a
  viewfinder showing where to point.

Two layouts: the full view over the camera image, and ``compact`` - a small
always-on-top panel with just the numbers, for keeping the axes in your
peripheral vision while a game has the focus.

Everything is drawn with OpenCV primitives on purpose: no GUI toolkit, no
extra dependency, and it keeps working inside the frozen build.
"""

from __future__ import annotations

import math
import sys

import cv2
import numpy as np

from .calibrate import STEPS, CalibrationWizard
from .pose import HeadPose

# --- palette (OpenCV is BGR) ---------------------------------------------
_BG = (18, 15, 24)             # window backdrop / card shadow
_PANEL = (30, 25, 39)          # card fill
_PANEL_2 = (52, 45, 64)        # inset chips and meter tracks
_LINE = (84, 75, 99)           # hairlines and borders
_TEXT = (243, 245, 250)
_MUTED = (166, 162, 182)
_OK = (122, 226, 148)
_WARN = (86, 170, 240)
_GOLD = (206, 176, 110)
_FACE = (168, 230, 200)
_GLOW = (96, 138, 116)         # soft pass under the face contours
_TRAIL_OLD = (88, 108, 96)     # oldest trail sample
_TRAIL_NEW = (196, 245, 214)

_FONT = cv2.FONT_HERSHEY_DUPLEX
_FONT_SMALL = cv2.FONT_HERSHEY_SIMPLEX

# --- layout ---------------------------------------------------------------
_MARGIN = 18
_RAIL_W = 364
_PAD = 24
_RADIUS = 18
# Cards keep a fifth of the video behind them: enough to feel like a
# heads-up display rather than a window pasted over the picture, opaque
# enough to read a 0.5-scale label against a bright face.
_PANEL_ALPHA = 0.90
_CARD_ALPHA = 0.97              # centred cards: read-first, not seen-through
_STRIP_H = 56
_GAUGE_W = 176
_GAUGE_H = 190
_WIZARD_H = 148
_NO_FACE_W = 560
_NO_FACE_H = 132
_HELP_W = 520
_HELP_H = 316
_TRAIL_LEN = 46

# Rail rhythm. _draw_rail walks these and _rail_height adds them up, so the
# card is always exactly as tall as the content inside it; a test asserts the
# content still fits when the capture is short.
_TITLE_H = 34          # title baseline offset from the top of the content
_PILL_H = 34
_METER_STEP = 46
_POSITION_STEP = 27
_CHIP_H = 28
_CHIP_GAP = 8
_CHIP_ROW_STEP = 36

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

HELP_LINES = (
    ("C", "calibration wizard (5 quick steps)"),
    ("R", "re-centre on how you sit right now"),
    ("M", "show / hide the face mesh"),
    ("F", "switch camera view / compact HUD"),
    ("H", "show or hide this help"),
    ("Q", "quit"),
)

NO_FACE_LINES = (
    "Move into frame and face the camera.",
    "If the preview is black or frozen, run the app again with --pick-camera.",
)


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
    """A rounded rectangle built from two bars and four corner discs.

    Deliberately *not* anti-aliased: three overlapping shapes, each blended
    against the background on its own edge, leave a faint cross at every
    corner where they meet. Hard-edged primitives land on the same pixels
    and the seam disappears, and at these radii the jaggies cannot be seen.
    """
    cv2.rectangle(img, (p1[0] + r, p1[1]), (p2[0] - r, p2[1]), color, thickness)
    cv2.rectangle(img, (p1[0], p1[1] + r), (p2[0], p2[1] - r), color, thickness)
    for cx, cy in ((p1[0] + r, p1[1] + r), (p2[0] - r, p1[1] + r),
                   (p1[0] + r, p2[1] - r), (p2[0] - r, p2[1] - r)):
        cv2.circle(img, (cx, cy), r, color, thickness, cv2.LINE_8)


def put(img, text: str, org, scale: float, color, thickness: int = 1,
        font=_FONT) -> None:
    cv2.putText(img, text, org, font, scale, color, thickness, cv2.LINE_AA)


def text_w(text: str, scale: float, thickness: int = 1, font=_FONT_SMALL) -> int:
    """Width of a whole string, measured in one go (kerning included)."""
    return cv2.getTextSize(text, font, scale, thickness)[0][0]


def put_right(img, text: str, org_right, scale: float, color,
              thickness: int = 1, font=_FONT) -> None:
    put(img, text, (org_right[0] - text_w(text, scale, thickness, font),
                    org_right[1]), scale, color, thickness, font)


def put_run(img, text: str, org, scale: float, color,
            thickness: int = 1, font=_FONT_SMALL) -> int:
    """Draw a string, advancing one glyph at a time.

    Needed by anything that has to lay itself out inside a fixed box (a
    chip with a dot, a label and a width), where measuring the whole
    string up front would not tell you where the middle falls.
    """
    x, y = org
    for ch in text:
        put(img, ch, (x, y), scale, color, thickness, font)
        x += text_w(ch, scale, thickness, font)
    return x


def put_run_right(img, text: str, org_right, scale: float, color,
                  thickness: int = 1, font=_FONT_SMALL) -> None:
    put_run(img, text, (org_right[0] - text_w(text, scale, thickness, font),
                        org_right[1]), scale, color, thickness, font)


def caps_w(text: str, scale: float, track: int, thickness: int = 1,
           font=_FONT_SMALL) -> int:
    return text_w(text, scale, thickness, font) + track * max(0, len(text) - 1)


def put_caps(img, text: str, org, scale: float, color, *, track: int = 3,
             thickness: int = 1, font=_FONT_SMALL) -> int:
    """A small-caps section label, letter-spaced so it reads as a heading."""
    x = put_run(img, text, org, scale, color, thickness, font)
    return x + track * max(0, len(text) - 1)


def put_caps_right(img, text: str, org_right, scale: float, color, *,
                   track: int = 3, thickness: int = 1,
                   font=_FONT_SMALL) -> None:
    put_caps(img, text, (org_right[0] - caps_w(text, scale, track, thickness,
                                               font), org_right[1]),
             scale, color, track=track, thickness=thickness, font=font)


def fit_scale(text: str, max_w: int, start: float = 0.74,
              floor: float = 0.34) -> float:
    """Largest font scale at which ``text`` still fits in ``max_w`` pixels.

    The prompts are user-visible sentences of varying length; shrinking
    instead of clipping means a long one is smaller rather than cut off.
    """
    if max_w <= 0:
        return floor
    scale = start
    while scale > floor and text_w(text, scale, 1, _FONT) > max_w:
        scale -= 0.02
    return max(scale, floor)


def _lerp(a, b, t: float):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def draw_meter(img, x: int, y: int, w: int, *, label: str, value: float,
               full: float, legend: str, colour, tracking: bool,
               value_w: int = 62) -> None:
    """One axis row: label + sign legend above a centre-anchored bar.

    Shared by the full view and the compact panel so the two can never
    drift apart.

    The numeric readout sits in a gutter to the right of the track instead
    of on top of it - printed over the fill it was unreadable exactly when
    the value mattered most.
    """
    put(img, label, (x, y), 0.48, _MUTED, 1, _FONT_SMALL)
    put_right(img, legend, (x + w, y), 0.40, _MUTED, 1, _FONT_SMALL)

    bar_w = w - value_w
    bar_y = y + 8
    round_rect(img, (x, bar_y), (x + bar_w, bar_y + 13), 6, _PANEL_2)
    if tracking:
        x1, y1, x2, y2 = meter_fill(x, bar_y, bar_w, 13, clamp_frac(value, full))
        round_rect(img, (x1, y1), (x2, y2), 6, colour)
    # centre tick sits proud of the track so "zero" is findable at a glance
    cv2.line(img, (x + bar_w // 2, bar_y - 2),
             (x + bar_w // 2, bar_y + 15), _TEXT, 1, cv2.LINE_AA)
    put_right(img, f"{value:+5.1f}°", (x + w, bar_y + 12), 0.5,
              colour if tracking else _MUTED, 1, _FONT_SMALL)


def chip_w(label: str) -> int:
    return text_w(label.upper(), 0.46, 1, _FONT_SMALL) + 44


def chip_rows(chips, width: int) -> int:
    """Rows the output chips need at ``width``.

    Takes either bare labels or ``(label, active)`` pairs, so it can be
    asked about a set of names before any of them has a state. Shared by
    the rail height and the rail layout, so the card is exactly as tall as
    its content whether there is one chip or five.
    """
    rows, cx = 1, 0
    for entry in chips:
        name = entry[0] if isinstance(entry, (tuple, list)) else entry
        cw = chip_w(str(name))
        if cx and cx + cw > width:
            rows, cx = rows + 1, 0
        cx += cw + _CHIP_GAP
    return rows


def draw_chip(img, x: int, y: int, label: str, colour, *, on: bool = True,
              h: int = 28) -> int:
    """A pill with a state dot; returns its width so chips can be laid out."""
    label = label.upper()
    w = chip_w(label)
    round_rect(img, (x, y), (x + w, y + h), h // 2, _PANEL_2 if on else _BG)
    cv2.circle(img, (x + 14, y + h // 2), 4, colour if on else _LINE, -1,
               cv2.LINE_AA)
    put_run(img, label, (x + 24, y + h // 2 + 5), 0.46,
            _TEXT if on else _MUTED, 1, _FONT_SMALL)
    return w


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
        self.help_visible = False
        self._opened = False
        self._topmost_applied = False
        self._trail: list[tuple[float, float]] = []

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

    def toggle_help(self) -> bool:
        self.help_visible = not self.help_visible
        return self.help_visible

    def toggle_mesh(self) -> bool:
        self.show_mesh = not self.show_mesh
        return self.show_mesh

    def set_compact(self, compact: bool) -> bool:
        self.compact = bool(compact)
        return self.compact

    # ------------------------------------------------------------------
    def draw(self, frame: np.ndarray, pose: HeadPose, pose_out: dict[str, float],
             tracking: bool, fps: float, mirror: bool,
             wizard: CalibrationWizard | None = None,
             status: str = "", chips: tuple = (),
             show_help: bool | None = None) -> int:
        """Render one frame; returns the key code from waitKey (or -1)."""
        if show_help is not None:
            self.help_visible = show_help
        if self.compact or frame is None:
            img = self._compact_canvas(pose_out, tracking, fps, status, chips)
        else:
            img = cv2.flip(frame, 1) if mirror else frame
            img = self._compose(img, pose, pose_out, tracking, fps, mirror,
                                wizard, status, chips)

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
                 status, chips=()) -> np.ndarray:
        h, w = img.shape[:2]

        # Gaze trail first: it belongs to the video, and the cards are
        # blended over it afterwards, so a card never gets a line scribbled
        # across it.
        self._update_trail(pose_out, tracking)
        self._draw_trail(img, mirror)

        # Face under the cards: the contour is the loudest thing on screen,
        # and drawn on top it cut straight through the help and calibration
        # text. The cards are near-opaque, so it still reads as frosted.
        if pose.detected and pose.landmarks is not None:
            self._draw_face(img, pose, mirror, tracking)

        boxes = self._layout(w, h, wizard, chips)
        no_face = not tracking and wizard is None
        extra = []
        if no_face:
            extra.append(self._centered(w, h, _NO_FACE_W, _NO_FACE_H))
        if self.help_visible:
            extra.append(self._centered(w, h, _HELP_W, _HELP_H))
        cards = [b for b in (boxes["rail"], boxes["gauge"], boxes["strip"],
                             boxes["wizard"]) if b is not None]

        # --- one translucent layer for every edge card ------------------
        layer = img.copy()
        for x1, y1, x2, y2 in cards:
            round_rect(layer, (x1 + 2, y1 + 4), (x2 + 2, y2 + 4), _RADIUS, _BG)
            round_rect(layer, (x1, y1), (x2, y2), _RADIUS, _PANEL)
            round_rect(layer, (x1, y1), (x2, y2), _RADIUS, _LINE, 1)
        cv2.addWeighted(layer, _PANEL_ALPHA, img, 1.0 - _PANEL_ALPHA, 0, dst=img)

        # The centred cards get their own, far more opaque pass: they are
        # the ones people actually read, and at the edge-card alpha the
        # face contour still ghosted through the text.
        if extra:
            layer = img.copy()
            for x1, y1, x2, y2 in extra:
                round_rect(layer, (x1 + 2, y1 + 4), (x2 + 2, y2 + 4), _RADIUS, _BG)
                round_rect(layer, (x1, y1), (x2, y2), _RADIUS, _PANEL)
                round_rect(layer, (x1, y1), (x2, y2), _RADIUS, _LINE, 1)
            cv2.addWeighted(layer, _CARD_ALPHA, img, 1.0 - _CARD_ALPHA, 0,
                            dst=img)

        # --- rail -------------------------------------------------------
        self._draw_rail(img, boxes["rail"], pose_out, tracking, fps, chips)
        self._draw_gauge(img, boxes["gauge"], pose_out, tracking, mirror)
        self._draw_strip(img, w, h, boxes["strip"], status, fps)
        if no_face:
            self._draw_no_face(img, w, h)
        if self.help_visible:
            self._draw_help(img, w, h)
        self._draw_wizard(img, w, h, wizard, boxes["wizard"])
        return img

    @staticmethod
    def _rail_height(chip_rows_: int = 1) -> int:
        """Height of the rail card.

        Walks exactly the same steps as :meth:`_draw_rail`, offset by the
        card's top pad, so the card ends at the last row of content. If the
        two ever drift apart the trail of tests fails, which is the point.
        """
        y = _PAD                          # top of the content
        y += 10 + _PILL_H + 26            # status pill, then the divider
        # (the title baseline is an offset from y, it does not advance it)
        y += 24 + 20                      # "HEAD POSE" caption and its gap
        y += 3 * _METER_STEP - 18         # three meters, then the divider
        y += 24 + 20                      # "POSITION" caption and its gap
        y += 3 * _POSITION_STEP - 8       # three translation rows + divider
        y += 24 + 12                      # "OUTPUTS" caption and its gap
        y += (chip_rows_ * _CHIP_ROW_STEP - (_CHIP_ROW_STEP - _CHIP_H))
        return y + _PAD

    # ------------------------------------------------------------------
    # geometry
    def _layout(self, w: int, h: int, wizard, chips=()) -> dict:
        """Where each card sits, or None where there is no room for it."""
        rail_x2 = _MARGIN + _RAIL_W
        busy = _MARGIN * 2 + (_WIZARD_H if self._wizard_active(wizard) else _STRIP_H)
        want = self._rail_height(chip_rows(chips, _RAIL_W - 2 * _PAD))
        rail_h = max(200, min(want, h - busy))

        gauge = None
        gx1 = w - _MARGIN - _GAUGE_W
        if gx1 > rail_x2 + _MARGIN and h > _MARGIN * 2 + _GAUGE_H:
            gauge = (gx1, _MARGIN, w - _MARGIN, _MARGIN + _GAUGE_H)

        strip = wizard_box = None
        if h > _MARGIN * 2 + _STRIP_H:
            if self._wizard_active(wizard):
                wizard_box = (_MARGIN, h - _MARGIN - _WIZARD_H, w - _MARGIN,
                              h - _MARGIN)
            else:
                strip = (_MARGIN, h - _MARGIN - _STRIP_H, w - _MARGIN,
                         h - _MARGIN)
        return {"rail": (_MARGIN, _MARGIN, rail_x2, _MARGIN + rail_h),
                "gauge": gauge, "strip": strip, "wizard": wizard_box}

    @staticmethod
    def _wizard_active(wizard) -> bool:
        return wizard is not None and not wizard.done and not wizard.cancelled

    @staticmethod
    def _centered(w: int, h: int, cw: int, ch: int) -> tuple:
        """A centred card rect, in the same x1y1x2y2 shape as the edges."""
        x1, y1 = (w - cw) // 2, (h - ch) // 2
        return x1, y1, x1 + cw, y1 + ch

    # ==================================================================
    # rail
    def _draw_rail(self, img, rail, pose_out, tracking, fps, chips) -> int:
        """Draw the left rail; returns the y its content reached."""
        if rail is None:
            return 0
        x1, y1, x2, y2 = rail
        x0 = x1 + _PAD
        w = (x2 - x1) - 2 * _PAD
        right = x2 - _PAD

        y = y1 + _PAD
        put(img, "EYETRACKER", (x0, y + _TITLE_H), 0.72, _TEXT, 1)
        put_right(img, f"{fps:4.1f} fps", (right, y + _TITLE_H), 0.52,
                  _MUTED, 1, _FONT_SMALL)

        # status pill: the single answer to "is it working?"
        y += 10 + _PILL_H
        colour = _OK if tracking else _WARN
        label = "TRACKING" if tracking else "NO FACE"
        pw = text_w(label, 0.56, 1, _FONT_SMALL) + 50
        round_rect(img, (x0, y), (x0 + pw, y + _PILL_H), 17, _PANEL_2)
        cv2.circle(img, (x0 + 19, y + _PILL_H // 2), 6, colour, -1, cv2.LINE_AA)
        put_run(img, label, (x0 + 35, y + 23), 0.56, colour, 1, _FONT_SMALL)
        put_right(img, "calibrated" if tracking else "searching",
                  (right, y + 23), 0.46, _MUTED, 1, _FONT_SMALL)

        y = self._divider(img, x0, right, y + 26)

        put_caps(img, "HEAD POSE", (x0, y + 24), 0.46, _MUTED, track=3)
        y += 22 + 16
        for i, (lbl, value, full, legend, col) in enumerate((
                ("YAW", pose_out["yaw"], self.yaw_range, "+ = your left", _OK),
                ("PITCH", pose_out["pitch"], self.pitch_range, "+ = up", _GOLD),
                ("ROLL", pose_out["roll"], self.roll_range, "+ = tilt left", _GOLD))):
            draw_meter(img, x0, y + i * _METER_STEP, w, label=lbl,
                       value=value, full=full, legend=legend, colour=col,
                       tracking=tracking)
        y = self._divider(img, x0, right, y + 3 * _METER_STEP - 18)

        # The lower sections are the first to go on a short capture: the
        # meters are what you read, the rest is reference.
        if y + 130 < y2:
            put_caps(img, "POSITION", (x0, y + 24), 0.46, _MUTED, track=3)
            y += 22 + 16
            for i, (lbl, value) in enumerate((("X", pose_out["x"]),
                                              ("Y", pose_out["y"]),
                                              ("Z", pose_out["z"]))):
                ry = y + i * _POSITION_STEP
                put(img, lbl, (x0, ry), 0.46, _MUTED, 1, _FONT_SMALL)
                bx, bw = x0 + 22, w - 108
                round_rect(img, (bx, ry - 7), (bx + bw, ry + 1), 4,
                           _PANEL_2)
                if tracking:
                    xa, ya, xb, yb = meter_fill(bx, ry - 7, bw, 8,
                                                clamp_frac(value, 25.0))
                    round_rect(img, (xa, ya), (xb, yb), 4, _GOLD)
                put_right(img, f"{value:+5.1f} cm", (right, ry), 0.46,
                          _TEXT if tracking else _MUTED, 1, _FONT_SMALL)
            y = self._divider(img, x0, right, y + 3 * _POSITION_STEP - 8)

        if y + 66 < y2:
            put_caps(img, "OUTPUTS", (x0, y + 24), 0.46, _MUTED, track=3)
            y += 24 + 12
            if not chips:
                put_run(img, "none enabled", (x0, y + 19), 0.46, _MUTED, 1,
                        _FONT_SMALL)
                y += 19
            else:
                cx, cy = x0, y
                for name, on in chips:
                    name = str(name)
                    cw = chip_w(name)
                    if cx + cw > right and cx > x0:
                        cx, cy = x0, cy + _CHIP_ROW_STEP
                    draw_chip(img, cx, cy, name, _OK if on else _MUTED, on=on)
                    cx += cw + _CHIP_GAP
                y = cy + _CHIP_H
        return y

    def _divider(self, img, x0: int, right: int, y: int) -> int:
        cv2.line(img, (x0, y), (right, y), _LINE, 1, cv2.LINE_AA)
        return y

    # ==================================================================
    # view gauge
    def _draw_gauge(self, img, box, pose_out, tracking, mirror) -> None:
        if box is None:
            return
        x1, y1, x2, y2 = box
        cx = (x1 + x2) // 2
        put_caps(img, "VIEW", (x1 + 18, y1 + 22), 0.44, _MUTED, track=3)
        r = min((x2 - x1) // 2 - 20, (y2 - y1 - 76) // 2)
        if r >= 16:
            cy = y1 + 34 + r
            cv2.circle(img, (cx, cy), r, _LINE, 1, cv2.LINE_AA)
            for gx in (cx - r + 9, cx + r - 9):        # cardinal ticks
                cv2.line(img, (gx, cy - 5), (gx, cy + 5), _MUTED, 1, cv2.LINE_AA)
            for gy in (cy - r + 9, cy + r - 9):
                cv2.line(img, (cx - 5, gy), (cx + 5, gy), _MUTED, 1, cv2.LINE_AA)

            roll = pose_out["roll"] if tracking else 0.0
            span = int(r * 0.82)
            half = int(span * math.cos(math.radians(roll)))
            lift = int(span * math.sin(math.radians(roll)))
            # Screen y grows downwards, and a mirror flips x: both cancel
            # for the horizon, so roll reads as "the line follows your head".
            cv2.line(img, (cx - half, cy + lift), (cx + half, cy + lift),
                     _GOLD if tracking else _MUTED, 2, cv2.LINE_AA)

            dx = int(clamp_frac(pose_out["yaw"], self.yaw_range) * (r - 12))
            dy = int(-clamp_frac(pose_out["pitch"], self.pitch_range) * (r - 12))
            if mirror:
                dx = -dx
            dot = (cx + dx, cy + dy)
            cv2.line(img, (cx, cy), dot, _MUTED, 1, cv2.LINE_AA)
            cv2.circle(img, dot, 5, _OK if tracking else _MUTED, -1, cv2.LINE_AA)

        label = f"ROLL  {pose_out['roll']:+.0f} deg" if tracking else "ROLL  --"
        put_right(img, label, (x2 - 18, y2 - 14), 0.46, _MUTED, 1, _FONT_SMALL)

    # ==================================================================
    # bottom strip
    def _draw_strip(self, img, w, h, strip, status, fps) -> None:
        if strip is None:
            return
        x1, y1, x2, _ = strip
        left, right = x1 + 20, x2 - 20
        mid = y1 + _STRIP_H // 2

        put_run(img, "C  calibrate     R  recenter     H  help     Q  quit",
                (left, mid + 5), 0.5, _MUTED, 1, _FONT_SMALL)
        if status:
            put_run_right(img, status, (right, mid + 5), 0.5, _OK, 1, _FONT_SMALL)
        else:
            put_right(img, f"{w}x{h}   {fps:4.1f} fps", (right, mid + 5), 0.5,
                      _MUTED, 1, _FONT_SMALL)

    # ==================================================================
    # wizard
    def _draw_wizard(self, img, w, h, wizard, box) -> None:
        if wizard is None:
            return
        if wizard.cancelled:
            self._draw_banner(img, w, h, "calibration cancelled - press C to retry",
                              _WARN)
            return
        if wizard.done:
            self._draw_banner(img, w, h, "calibration saved - press C to redo",
                              _OK)
            return
        if box is None:
            return

        x1, y1, x2, y2 = box
        left, right = x1 + _PAD, x2 - _PAD
        step = wizard.step
        index = min(max(0, getattr(wizard, "index", 0)), len(STEPS))
        total = len(STEPS)
        finder = 112
        finder_x = right - finder

        put_caps(img, "CALIBRATION", (left, y1 + 28), 0.46, _MUTED, track=3)
        put_caps_right(img, f"STEP {index + 1} OF {total}", (right, y1 + 28), 0.46,
                       _MUTED, track=3)

        prompt = step.prompt if step is not None else "All done"
        put_run(img, prompt, (left, y1 + 72),
                fit_scale(prompt, finder_x - left - 30), _TEXT, 1, _FONT)

        # progress: pips joined by a rail, filled up to the current step
        py, pw = y2 - 30, 12
        for i in range(total):
            px = left + i * (pw * 2 + 8)
            if i:
                cv2.line(img, (px - pw - 4, py), (px, py),
                         _OK if i <= index else _LINE, 2, cv2.LINE_AA)
            cv2.circle(img, (px + pw, py), 9, _PANEL_2, -1, cv2.LINE_AA)
            if i <= index:
                cv2.circle(img, (px + pw, py), 5, _OK, -1, cv2.LINE_AA)
        put_run_right(img, "SPACE  capture      ESC  skip", (finder_x - 28, y2 - 26),
                      0.46, _MUTED, 1, _FONT_SMALL)

        # viewfinder: where to point, big enough to read across a desk
        fy1, fy2 = y1 + 16, y2 - 16
        fcx, fcy = finder_x + finder // 2, (fy1 + fy2) // 2
        round_rect(img, (finder_x, fy1), (right, fy2), 14, _BG)
        cv2.line(img, (finder_x + 6, fcy), (right - 6, fcy), _LINE, 1, cv2.LINE_AA)
        cv2.line(img, (fcx, fy1 + 6), (fcx, fy2 - 6), _LINE, 1, cv2.LINE_AA)
        if step is not None and step.dot is not None:
            dx = int(finder_x + step.dot[0] * finder)
            dy = int(fy1 + step.dot[1] * (fy2 - fy1))
            cv2.circle(img, (dx, dy), 18, _OK, 2, cv2.LINE_AA)
            cv2.circle(img, (dx, dy), 5, _OK, -1, cv2.LINE_AA)

    def _draw_banner(self, img, w, h, text: str, colour) -> None:
        """A single centred pill for one-off confirmations."""
        tw = text_w(text, 0.54, 1, _FONT)
        x1, x2 = w // 2 - tw // 2 - 20, w // 2 + tw // 2 + 20
        y = h - _MARGIN - _STRIP_H - _MARGIN - 46
        if y < _MARGIN:
            y = h - _MARGIN - 46
        round_rect(img, (x1, y), (x2, y + 42), 13, _PANEL)
        put_right(img, text, (x2 - 20, y + 28), 0.54, colour, 1)

    # ==================================================================
    # centred cards
    def _draw_no_face(self, img, w, h) -> None:
        x1, y1 = (w - _NO_FACE_W) // 2, (h - _NO_FACE_H) // 2
        put(img, "NO FACE DETECTED", (x1 + _PAD, y1 + 44), 0.72, _WARN, 1)
        for i, line in enumerate(NO_FACE_LINES):
            put(img, line, (x1 + _PAD, y1 + 82 + i * 26), 0.52,
                _TEXT if i == 0 else _MUTED, 1, _FONT_SMALL)

    def _draw_help(self, img, w, h) -> None:
        x1, y1 = (w - _HELP_W) // 2, (h - _HELP_H) // 2
        put(img, "KEYS", (x1 + _PAD, y1 + 46), 0.72, _TEXT, 1)
        y = y1 + 80
        for key, what in HELP_LINES:
            round_rect(img, (x1 + _PAD, y - 17), (x1 + _PAD + 36, y + 2), 6,
                       _PANEL_2)
            put(img, key, (x1 + _PAD + 13, y - 1), 0.5, _TEXT, 1, _FONT_SMALL)
            put(img, what, (x1 + _PAD + 56, y), 0.54, _MUTED, 1, _FONT_SMALL)
            y += 38

    # ==================================================================
    # compact HUD (no camera image, small, always-on-top friendly)
    def _compact_canvas(self, pose_out, tracking, fps, status,
                        chips=()) -> np.ndarray:
        w, h = 468, 268
        # Fill the whole canvas with the shadow colour: the rounded card
        # inside it then reads as rounded against the window, because the
        # window shows the image and nothing else.
        img = np.full((h, w, 3), _BG, np.uint8)
        round_rect(img, (2, 2), (w - 3, h - 3), 16, _PANEL)
        round_rect(img, (2, 2), (w - 3, h - 3), 16, _LINE, 1)

        colour = _OK if tracking else _WARN
        label = "TRACKING" if tracking else "NO FACE"
        cv2.circle(img, (32, 36), 6, colour, -1, cv2.LINE_AA)
        put_run(img, label, (46, 41), 0.52, colour, 1, _FONT_SMALL)
        put_right(img, f"{fps:4.1f} fps", (w - 20, 41), 0.46, _MUTED, 1,
                  _FONT_SMALL)
        cv2.line(img, (20, 58), (w - 20, 58), _LINE, 1, cv2.LINE_AA)

        x0, mw = 20, 288
        for i, (lbl, value, full, legend, col) in enumerate((
                ("YAW", pose_out["yaw"], self.yaw_range, "+ left", _OK),
                ("PITCH", pose_out["pitch"], self.pitch_range, "+ up", _GOLD),
                ("ROLL", pose_out["roll"], self.roll_range, "+ tilt", _GOLD))):
            draw_meter(img, x0, 88 + i * 42, mw, label=lbl, value=value,
                       full=full, legend=legend, colour=col, tracking=tracking)

        self._draw_gauge(img, (312, 62, w - 16, 212), pose_out, tracking, False)

        cv2.line(img, (20, h - 54), (w - 20, h - 54), _LINE, 1, cv2.LINE_AA)
        put_run(img, "x {:+5.1f}  y {:+5.1f}  z {:+5.1f} cm".format(
            pose_out["x"], pose_out["y"], pose_out["z"]),
            (20, h - 24), 0.46, _MUTED, 1, _FONT_SMALL)
        if status:
            put_right(img, status, (w - 20, h - 24), 0.44, _OK, 1, _FONT_SMALL)
        elif chips:
            put_right(img, " ".join(str(n).upper() for n, _ in chips),
                      (w - 20, h - 24), 0.42, _MUTED, 1, _FONT_SMALL)
        return img

    # ==================================================================
    # gaze trail
    def _update_trail(self, pose_out, tracking: bool) -> None:
        if not tracking:
            self._trail.clear()
            return
        self._trail.append((clamp_frac(pose_out["yaw"], self.yaw_range),
                            clamp_frac(pose_out["pitch"], self.pitch_range)))
        if len(self._trail) > _TRAIL_LEN:
            del self._trail[:len(self._trail) - _TRAIL_LEN]

    def _draw_trail(self, img, mirror: bool) -> None:
        """Recent gaze, projected onto the frame and fading out behind.

        Drawn from the calibrated axes rather than from the landmark
        positions so it stays put when the mesh is hidden, and it makes
        head jitter visible at a glance instead of hiding it in the meters.
        """
        n = len(self._trail)
        if n < 2:
            return
        h, w = img.shape[:2]

        def pt(s):
            x = int(w * 0.5 + s[0] * w * 0.3)
            y = int(h * 0.5 - s[1] * h * 0.3)
            return (w - x if mirror else x, y)

        for i in range(1, n):
            f = (i + 1) / n
            cv2.line(img, pt(self._trail[i - 1]), pt(self._trail[i]),
                     _lerp(_TRAIL_OLD, _TRAIL_NEW, f),
                     2 if f < 0.65 else 3, cv2.LINE_AA)
        cv2.circle(img, pt(self._trail[-1]), 6, _TRAIL_NEW, -1, cv2.LINE_AA)

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
            # Soft glow: a wide dim pass under a thin bright one. The dim
            # pass is what stops the contours reading as neon tubing.
            cv2.polylines(img, [pts], closed, _lerp(_GLOW, colour, 0.3),
                          thick + 2, cv2.LINE_AA)
            cv2.polylines(img, [pts], closed, colour, thick, cv2.LINE_AA)

        if self.show_mesh:
            # Dense mesh as faint dots, every third point to keep it cheap
            # and quiet - at every other point it read as sensor noise.
            for i in range(0, len(lms), 3):
                x, y = pt(i)
                cv2.circle(img, (x, y), 1, (74, 100, 92), -1)

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
        cv2.line(img, p0, p1, _OK if tracking else _MUTED, 2, cv2.LINE_AA)