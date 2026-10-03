"""Tests for the tracking session - the machine both front ends drive.

Start/stop has to be repeatable (the GUI's button calls it on every press),
a stalled camera must not wedge the loop, and the calibration lifecycle has
to go through the real wizard rather than around it.
"""

from __future__ import annotations

import pytest

from eyetrack.config import Config
from eyetrack.session import (
    TrackingSession,
    apply_gains,
    output_chips,
    open_camera,
)


class FakeCapture:
    def __init__(self, frames=50):
        self.frames = frames
        self.released = False
        self.closed = False

    def isOpened(self):
        return True

    def read(self):
        if self.frames <= 0:
            return False, None
        self.frames -= 1
        return True, [[0]]

    def set(self, *_a):
        pass

    def release(self):
        self.released = True


class FakeEstimator:
    def __init__(self, detected=True, **pose_kwargs):
        self.detected = detected
        self.closed = False
        self.calls = 0
        self.pose_kwargs = pose_kwargs

    def process(self, frame, t_ms=0):
        from eyetrack.pose import HeadPose

        self.calls += 1
        p = HeadPose()
        p.detected = self.detected
        p.face_px = 100.0
        p.proxy_yaw, p.proxy_pitch = 0.2, -0.1
        for k, v in self.pose_kwargs.items():
            setattr(p, k, v)
        return p

    def close(self):
        self.closed = True


class FakeOutput:
    def __init__(self, name, **attrs):
        self.name = name
        self.sent = []
        self.started = 0
        self.closed = 0
        for k, v in attrs.items():
            setattr(self, k, v)

    def start(self):
        self.started += 1

    def send(self, pose, tracking, t):
        self.sent.append((dict(pose), tracking))

    def close(self):
        self.closed += 1


@pytest.fixture
def rig(monkeypatch, tmp_path):
    import eyetrack.session as s

    rec = {"capture": FakeCapture(), "estimator": FakeEstimator(),
           "outputs": [FakeOutput("udp"), FakeOutput("game-link")]}

    monkeypatch.setattr(s, "open_camera", lambda cfg: rec["capture"])
    monkeypatch.setattr(s, "HeadPoseEstimator", lambda: rec["estimator"])
    monkeypatch.setattr(s, "build_outputs", lambda cfg: rec["outputs"])
    monkeypatch.setattr(s.Calibration, "load", classmethod(lambda cls: s.Calibration()))
    monkeypatch.setattr(s.Calibration, "save", lambda self, *a: None)
    rec["cfg"] = Config()
    rec["path"] = tmp_path / "eyetrack.json"
    return rec


def _session(rig, **kw):
    return TrackingSession(rig["cfg"], rig["path"], **kw)


# ------------------------------------------------------------- lifecycle
def test_start_opens_the_camera_and_the_outputs(rig):
    ses = _session(rig)
    ses.start()
    assert ses.running is True
    assert rig["capture"].isOpened()
    for out in rig["outputs"]:
        assert out.started == 1


def test_starting_twice_does_not_open_a_second_camera(rig):
    """The GUI's Start button can be pressed twice; the second must be a no-op."""
    ses = _session(rig)
    ses.start()
    ses.start()
    assert rig["outputs"][0].started == 1


def test_stop_releases_everything(rig):
    ses = _session(rig)
    ses.start()
    ses.stop()
    assert ses.running is False
    assert rig["capture"].released is True
    assert rig["estimator"].closed is True
    for out in rig["outputs"]:
        assert out.closed == 1


def test_stop_is_safe_before_start_and_twice_after(rig):
    ses = _session(rig)
    ses.stop()
    ses.start()
    ses.stop()
    ses.stop()
    assert ses.running is False


def test_stop_and_start_again_works(rig):
    """Stop then Start is the normal way to switch cameras in the GUI."""
    ses = _session(rig)
    ses.start()
    ses.stop()
    rig["capture"] = FakeCapture()
    import eyetrack.session as s
    s.open_camera = lambda cfg: rig["capture"]
    ses.start()
    assert ses.running is True
    ses.stop()
    assert rig["capture"].released is True


