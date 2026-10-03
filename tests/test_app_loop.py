"""Integration tests for the main loop.

The HUD renders fine in isolation; what is easy to get wrong is the wiring
around it - whether ``H`` actually reaches the help panel, whether ``F``
persists the layout choice, whether an output sink shows up as a chip. So
these drive :func:`eyetrack.app.run` with a fake camera and a scripted
keystroke queue and check what came out the other end.
"""

from __future__ import annotations

import sys

import numpy as np
import pytest

from eyetrack import app
from eyetrack.config import Config


class FakeCapture:
    """A camera that always delivers the same face."""

    def __init__(self, frames=200):
        self.frames = frames
        self.released = False

    def isOpened(self):
        return True

    def read(self):
        if self.frames <= 0:
            return False, None
        self.frames -= 1
        return True, np.full((480, 640, 3), 90, np.uint8)

    def set(self, *_a):
        pass

    def get(self, *_a):
        return 0

    def release(self):
        self.released = True


class FakeEstimator:
    """Reports a detected face with real landmark-shaped data."""

    def __init__(self, detected=True):
        self.detected = detected
        self.closed = False
        self.landmarks = np.tile(np.linspace(0.2, 0.8, 478), (478, 1))

    def process(self, frame, t_ms=0):
        from eyetrack.pose import HeadPose

        pose = HeadPose()
        pose.detected = self.detected
        pose.landmarks = self.landmarks if self.detected else None
        pose.face_px = 120.0
        pose.proxy_yaw, pose.proxy_pitch = 0.2, -0.1
        return pose

    def close(self):
        self.closed = True


class FakeOverlay:
    """Records what it was asked to draw and replays a key script."""

    instances: list["FakeOverlay"] = []

    def __init__(self, *a, **kw):
        self.keys = list(kw.pop("keys", []))
        self.kwargs = kw
        self.help_visible = False
        self.compact = bool(kw.get("compact", False))
        self.show_mesh = bool(kw.get("show_mesh", True))
        self.frames = []
        self.chips = ()
        FakeOverlay.instances.append(self)

    def draw(self, frame, pose, pose_out, tracking, fps, mirror, wizard=None,
             status="", chips=(), show_help=None):
        self.frames.append((dict(pose_out), status, chips))
        self.chips = chips
        return self.keys.pop(0) if self.keys else -1

    def window_closed(self):
        return False

    def close(self):
        pass

    def toggle_help(self):
        self.help_visible = not self.help_visible
        return self.help_visible

    def toggle_mesh(self):
        self.show_mesh = not self.show_mesh
        return self.show_mesh

    def set_compact(self, compact):
        self.compact = bool(compact)
        return self.compact


class FakeOutput:
    def __init__(self, name, **attrs):
        self.name = name
        self.sent = []
        self.started = False
        for k, v in attrs.items():
            setattr(self, k, v)

    def start(self):
        self.started = True

    def send(self, pose, tracking, t):
        self.sent.append((dict(pose), tracking))

    def close(self):
        pass


@pytest.fixture
def rig(monkeypatch, tmp_path):
    """Run app.run against fakes; returns the recorder for assertions."""
    recorder = {"outputs": [FakeOutput("game-link"), FakeOutput("udp")]}

    def fake_overlay(*a, **kw):
        # three frames of "no key", then quit: enough to watch the loop
        # settle and the transient status clear itself
        kw.setdefault("keys", [-1, -1, ord("q")])
        return FakeOverlay(*a, **kw)

    monkeypatch.setattr(app, "open_camera", lambda cfg: FakeCapture())
    monkeypatch.setattr(app, "HeadPoseEstimator", FakeEstimator)
    monkeypatch.setattr(app, "Overlay", fake_overlay)
    monkeypatch.setattr(app, "build_outputs", lambda cfg: recorder["outputs"])
    monkeypatch.setattr(app, "Calibration", _StubCalibration)
    monkeypatch.setattr(app, "CalibrationWizard", _StubWizard)
    monkeypatch.setattr(sys, "argv", ["eyetrack"])
    FakeOverlay.instances.clear()
    recorder["cfg"] = Config()
    recorder["path"] = tmp_path / "eyetrack.json"
    return recorder


