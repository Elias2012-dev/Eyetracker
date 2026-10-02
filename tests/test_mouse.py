"""Mouse-emulation output: pure mapping maths + output wiring (Windows)."""

import sys

import pytest

from eyetrack.config import Config, MouseConfig
from eyetrack.outputs import build_outputs
from eyetrack.outputs.mouse import (MouseMapper, MouseOutput, parse_toggle_key,
                                    _shape)

POSE = {"yaw": 0.0, "pitch": 0.0, "roll": 0.0, "x": 0.0, "y": 0.0, "z": 0.0}

needs_windows = pytest.mark.skipif(sys.platform != "win32",
                                   reason="mouse emulation is Windows-only")


def pose(**kw):
    p = dict(POSE)
    p.update(kw)
    return p


# --------------------------------------------------------------------- maths
def test_first_frame_only_anchors():
    m = MouseMapper(sensitivity=5.0, v_sensitivity=5.0, deadzone_deg=0.0)
    assert m.update(pose(yaw=10.0)) == (0, 0)     # anchor, no jump
    dx, dy = m.update(pose(yaw=15.0))             # +5 deg -> 25 px
    assert (dx, dy) == (-25, 0)                   # look left -> cursor left


def test_pitch_up_moves_mouse_up():
    m = MouseMapper(sensitivity=5.0, v_sensitivity=4.0, deadzone_deg=0.0)
    m.update(pose())
    dx, dy = m.update(pose(pitch=10.0))           # 40 px
    assert (dx, dy) == (0, -40)                   # look up -> cursor up


def test_holding_a_pose_stops_the_cursor():
    m = MouseMapper(sensitivity=5.0, deadzone_deg=0.0)
    m.update(pose(yaw=20.0))
    m.update(pose(yaw=20.0))
    assert m.update(pose(yaw=20.0)) == (0, 0)


def test_returning_to_start_returns_the_cursor():
    m = MouseMapper(sensitivity=5.0, deadzone_deg=0.0)
    m.update(pose(yaw=0.0))
    away = m.update(pose(yaw=20.0))[0]            # look left: cursor left
    back = m.update(pose(yaw=0.0))[0]             # look back: cursor back
    assert (away, back) == (-100, 100)
    assert away + back == 0                       # net zero: no drift


def test_deadzone_swallows_centre_jitter():
    m = MouseMapper(sensitivity=5.0, deadzone_deg=3.0)
    m.update(pose(yaw=0.0))
    assert m.update(pose(yaw=1.5)) == (0, 0)      # inside the deadzone
    assert m.update(pose(yaw=-2.0)) == (0, 0)
    dx, _ = m.update(pose(yaw=5.0))               # 5 - 3 = 2 deg -> 10 px
    assert dx == -10


def test_shape_passthrough_when_disabled():
    assert _shape(-7.0, 0.0) == -7.0
    assert _shape(1.0, 2.0) == 0.0
    assert _shape(4.0, 2.0) == 2.0
    assert _shape(-4.0, 2.0) == -2.0


def test_invert_flips_both_axes():
    m = MouseMapper(sensitivity=5.0, v_sensitivity=5.0, deadzone_deg=0.0,
                    invert_x=True, invert_y=True)
    m.update(pose())
    dx, dy = m.update(pose(yaw=10.0, pitch=10.0))  # 10 deg * 5 px/deg
    assert (dx, dy) == (50, 50)                    # mirrored vs. the -50/-50 default


def test_face_lost_reanchors_without_jumping():
    m = MouseMapper(sensitivity=5.0, deadzone_deg=0.0)
    m.update(pose(yaw=10.0))
    assert m.update(pose(yaw=-20.0), tracking=False) == (0, 0)
    assert m.update(pose(yaw=-20.0)) == (0, 0)    # recovery re-anchors
    dx, _ = m.update(pose(yaw=-15.0))             # +5 deg relative
    assert dx == -25


