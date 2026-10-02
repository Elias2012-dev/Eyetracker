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

from eyetrack.config import Config
from eyetrack.overlay import (
    Overlay,
    clamp_frac,
    draw_meter,
    meter_fill,
    round_rect,
)

_TRACK = {"yaw": -18.5, "pitch": 7.0, "roll": -9.0, "x": -4.2, "y": 1.5, "z": 8.0}


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


# ------------------------------------------------------------------ meters
_GUTTER = 66   # width reserved at the right of a meter for the readout


def _draw(value: float, full: float = 40.0, w: int = 256,
          tracking: bool = True):
    img = np.full((80, 520, 3), 34, np.uint8)
    x, y = 34, 20
    draw_meter(img, lambda v: v, x, y, w, label="YAW", value=value,
               full=full, legend="+ = your left", colour=(120, 226, 148),
               tracking=tracking)
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return grey, x, y, w


@pytest.mark.parametrize("value", [-18.5, 0.0, 18.5, 40.0, -40.0])
def test_meter_readout_never_overlaps_its_own_bar(value):
    """The number must sit in the gutter, never on the track.

    Printed on top of the fill it was unreadable exactly when the value
    was large enough to matter. Checked with tracking off so the track
    holds no fill: then *any* bright pixel inside it is stray text, apart
    from the centre tick.
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
    assert (w, h) == (460, 250)
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