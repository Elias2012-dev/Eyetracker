"""Rendering tests for the HUD.

The overlay is the whole UI of the packaged exe, so the things a user
reads while a game has the focus get checked here: the meter readout must
never sit on top of its own bar, the fill must grow from the centre, and
both layouts must survive the mirror flip.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from eyetrack.calibrate import STEPS
from eyetrack.config import Config
from eyetrack.overlay import (
    _RAIL_W,
    chip_rows,
    _PAD,
    _TRAIL_LEN,
    Overlay,
    chip_w,
    clamp_frac,
    draw_chip,
    draw_meter,
    fit_scale,
    meter_fill,
    round_rect,
    text_w,
)

_TRACK = {"yaw": -18.5, "pitch": 7.0, "roll": -9.0, "x": -4.2, "y": 1.5, "z": 8.0}
_CHIPS = (("TrackIR", True), ("Minecraft", True), ("Mouse", False))


class _Pose:
    """Stand-in pose with no landmarks, which is the no-tracking case."""

    detected = False
    landmarks = None


def _frame(w: int = 1280, h: int = 720) -> np.ndarray:
    return np.full((h, w, 3), 60, np.uint8)


def _config_from_args(args, cfg=None):
    """Apply parsed CLI args to a Config the way __main__ does."""
    from eyetrack.config import Config

    if cfg is None:
        cfg = Config()
    if getattr(args, "compact", None) is not None:
        cfg.overlay.compact = args.compact
    if getattr(args, "top_most", None) is not None:
        cfg.overlay.top_most = args.top_most
    return cfg


_GUTTER = 62   # width reserved at the right of a meter for the readout


# ---------------------------------------------------------------- helpers
def test_clamp_frac_pins_to_unit_range():
    assert clamp_frac(5.0, 40.0) == pytest.approx(0.125)
    assert clamp_frac(999.0, 40.0) == 1.0
    assert clamp_frac(-999.0, 40.0) == -1.0
    assert clamp_frac(5.0, 0.0) == 0.0, "a zero range must not divide by zero"


def test_meter_fill_anchors_at_the_centre():
    x, y, w, h = 10, 20, 100, 12
    # Dead centre: a zero-width sliver at the middle of the track.
    x1, _, x2, _ = meter_fill(x, y, w, h, 0.0)
    assert (x1, x2) == (x + w // 2, x + w // 2)
    # Positive grows right, negative grows left, both from the centre.
    x1, _, x2, _ = meter_fill(x, y, w, h, 0.5)
    assert x1 == x + w // 2 and x2 > x + w // 2
    x1, _, x2, _ = meter_fill(x, y, w, h, -0.5)
    assert x1 < x + w // 2 and x2 == x + w // 2


def test_meter_fill_clamps_beyond_the_range():
    x, y, w, h = 10, 20, 100, 12
    x1, _, x2, _ = meter_fill(x, y, w, h, 9.0)
    assert (x1, x2) == (x + w // 2, x + w)


def test_round_rect_covers_its_interior():
    img = np.zeros((60, 60, 3), np.uint8)
    round_rect(img, (10, 10), (50, 40), 8, (200, 200, 200))
    # Corners are rounded away, so sample just inside the box, not on it.
    assert img[25, 12].max() > 0
    assert img[25, 47].max() > 0
    assert img[0, 0].max() == 0, "must not paint outside the rounded rect"


# ------------------------------------------------------------------ meters_GUTTER = 62   # width reserved at the right of a meter for the readout


def _draw(value: float, full: float = 40.0, w: int = 256,
          tracking: bool = True):
    img = np.full((80, 520, 3), 34, np.uint8)
    x, y = 34, 20
    draw_meter(img, x, y, w, label="YAW", value=value,
               full=full, legend="+ = your left", colour=(120, 226, 148),
               tracking=tracking)
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return grey, x, y, w


@pytest.mark.parametrize("value", [-18.5, 0.0, 18.5, 40.0, -40.0])
def test_meter_readout_never_overlaps_its_own_bar(value):
    """The number must sit in the gutter, never on the track.

    Printed on top of the fill it was unreadable exactly when the value was
    large enough to matter. Checked with tracking off so the track holds no
    fill: then *any* bright pixel inside it is stray text, apart from the
    centre tick.
    """
    grey, x, y, w = _draw(value, tracking=False)
    track_w = w - _GUTTER
    centre = x + track_w // 2

    band = grey[y + 8:y + 20, x:x + track_w]        # inside the track
    bright = np.where(band.max(axis=0) > 120)[0]
    stray = [c for c in bright if abs(c - (centre - x)) > 3]
    assert not stray, (
        f"text painted inside the track at offsets {stray} "
        f"(track x[{x},{x + track_w}), centre tick at {centre})")

    # ...and the readout really is drawn, in the gutter to the right.
    gutter = grey[y + 8:y + 22, x + track_w:]
    assert gutter.max() > 120, "the readout is missing from its gutter"


@pytest.mark.parametrize("value,side", [(18.5, "right"), (-18.5, "left")])
def test_meter_fill_visible_on_the_correct_side(value, side):
    grey, x, y, w = _draw(value)
    track_w = w - _GUTTER
    band = grey[y + 9:y + 19, x:x + track_w].astype(int)
    left_half = band[:, :track_w // 2].mean()
    right_half = band[:, track_w // 2:].mean()
    if side == "right":
        assert right_half > left_half, "a positive value must fill rightwards"
    else:
        assert left_half > right_half, "a negative value must fill leftwards"


# ------------------------------------------------------------------- rail
@pytest.mark.parametrize("chips", [
    (),
    (("TrackIR", True),),
    (("TrackIR", True), ("Minecraft", True), ("Mouse", False)),
    tuple((f"OUTPUT NUMBER {i}", True) for i in range(7)),
])
def test_rail_content_always_fits_inside_its_card(chips):
    """The rail card must be exactly as tall as the content drawn in it.

    The card height is computed by walking the same rhythm the renderer
    walks; if the two ever drift, the leftover space reads as a rendering
    bug (a card that stops halfway down its own contents).
    """
    ov = Overlay()
    img = np.full((900, 900, 3), 60, np.uint8)
    rail = ov._layout(900, 900, None, chips)["rail"]
    bottom = ov._draw_rail(img, rail, _TRACK, True, 58.4, chips)
    assert bottom <= rail[3], (
        f"rail content reached {bottom}, past the card bottom {rail[3]}")
    assert rail[3] - bottom < 2 * _PAD, "the card is much taller than its content"


@pytest.mark.parametrize("h", [480, 600, 720, 1080])
def test_full_view_survives_any_capture_size(h):
    """A 4:3 or a 640x480 webcam must not push the cards off the frame."""
    w = int(h * 16 / 9)
    img = Overlay()._compose(np.full((h, w, 3), 60, np.uint8), _Pose(), _TRACK,
                             False, 30.0, False, None, "", _CHIPS)
    assert img.shape == (h, w, 3)


def test_the_rail_is_left_of_the_view_gauge_at_every_width():
    """The two edge cards must never collide, or the gauge covers numbers."""
    ov = Overlay()
    for w in (640, 800, 1024, 1280, 1920):
        boxes = ov._layout(w, 720, None, _CHIPS)
        rail, gauge = boxes["rail"], boxes["gauge"]
        assert gauge is None or gauge[0] > rail[2], (
            f"gauge at {gauge[0]} overlaps the rail ending at {rail[2]} "
            f"in a {w}px-wide frame")


def test_output_chips_are_labelled_and_styled_from_their_state():
    img = np.full((300, 600, 3), 30, np.uint8)
    on = draw_chip(img, 10, 10, "TrackIR", (120, 226, 148), on=True)
    off = draw_chip(img, 10 + on + 8, 10, "Mouse", (120, 226, 148), on=False)
    assert chip_w("TrackIR") == on, "the reported width must match the drawing"
    # The dim state really is dimmer than the lit one.
    lit = img[10:38, 10:10 + on]
    dark = img[10:38, 10 + on + 8:10 + on + 8 + off]
    assert lit.max() > dark.max() + 40


# ------------------------------------------------------------------ states
@pytest.mark.parametrize("compact", [False, True])
def test_help_and_wizard_draw_in_both_layouts(compact):
    """Every screen has to survive a frame; a crash here is a frozen app."""
    ov = Overlay(compact=compact)
    ov.help_visible = True
    _draw_wizard_screen(ov, _TRACK, _CHIPS)


def _draw_wizard_screen(ov, values, chips):
    class _W:
        index = 1
        captures = {"center": object()}
        done = False
        cancelled = False
        step = STEPS[1]
        prompt = "Turn your head LEFT (keep body still)"
    if ov.compact:
        ov._compact_canvas(values, True, 58.4, "", chips)
    else:
        ov._compose(np.full((720, 1280, 3), 60, np.uint8), _Pose(), values,
                    True, 58.4, False, _W(), "", chips)
        ov.help_visible = False
        ov._compose(np.full((720, 1280, 3), 60, np.uint8), _Pose(), values,
                    True, 58.4, False, _W(), "", chips)
        ov.help_visible = True


def test_a_cancelled_wizard_does_not_leave_the_card_up():
    """ESC has to clear the wizard card, not freeze it on the last step."""

    class _W:
        index = 3
        captures = {}
        done = False
        cancelled = True
        step = None
        prompt = "cancelled"

    ov = Overlay()
    boxes = ov._layout(1280, 720, _W())
    assert boxes["wizard"] is None, "a cancelled wizard must give up the slot"
    ov._compose(np.full((720, 1280, 3), 60, np.uint8), _Pose(), _TRACK, True,
                58.4, False, _W(), "", _CHIPS)


def test_long_prompts_shrink_to_fit_instead_of_running_off_the_card():
    """The wizard prompt is a sentence; a long one must not be clipped."""
    narrow = fit_scale("Look UP (chin up) - press SPACE", 200)
    wide = fit_scale("Look UP", 200)
    assert narrow < wide, "a longer prompt must use a smaller font"
    assert text_w("Look UP (chin up) - press SPACE", narrow, 1, cv2.FONT_HERSHEY_DUPLEX) <= 200


def test_mirror_only_flips_the_video_not_the_cards():
    """The HUD is screen furniture: its text must read normally either way.

    The face mesh, the gaze trail and the gauge dot follow the flipped
    preview; the cards are anchored to the frame, so mirroring their
    layout put the header readout outside its own card.
    """
    rail = Overlay()._layout(1280, 720, None, _CHIPS)["rail"]
    reads = []
    for mirror in (False, True):
        img = Overlay()._compose(_frame(), _Pose(), _TRACK, True, 58.4, mirror,
                                 None, "", _CHIPS)
        grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # the fps readout sits at the rail's right edge in both modes
        head = grey[rail[1] + 50:rail[1] + 80, rail[2] - 90:rail[2] - 10]
        assert head.max() > 100, "the header readout left its card"
        reads.append(head)
    assert np.array_equal(reads[0], reads[1]), (
        "mirroring changed the rail, which is anchored to the frame")


# ------------------------------------------------------------------ trail
def test_the_trail_grows_while_tracking_and_resets_when_the_face_lost():
    ov = Overlay()
    for i in range(_TRAIL_LEN + 20):
        ov._update_trail({"yaw": i * 0.5, "pitch": 0.0}, True)
    assert len(ov._trail) == _TRAIL_LEN, "the trail must stay bounded"
    ov._update_trail({"yaw": 0.0, "pitch": 0.0}, False)
    assert ov._trail == [], "a lost face must not leave a stale trail behind"


def test_the_trail_lands_inside_the_frame():
    """Clamped to the axes: a saturated yaw cannot draw off-screen."""
    ov = Overlay()
    ov._update_trail({"yaw": 999.0, "pitch": -999.0}, True)
    ov._update_trail({"yaw": -999.0, "pitch": 999.0}, True)
    img = np.full((720, 1280, 3), 0, np.uint8)
    ov._draw_trail(img, mirror=False)
    assert img.any(), "a saturated trail must still be drawn"
    painted = np.argwhere(img.max(axis=2) > 0)
    assert painted[:, 0].min() >= 0 and painted[:, 0].max() < 720
    assert painted[:, 1].min() >= 0 and painted[:, 1].max() < 1280


# ----------------------------------------------------------------- layouts
@pytest.mark.parametrize("mirror", [False, True])
def test_full_view_renders_at_the_camera_resolution(mirror):
    img = Overlay()._compose(_frame(), _Pose(), _TRACK, True, 58.4, mirror,
                             None, "")
    assert img.shape == (720, 1280, 3)
    # Something was actually drawn over the flat background.
    assert len(np.unique(img.reshape(-1, 3), axis=0)) > 20


def test_compact_panel_is_small_and_top_most_by_default():
    ov = Overlay()
    img = ov._compact_canvas(_TRACK, True, 58.4, "")
    h, w = img.shape[:2]
    assert (w, h) == (468, 268)
    assert w < 640, "the compact panel must stay glanceable, not a window"
    assert ov.top_most is False, "always-on-top is opt-in, never forced"


def test_no_tracking_draws_without_raising():
    img = Overlay()._compose(_frame(), _Pose(), _TRACK, False, 0.0, False,
                             None, "")
    assert img.shape == (720, 1280, 3)


def test_meter_ranges_come_from_the_constructor():
    """Wide ranges must read as small deflections, not pinned needles."""
    img = Overlay(yaw_range=90.0)._compose(_frame(), _Pose(), _TRACK, True,
                                          58.4, False, None, "")
    assert img.shape == (720, 1280, 3)


# ------------------------------------------------------- config / CLI wiring
def test_overlay_options_persist_across_a_config_round_trip(tmp_path):
    path = tmp_path / "eyetrack.json"
    cfg = Config()
    cfg.overlay.compact = True
    cfg.overlay.top_most = True
    cfg.save(path)

    loaded = Config.load(path)
    assert loaded.overlay.compact is True
    assert loaded.overlay.top_most is True


def test_overlay_options_default_to_the_friendly_choices():
    cfg = Config()
    assert cfg.overlay.compact is False, "full view by default"
    assert cfg.overlay.top_most is False, "never steal z-order uninvited"


@pytest.mark.parametrize("flag,field,expected", [
    ("--compact", "compact", True),
    ("--no-compact", "compact", False),
    ("--top-most", "top_most", True),
    ("--no-top-most", "top_most", False),
])
def test_cli_flags_reach_the_config(flag, field, expected):
    """Each flag must land in OverlayConfig, not merely parse."""
    import eyetrack.__main__ as cli

    args = cli._parse_args([flag])
    assert getattr(args, field) is expected, "flag did not set the dest"

    cfg = _config_from_args(args)
    assert getattr(cfg.overlay, field) is expected, "flag never reached the config"


def test_omitting_the_flags_leaves_the_saved_config_untouched():
    """Absent flags must be None so a saved preference is not overwritten."""
    import eyetrack.__main__ as cli

    cfg = Config()
    cfg.overlay.compact = True
    cfg.overlay.top_most = True
    _config_from_args(cli._parse_args([]), cfg)
    assert cfg.overlay.compact is True
    assert cfg.overlay.top_most is True


def test_compact_flag_is_wired_into_overlay_construction():
    """app.run must forward the config's overlay choices to Overlay()."""
    import inspect

    from eyetrack import app

    src = inspect.getsource(app.run)
    for kw in ("compact=", "top_most=", "yaw_range=", "pitch_range=",
               "roll_range="):
        assert kw in src, f"run() does not forward {kw} to Overlay"

