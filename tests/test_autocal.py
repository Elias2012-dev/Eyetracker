"""Automatic calibration: the wizard starts itself and captures itself.

Two bugs this covers, both found by running the app rather than reading it:

* ``Calibration.center_from`` marked the calibration ``valid``. ``step()``
  calls it on the first detected frame - before the wizard has measured
  anything - and saved that, so the very first run wrote a "calibrated"
  file and every later launch skipped the wizard entirely.
* the wizard needed five SPACE presses, so the automatic start went
  nowhere. Holding a pose still is just as unambiguous.
"""

from __future__ import annotations

import pytest

from eyetrack.calibrate import STEPS, Calibration, CalibrationWizard
from eyetrack.pose import HeadPose
from eyetrack.session import CALM_HOLD_S, CALM_MOVE_EPS, TrackingSession


def pose(yaw=0.0, pitch=0.0, detected=True) -> HeadPose:
    p = HeadPose()
    p.proxy_yaw = yaw
    p.proxy_pitch = pitch
    p.detected = detected
    return p


class BareSession(TrackingSession):
    """A session with no camera, estimator or outputs - only what the
    auto-calibration logic actually reads."""

    def __init__(self):                 # deliberately not calling super()
        from eyetrack.config import Config
        self.cfg = Config()
        self.wizard = None
        self.last_raw = None
        self.calib = Calibration()
        self._calib_ref = None
        self.submitted = []

    def submit_calibration(self) -> bool:
        if self.wizard is None or self.last_raw is None:
            return False
        self.wizard.submit(self.last_raw)
        self.submitted.append(self.wizard.index)
        return not self.wizard.done


# ------------------------------------------------------- the valid flag
def test_centering_alone_does_not_claim_calibration():
    """The bug: auto-centring is not calibration, and must not say it is."""
    cal = Calibration()
    cal.center_from(pose(yaw=0.3, pitch=0.2))
    assert cal.valid is False
    assert cal.proxy_yaw_center == pytest.approx(0.3)


def test_only_the_wizard_may_mark_a_calibration_valid():
    wizard = CalibrationWizard(40.0, 30.0, 60.0)
    for i, step in enumerate(STEPS):
        wizard.submit(pose(yaw=(i - 2) * 0.5, pitch=0.1))
    assert wizard.done, "five poses should finish the wizard"
    cal = wizard.result(Calibration())
    assert cal is not None
    assert cal.valid is True, "a finished wizard is the only thing that calibrates"


# --------------------------------------------------------- auto capture
def test_a_held_pose_captures_itself():
    s = BareSession()
    s.wizard = CalibrationWizard(40.0, 30.0, 60.0)
    s.last_raw = pose()
    t = 1000.0

    assert s._autostep_calibration(t) is False      # first sighting: start the clock
    assert s._autostep_calibration(t + CALM_HOLD_S / 2) is False   # not held long enough
    assert s._autostep_calibration(t + CALM_HOLD_S + 0.01) is True
    assert s.submitted, "holding still should have captured the step"


def test_movement_restarts_the_hold_timer():
    s = BareSession()
    s.wizard = CalibrationWizard(40.0, 30.0, 60.0)
    s.last_raw = pose()
    t = 1000.0

    s._autostep_calibration(t)
    s.last_raw = pose(yaw=CALM_MOVE_EPS * 5)        # head still moving
    assert s._autostep_calibration(t + 0.9) is False, "moving must not capture"
    s._autostep_calibration(t + 0.9)               # re-anchors on the new pose
    assert s._autostep_calibration(t + 0.9 + CALM_HOLD_S + 0.01) is True


def test_it_walks_the_whole_wizard_unattended():
    s = BareSession()
    s.wizard = CalibrationWizard(40.0, 30.0, 60.0)
    t = 1000.0
    for i in range(len(STEPS)):
        s.last_raw = pose(yaw=(i - 2) * 0.4, pitch=0.05)
        s._autostep_calibration(t)
        t += CALM_HOLD_S + 0.01
        s._autostep_calibration(t)                  # captures
        t += 0.1
    assert s.wizard.done, "holding each pose should finish all five steps"