def test_subpixel_motion_is_carried_not_rounded_away():
    m = MouseMapper(sensitivity=1.0, deadzone_deg=0.0)   # 1 px per degree
    m.update(pose(yaw=0.0))
    assert m.update(pose(yaw=0.4)) == (0, 0)
    assert m.update(pose(yaw=0.8)) == (0, 0)
    assert m.update(pose(yaw=1.2))[0] == -1       # 0.4+0.4+0.4 finally paid out


# ------------------------------------------------------------------- wiring
def test_parse_toggle_key():
    assert parse_toggle_key("F9") == 0x78
    assert parse_toggle_key("f1") == 0x70
    assert parse_toggle_key("F12") == 0x7B
    assert parse_toggle_key("HOME") == 0x24
    assert parse_toggle_key("k") == ord("K")
    for off in ("", "none", "NONE", "off", "  "):
        assert parse_toggle_key(off) is None
    with pytest.raises(ValueError):
        parse_toggle_key("F13")


def test_config_roundtrip_keeps_mouse_section(tmp_path):
    cfg = Config()
    cfg.mouse.enabled = True
    cfg.mouse.sensitivity = 7.5
    cfg.mouse.toggle_key = "F10"
    path = tmp_path / "eyetrack.json"
    cfg.save(path)

    loaded = Config.load(path)
    assert loaded.mouse.enabled is True
    assert loaded.mouse.sensitivity == 7.5
    assert loaded.mouse.toggle_key == "F10"


def test_build_outputs_wires_mouse_output():
    cfg = Config()
    assert "mouse" not in [o.name for o in build_outputs(cfg)]
    if sys.platform != "win32":
        pytest.skip("constructing the output requires Windows")
    cfg.mouse.enabled = True
    assert "mouse" in [o.name for o in build_outputs(cfg)]


@needs_windows
def test_sendinput_struct_is_the_x64_layout():
    import ctypes
    from eyetrack.outputs.mouse import INPUT

    if ctypes.sizeof(ctypes.c_void_p) != 8:
        pytest.skip("64-bit only")
    assert ctypes.sizeof(INPUT) == 40  # DWORD type + pad + 28-byte MOUSEINPUT


@needs_windows
def test_output_starts_disarmed_and_toggles():
    out = MouseOutput(MouseConfig(enabled=True, toggle_key="F9"))
    out.start()
    sent = []
    out._emit = lambda dx, dy: sent.append((dx, dy))
    assert out.active is False                 # safe default
    assert out.status_text() == "mouse: off [F9]"

    held = [True]
    out._key_pressed = lambda: held[0]
    out.send(pose(), True, 1.0)                # key press -> armed
    assert out.active is True
    out.send(pose(), True, 1.2)                # still held: no double toggle
    assert out.active is True

    held[0] = False
    out.send(pose(), True, 1.4)                # release
    held[0] = True
    out.send(pose(yaw=25.0), True, 1.6)        # second press -> disarmed
    assert out.active is False
    out.send(pose(yaw=25.0), True, 1.8)        # while disarmed: nothing moves
    assert sent == []


@needs_windows
def test_rearming_never_jumps_and_rate_limit_keeps_deltas():
    out = MouseOutput(MouseConfig(enabled=True, toggle_key="F9",
                                  sensitivity=5.0, deadzone_deg=0.0,
                                  rate_hz=10))
    out.start()
    sent = []
    out._emit = lambda dx, dy: sent.append((dx, dy))
    out.active = True                          # pretend it was armed
    out._key_pressed = lambda: False           # ...with no key held

    # Head moved while disarmed/lost: the first armed frame only anchors.
    out.send(pose(yaw=20.0), True, 1.0)
    assert sent == []

    # Rate limit: within 1/10 s nothing is emitted - but no delta is lost.
    out.send(pose(yaw=30.0), True, 1.001)
    assert sent == []
    out.send(pose(yaw=30.0), True, 1.15)
    assert sent == [(-50, 0)]

    # A face hiccup mid-stream re-anchors instead of snapping the cursor.
    out.send(pose(yaw=-40.0), False, 1.3)
    out.send(pose(yaw=-40.0), True, 1.45)
    out.send(pose(yaw=-40.0), True, 1.6)
    assert sent == [(-50, 0)]