# --------------------------------------------------------- output chips
class _Out:
    def __init__(self, name, **attrs):
        self.name = name
        for k, v in attrs.items():
            setattr(self, k, v)


@pytest.mark.parametrize("tracking", [True, False])
def test_outputs_become_labelled_chips(tracking):
    from eyetrack.app import _output_chips

    outputs = [_Out("game-link"), _Out("udp"), _Out("mouse", active=True)]
    chips = _output_chips(outputs, tracking)
    assert [c[0] for c in chips] == ["TrackIR", "Minecraft", "Mouse"]
    assert all(on is tracking for _, on in chips), (
        "a sink is only sending while a face is tracked")


def test_a_disarmed_mouse_is_shown_as_off_even_while_tracking():
    from eyetrack.app import _output_chips

    chips = _output_chips([_Out("mouse", active=False)], True)
    assert chips == (("Mouse", False),)


def test_chip_labels_are_short_enough_to_share_one_row():
    """Three sinks have to fit side by side, or the card grows a dead row."""
    from eyetrack.app import CHIP_LABELS

    chips = [CHIP_LABELS[n] for n in ("game-link", "udp", "mouse")]
    assert chip_rows(chips, _RAIL_W - 2 * _PAD) == 1, (
        f"{chips} wrap: {sum(chip_w(c) for c in chips)} px of chips in "
        f"{_RAIL_W - 2 * _PAD} px")


def test_chip_rows_grows_when_the_chips_do_not_fit():
    many = [f"OUTPUT {i}" for i in range(8)]
    assert chip_rows(many, _RAIL_W - 2 * _PAD) > 1