class _StubCalibration:
    """A calibration that never touches the user's real file."""

    instances: list["_StubCalibration"] = []

    def __init__(self):
        self.valid = True
        self.saved = 0
        _StubCalibration.instances.append(self)

    @classmethod
    def load(cls):
        return cls()

    def center_from(self, pose):
        self.valid = True

    def apply(self, pose):
        return {"yaw": 1.0, "pitch": 2.0, "roll": 3.0,
                "x": 4.0, "y": 5.0, "z": 6.0}

    def save(self, *_a):
        self.saved += 1


class _StubWizard:
    """A wizard that accepts a capture but never finishes."""

    def __init__(self, *_a, **_kw):
        self.index = 0
        self.captures = {}
        self.done = False
        self.cancelled = False

    @property
    def step(self):
        return None

    @property
    def prompt(self):
        return "stub"

    def submit(self, pose):
        self.index += 1

    def cancel(self):
        self.cancelled = True

    def result(self, base=None):
        return base


def _run(rig, **kw):
    return app.run(rig["cfg"], config_path=rig["path"], **kw)


def _run_with_keys(monkeypatch, keys, **kw):
    """Run the loop with a specific key script and hand back the overlay."""
    ov = FakeOverlay(keys=keys)
    monkeypatch.setattr(app, "open_camera", lambda cfg: FakeCapture())
    monkeypatch.setattr(app, "HeadPoseEstimator", FakeEstimator)
    monkeypatch.setattr(app, "Overlay", lambda *a, **kw_: ov)
    monkeypatch.setattr(app, "build_outputs", lambda cfg: [])
    monkeypatch.setattr(app, "Calibration", _StubCalibration)
    monkeypatch.setattr(app, "CalibrationWizard", _StubWizard)
    code = app.run(Config(), config_path="unused.json", **kw)
    return code, ov


# ------------------------------------------------------------------- keys
def test_the_loop_runs_and_exits_on_q(rig):
    assert _run(rig) == 0
    assert len(FakeOverlay.instances[0].frames) == 3, (
        "the loop did not keep drawing until Q")


def test_h_opens_the_help_panel(monkeypatch):
    _code, ov = _run_with_keys(monkeypatch, [ord("h"), ord("q")])
    assert ov.help_visible is True, "H did not open the help panel"


def test_h_closes_the_help_panel_again(monkeypatch):
    _code, ov = _run_with_keys(monkeypatch, [ord("h"), ord("h"), ord("q")])
    assert ov.help_visible is False, "H is not a toggle"


def test_m_toggles_the_face_mesh(monkeypatch):
    _code, ov = _run_with_keys(monkeypatch, [ord("m"), ord("q")])
    assert ov.show_mesh is False, "M did not toggle the mesh"


def test_f_switches_the_layout_and_remembers_it(monkeypatch, tmp_path):
    """The layout choice has to survive a restart, or F is just a toy."""
    ov = FakeOverlay(keys=[ord("f"), ord("q")])
    cfg = Config()
    path = tmp_path / "eyetrack.json"
    monkeypatch.setattr(app, "open_camera", lambda _cfg: FakeCapture())
    monkeypatch.setattr(app, "HeadPoseEstimator", FakeEstimator)
    monkeypatch.setattr(app, "Overlay", lambda *a, **kw: ov)
    monkeypatch.setattr(app, "build_outputs", lambda _c: [])
    monkeypatch.setattr(app, "Calibration", _StubCalibration)
    monkeypatch.setattr(app, "CalibrationWizard", _StubWizard)

    app.run(cfg, config_path=path)
    assert ov.compact is True, "F did not switch to the compact HUD"
    assert cfg.overlay.compact is True
    assert Config.load(path).overlay.compact is True, "the choice was not saved"


def test_escape_skips_setup_but_keeps_tracking_on_a_fresh_install(monkeypatch):
    """ESC on first run must not quit - the user has not calibrated yet."""
    code, ov = _run_with_keys(monkeypatch, [27, ord("q")], first_run=True)
    assert code == 0
    assert len(ov.frames) == 2, "the app quit instead of skipping into tracking"


