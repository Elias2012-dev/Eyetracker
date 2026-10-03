"""The settings window's theme.

The colour choices are the whole point of this module, and nobody looks at
them in CI, so the accessibility of every foreground/background pair is
asserted instead of eyeballed.
"""

from __future__ import annotations

import os

import pytest

tk = pytest.importorskip("tkinter")

from eyetrack import theme as T

# AA for body text. Large/bold UI labels could get away with AA Large (3.0),
# but there is no reason to ship text that hard to read.
AA = 4.5


@pytest.fixture(scope="module")
def root():
    # Creating a Tk window is occasionally refused outright when several
    # roots have just been created and destroyed in the same session, which
    # silently skipped 12 tests about one run in six. Retry once before
    # concluding the machine has no display.
    last = None
    for _ in range(2):
        try:
            win = tk.Tk()
        except Exception as exc:
            last = exc
            continue
        win.withdraw()
        yield win
        win.destroy()
        return
    pytest.skip(f"no display available: {last}")


# ------------------------------------------------------------- contrast
def test_contrast_ratio_matches_the_wcag_extremes():
    # Anchors: these two are fixed by the formula, so a wrong implementation
    # cannot pass by accident.
    assert T.contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0, abs=0.01)
    assert T.contrast_ratio("#FFFFFF", "#FFFFFF") == pytest.approx(1.0, abs=0.01)


def test_contrast_ratio_is_symmetric():
    assert T.contrast_ratio(T.TEXT, T.BG) == pytest.approx(
        T.contrast_ratio(T.BG, T.TEXT))


@pytest.mark.parametrize("name,fg,bg", [
    ("body text on the backdrop", T.TEXT, T.BG),
    ("body text on a card", T.TEXT, T.CARD),
    ("secondary text on a card", T.MUTED, T.CARD),
    ("secondary text on the backdrop", T.MUTED, T.BG),
    ("label on the green button", T.ON_BADGE, T.ACCENT),
    ("green accent on the backdrop", T.ACCENT, T.BG),
    ("amber warning on the backdrop", T.WARN, T.BG),
])
def test_every_text_colour_is_readable(name, fg, bg):
    ratio = T.contrast_ratio(fg, bg)
    assert ratio >= AA, f"{name}: {fg} on {bg} is only {ratio:.2f}:1"


def test_luminance_rejects_a_bad_colour():
    with pytest.raises(ValueError):
        T.relative_luminance("#fff")
    with pytest.raises(ValueError):
        T.relative_luminance("not a colour")


def test_the_palette_is_the_huds_palette():
    """The two windows should look like one program.

    overlay.py holds its colours as BGR tuples; these are the same values
    as hex, checked literally so a drift in either file is caught.
    """
    assert T.ACCENT == "#94E27A"      # overlay _OK   = (122, 226, 148)
    assert T.WARN == "#F0AA56"        # overlay _WARN = (86, 170, 240)
    assert T.TEXT == "#FAF5F3"        # overlay _TEXT = (243, 245, 250)
    assert T.CARD == "#27191E"        # overlay _PANEL = (30, 25, 39)


# ------------------------------------------------------------ geometry
def test_round_rect_draws_a_closed_shape_inside_its_bounds(root):
    canvas = tk.Canvas(root, width=100, height=60)
    item = T.round_rect(canvas, 5, 5, 95, 55, 12, fill="#123456", width=0)
    coords = canvas.coords(item)
    assert len(coords) >= 8, "a rounded rect needs at least four corners"
    assert len(coords) % 2 == 0, "polygon coordinates come in x,y pairs"
    xs, ys = coords[0::2], coords[1::2]
    assert 5 - 0.5 <= min(xs) and max(xs) <= 95 + 0.5
    assert 5 - 0.5 <= min(ys) and max(ys) <= 55 + 0.5


def test_round_rect_radius_cannot_exceed_half_the_box(root):
    """A radius bigger than the box would invert the corners and draw a
    bow tie instead of a box."""
    canvas = tk.Canvas(root, width=40, height=20)
    item = T.round_rect(canvas, 0, 0, 40, 20, 999, fill="#ffffff", width=0)
    coords = canvas.coords(item)
    assert min(coords[0::2]) >= -0.5 and max(coords[0::2]) <= 40.5
    assert min(coords[1::2]) >= -0.5 and max(coords[1::2]) <= 20.5


def test_round_rect_rounds_every_corner(root):
    canvas = tk.Canvas(root, width=80, height=80)
    item = T.round_rect(canvas, 4, 4, 76, 76, 10, fill="#abcdef", width=0)
    # A plain rectangle would use 4 points; smoothing needs many.
    assert len(canvas.coords(item)) > 20


# ------------------------------------------------------------- controls
@pytest.mark.skipif(os.environ.get("EYETRACK_HEADLESS") == "1",
                    reason="headless run")
def test_toggle_follows_its_variable_and_flips_on_click(root):
    var = tk.BooleanVar(master=root, value=False)
    sw = T.Toggle(root, var, on_text="on", off_text="off")
    root.update()

    track = sw.canvas.itemcget(sw._track, "fill")
    assert track.lower() == T.LINE.lower(), "off switches sit on the track colour"

    var.set(True)
    root.update()
    assert sw.canvas.itemcget(sw._track, "fill").lower() == T.ACCENT.lower()

    # The knob must actually travel, not just recolour.
    before = sw.canvas.coords(sw._knob)
    sw._click()
    root.update()
    assert sw.canvas.itemcget(sw._track, "fill").lower() == T.LINE.lower()
    assert sw.canvas.coords(sw._knob) != before
    assert var.get() is False, "clicking the switch must change the variable"


def test_toggle_runs_its_command(root):
    calls = []
    var = tk.BooleanVar(master=root, value=False)
    sw = T.Toggle(root, var, command=lambda: calls.append(1))
    sw._click()
    root.update()
    assert calls == [1]


def test_toggle_label_follows_the_state(root):
    var = tk.BooleanVar(master=root, value=False)
    sw = T.Toggle(root, var, on_text="on", off_text="off")
    root.update()
    text = sw.canvas.itemcget("lbl", "text")
    var.set(True)
    root.update()
    assert text != sw.canvas.itemcget("lbl", "text")


@pytest.mark.parametrize("tone,expected_dot", [
    ("idle", T.MUTED),
    ("live", T.ACCENT),
    ("warn", T.WARN),
])
def test_status_pill_colours_its_dot_by_tone(root, tone, expected_dot):
    pill = T.StatusPill(root, text="x")
    pill.set("Tracking", tone)
    assert pill.canvas.itemcget(pill._dot, "fill").lower() == expected_dot.lower()


def test_status_pill_shows_the_wording_not_just_a_colour(root):
    """Colour alone must never be the only signal."""
    pill = T.StatusPill(root, text="Ready")
    pill.set("Tracking", "live")
    assert pill.canvas.itemcget(pill._text, "text") == "Tracking"


# ---------------------------------------------------------------- theme
def test_apply_theme_configures_the_stock_widgets(root):
    style = T.apply_theme(root)
    bg = style.lookup("TFrame", "background")
    fg = style.lookup("TLabel", "foreground")
    assert bg.lower() == T.BG.lower()
    assert fg.lower() == T.TEXT.lower()


def test_apply_theme_uses_clam_when_present(root):
    style = T.apply_theme(root)
    if "clam" in style.theme_names():
        assert style.theme_use() == "clam"