# ----------------------------------------------------------------- frames
def test_a_tracked_frame_produces_values_and_reaches_every_output(rig):
    ses = _session(rig)
    ses.start()
    res = ses.step()
    assert res.ok is True
    assert res.tracking is True
    assert set(res.values) == {"yaw", "pitch", "roll", "x", "y", "z"}
    for out in rig["outputs"]:
        assert out.sent, f"{out.name} received nothing"
        assert out.sent[-1][1] is True


def test_a_lost_face_still_sends_a_not_tracking_flag(rig):
    """The game needs to know the face went away, not keep the last pose."""
    ses = _session(rig)
    ses.start()
    ses.step()
    rig["estimator"].detected = False
    res = ses.step()
    assert res.tracking is False
    assert rig["outputs"][0].sent[-1][1] is False


def test_a_stalled_camera_reports_failure_without_raising(rig):
    ses = _session(rig)
    ses.start()
    rig["capture"].frames = 0
    res = ses.step()
    assert res.ok is False
    assert res.frame is None


def test_stepping_a_stopped_session_is_a_no_op(rig):
    ses = _session(rig)
    res = ses.step()
    assert res.ok is False
    assert rig["estimator"].calls == 0, "a stopped session must not touch the camera"


# ----------------------------------------------------------------- gains
def test_gains_and_inverts_are_applied():
    cfg = Config()
    cfg.pose.yaw_gain = 2.0
    cfg.pose.invert_pitch = True
    out = apply_gains(cfg, {"yaw": 3.0, "pitch": 4.0, "roll": 5.0,
                            "x": 6.0, "y": 7.0, "z": 8.0})
    assert out["yaw"] == pytest.approx(6.0)
    assert out["pitch"] == pytest.approx(-4.0)
    assert out["roll"] == pytest.approx(5.0)


# -------------------------------------------------------------- commands
def test_recentre_needs_a_face_first(rig):
    ses = _session(rig)
    ses.start()
    assert ses.recentre() is False, "recentre claimed to work with no face"
    ses.step()
    assert ses.recentre() is True


def test_calibration_walks_all_five_steps_then_saves(rig):
    ses = _session(rig)
    ses.start()
    ses.step()
    wizard = ses.begin_calibration()
    assert wizard is ses.wizard
    for _ in range(5):
        ses.step()
        ses.submit_calibration()
    assert wizard.done is True
    assert ses.calib.valid is True


def test_cancelling_the_wizard_closes_it(rig):
    ses = _session(rig)
    ses.start()
    ses.begin_calibration()
    ses.cancel_calibration()
    assert ses.wizard.cancelled is True
    assert ses.submit_calibration() is False, "a cancelled wizard must not save"


def test_submitting_without_a_wizard_does_nothing(rig):
    ses = _session(rig)
    ses.start()
    assert ses.submit_calibration() is False


# ----------------------------------------------------------------- chips
def test_chips_light_only_while_tracking(rig):
    ses = _session(rig)
    ses.start()
    assert all(on for _, on in ses.chips(True))
    assert not any(on for _, on in ses.chips(False))


def test_chips_are_empty_before_start(rig):
    assert _session(rig).chips(True) == ()


def test_describe_outputs_is_readable(rig):
    ses = _session(rig)
    assert ses.describe_outputs() == "none"
    ses.start()
    assert "udp" in ses.describe_outputs()


# --------------------------------------------------------------- camera
def test_open_camera_raises_a_readable_error_for_a_bad_device(monkeypatch):
    """The GUI catches this and shows it; the message has to say what to do."""
    import cv2

    class Closed:
        def isOpened(self):
            return False

    monkeypatch.setattr(cv2, "VideoCapture", lambda *a: Closed())
    with pytest.raises(SystemExit) as exc:
        open_camera(Config())
    text = str(exc.value)
    assert "camera" in text.lower()
    assert "settings" in text.lower(), "it should point at the settings window"


def test_output_chips_label_every_sink():
    chips = output_chips([FakeOutput("game-link"), FakeOutput("udp"),
                          FakeOutput("mouse", active=True)], True)
    assert [c[0] for c in chips] == ["TrackIR", "Minecraft", "Mouse"]