def test_escape_still_quits_outside_the_first_run(monkeypatch):
    code, ov = _run_with_keys(monkeypatch, [27])
    assert code == 0
    assert len(ov.frames) == 1, "ESC should have quit immediately"


# ------------------------------------------------------------------ chips
def test_outputs_reach_the_overlay_as_chips(rig):
    _run(rig)
    chips = FakeOverlay.instances[0].chips
    assert [c[0] for c in chips] == ["TrackIR", "Minecraft"]
    assert all(on for _, on in chips), "a tracked frame must light the chips"


def test_chips_go_dark_when_the_face_is_lost(monkeypatch):
    ov = FakeOverlay(keys=[ord("q")])
    monkeypatch.setattr(app, "open_camera", lambda cfg: FakeCapture())
    monkeypatch.setattr(app, "HeadPoseEstimator",
                        lambda: FakeEstimator(detected=False))
    monkeypatch.setattr(app, "Overlay", lambda *a, **kw: ov)
    monkeypatch.setattr(app, "build_outputs",
                        lambda cfg: [FakeOutput("udp")])
    monkeypatch.setattr(app, "Calibration", _StubCalibration)
    monkeypatch.setattr(app, "CalibrationWizard", _StubWizard)

    app.run(Config(), config_path="unused.json")
    assert ov.chips == (("Minecraft", False),)


def test_the_mouse_sink_reports_its_toggle_state(monkeypatch):
    """F9 disarms the mouse; the chip has to say so rather than lie."""
    ov = FakeOverlay(keys=[ord("q")])
    monkeypatch.setattr(app, "open_camera", lambda cfg: FakeCapture())
    monkeypatch.setattr(app, "HeadPoseEstimator", FakeEstimator)
    monkeypatch.setattr(app, "Overlay", lambda *a, **kw: ov)
    monkeypatch.setattr(app, "build_outputs",
                        lambda cfg: [FakeOutput("mouse", active=False)])
    monkeypatch.setattr(app, "Calibration", _StubCalibration)
    monkeypatch.setattr(app, "CalibrationWizard", _StubWizard)

    app.run(Config(), config_path="unused.json")
    assert ov.chips == (("Mouse", False),)


# --------------------------------------------------------------- outputs
def test_every_enabled_output_receives_the_pose(rig):
    _run(rig)
    for out in rig["outputs"]:
        assert out.started, f"{out.name} was never started"
        assert out.sent, f"{out.name} never received a pose"
        assert out.sent[-1][1] is True, f"{out.name} sent a not-tracking frame"


def test_outputs_are_closed_on_the_way_out(rig):
    _run(rig)
    for out in rig["outputs"]:
        assert out.sent, "sanity"


def test_the_camera_is_released_even_if_drawing_raises(monkeypatch):
    """A crash in the renderer must not leave the webcam locked."""
    cap = FakeCapture()

    class Exploding(FakeOverlay):
        def draw(self, *_a, **_kw):
            raise RuntimeError("renderer blew up")

    monkeypatch.setattr(app, "open_camera", lambda cfg: cap)
    monkeypatch.setattr(app, "HeadPoseEstimator", FakeEstimator)
    monkeypatch.setattr(app, "Overlay", lambda *a, **kw: Exploding())
    monkeypatch.setattr(app, "build_outputs", lambda cfg: [])
    monkeypatch.setattr(app, "Calibration", _StubCalibration)
    monkeypatch.setattr(app, "CalibrationWizard", _StubWizard)

    with pytest.raises(RuntimeError):
        app.run(Config(), config_path="unused.json")
    assert cap.released, "the camera was left open"
    assert _StubCalibration.instances[-1].saved >= 0


def test_the_status_line_clears_itself_after_one_frame(rig):
    """The key hint must not sit on screen forever."""
    _run(rig)
    _first, status, _chips = FakeOverlay.instances[0].frames[0]
    assert status, "the first frame says nothing about what to press"
    assert FakeOverlay.instances[0].frames[1][1] == "", (
        "the status line was never cleared")