def test_nothing_is_captured_without_a_wizard():
    s = BareSession()
    s.last_raw = pose()
    for i in range(10):
        assert s._autostep_calibration(1000.0 + i * CALM_HOLD_S) is False


def test_a_cancelled_wizard_is_left_alone():
    s = BareSession()
    s.wizard = CalibrationWizard(40.0, 30.0, 60.0)
    s.wizard.cancel()
    s.last_raw = pose()
    for i in range(10):
        assert s._autostep_calibration(1000.0 + i * CALM_HOLD_S) is False


def test_no_face_means_no_capture():
    s = BareSession()
    s.wizard = CalibrationWizard(40.0, 30.0, 60.0)
    s.last_raw = None
    for i in range(10):
        assert s._autostep_calibration(1000.0 + i * CALM_HOLD_S) is False


def test_beginning_calibration_clears_any_pending_hold():
    s = BareSession()
    s.wizard = CalibrationWizard(40.0, 30.0, 60.0)
    s.last_raw = pose()
    s._autostep_calibration(1000.0)                # a hold in progress
    s.wizard = None
    from eyetrack.config import Config
    fresh = TrackingSession.__new__(TrackingSession)
    fresh.cfg = Config()
    fresh.wizard = fresh.begin_calibration()
    assert fresh._calib_ref is None, "a new wizard must not inherit an old hold"


# ------------------------------------------------- the per-frame write
class _StubCap:
    """A camera that always delivers the same frame."""

    def read(self):
        import numpy as np
        return True, np.zeros((8, 8, 3), np.uint8)


class _StubEstimator:
    def __init__(self):
        self.n = 0

    def process(self, frame, ms):
        self.n += 1
        # A face that jitters slightly, as any real one does.
        return pose(yaw=0.001 * self.n, pitch=0.001 * self.n)


def _runnable_session():
    """A session with everything step() touches, and no real hardware."""
    from eyetrack.config import Config
    from eyetrack.filters import PoseFilter

    s = TrackingSession.__new__(TrackingSession)
    cfg = Config()
    s.cfg = cfg
    s.calib = Calibration()
    s.filt = PoseFilter(cfg.filter)
    s.cap = _StubCap()
    s.estimator = _StubEstimator()
    s.outputs = []
    s.wizard = None
    s.last_raw = None
    s._calib_ref = None
    s.running = True
    s.recenter_on_start = False
    s._auto_centred = False
    s._auto_recentered = True      # isolate the auto-centre branch
    s.values = {}
    s.fps_ema = 0.0
    s._t_last = 0.0
    return s


def test_autocentring_writes_once_not_every_frame():
    """Regression: centring used to be keyed on `calib.valid`.

    Since only the wizard may set that, "not valid" stays true for the
    whole session, so keying on it rewrote calibration.json once per frame
    and dragged the neutral pose along with every camera tremor.

    This drives the real ``step()``; re-implementing the branch here would
    pass even with the bug in place.
    """
    s = _runnable_session()
    saves = []
    s.calib.save = lambda *a, **k: saves.append(1)

    for _ in range(30):
        s.step()

    assert len(saves) == 1, f"calibration.json written {len(saves)} times in 30 frames"
    assert s.calib.valid is False, "auto-centring still must not claim calibration"


def test_autocentring_uses_the_first_pose_not_the_latest():
    s = _runnable_session()
    s.calib.save = lambda *a, **k: None
    for _ in range(10):
        s.step()
    # The neutral pose must not chase the camera's jitter.
    assert s.calib.proxy_yaw_center == pytest.approx(0.001, abs=1e-6)


def test_a_restarted_session_autocentres_again():
    """start() resets the flag, or Stop then Start skips the auto-centre."""

    s = _runnable_session()
    s.calib.save = lambda *a, **k: None
    for _ in range(3):
        s.step()
    assert s._auto_centred is True

    s.running = False
    s.cap = _StubCap()
    s.estimator = _StubEstimator()
    # Emulate what start() does, without opening a real camera.
    s._auto_recentered = False
    s._auto_centred = False
    s._t_last = 0.0
    s.running = True
    s.step()
    assert s._auto_centred is True, "a new session must auto-centre again"