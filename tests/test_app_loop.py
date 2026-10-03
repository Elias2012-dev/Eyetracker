"""Integration tests for the main loop.

The HUD renders fine in isolation; what is easy to get wrong is the wiring
around it - whether ``H`` actually reaches the help panel, whether ``F``
persists the layout choice, whether an output sink shows up as a chip. So
these drive :func:`eyetrack.app.run` with a scripted keystroke queue and a
fake session and check what came out the other end.

The session itself is covered separately in ``test_session.py``; here it is
the seam, not the subject.
"""

from __future__ import annotations

import pytest

from eyetrack import app
from eyetrack.config import Config


class FakeFrame:
    def __init__(self, ok=True):
        self.ok = ok


class FakeResult:
    """Stands in for one TrackingSession.step() result."""

    def __init__(self, ok=True, message="", tracking=True):
        self.ok = ok
        self.message = message
        self.tracking = tracking
        self.frame = FakeFrame(ok)
        self.pose = object()
        self.values = {"yaw": 1.0, "pitch": 2.0, "roll": 3.0,
                       "x": 4.0, "y": 5.0, "z": 6.0}
        self.fps = 58.4


class FakeWizard:
    def __init__(self):
        self.done = False
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class FakeSession:
    """A tracking session that never touches a camera."""

    def __init__(self, cfg, config_path, *, start_wizard=False,
                 recenter_on_start=False, ok=True):
        self.cfg = cfg
        self.config_path = config_path
        self.start_wizard = start_wizard
        self.recenter_on_start = recenter_on_start
        self.calib = type("C", (), {"valid": True})()
        self.wizard = FakeWizard() if start_wizard else None
        self.ok = ok
        self.running = False
        self.stopped = False
        self.recentred = 0
        self.calibrated = 0
        self.cancelled = 0
        self.drawn = []
        self.saved = 0

    # session API
    def start(self):
        self.running = True

    def stop(self):
        self.running = False
        self.stopped = True

    def step(self):
        return FakeResult(ok=self.ok)

    def chips(self, tracking):
        return (("TrackIR", tracking),)

    def recentre(self):
        self.recentred += 1
        return True

    def begin_calibration(self):
        self.wizard = FakeWizard()
        return self.wizard

    def cancel_calibration(self):
        self.cancelled += 1
        if self.wizard is not None:
            self.wizard.cancelled = True

    def submit_calibration(self):
        self.calibrated += 1
        return False

    def save_config(self):
        self.saved += 1

    def describe_outputs(self):
        return "udp"


class FakeOverlay:
    def __init__(self, *a, keys=None, **kw):
        self.keys = list(keys) if keys else []
        self.help_visible = False
        self.compact = bool(kw.get("compact", False))
        self.show_mesh = bool(kw.get("show_mesh", True))
        self.top_most = bool(kw.get("top_most", False))
        self._topmost_applied = False
        self.calls = 0

    def draw(self, *_a, **_kw):
        self.calls += 1
        return self.keys.pop(0) if self.keys else 27

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


@pytest.fixture
def rig(monkeypatch, tmp_path):
    """Run app.run against a fake session; returns a recorder."""
    rec = {"sessions": [], "overlays": [], "keys": [-1, -1, ord("q")]}

    def fake_session(cfg, config_path, *, start_wizard=False,
                     recenter_on_start=False):
        s = FakeSession(cfg, config_path, start_wizard=start_wizard,
                        recenter_on_start=recenter_on_start)
        rec["sessions"].append(s)
        return s

    def fake_overlay(*a, **kw):
        # The window is what hands keys back, so that is where the scripted
        # keys live - one list, shared, exactly like the real thing.
        ov = FakeOverlay(*a, keys=rec["keys"], **kw)
        rec["overlays"].append(ov)
        return ov

    monkeypatch.setattr(app, "TrackingSession", fake_session)
    monkeypatch.setattr(app, "Overlay", fake_overlay)
    rec["monkeypatch"] = monkeypatch
    rec["cfg"] = Config()
    rec["path"] = tmp_path / "eyetrack.json"
    return rec


def _run(rig, **kw):
    return app.run(rig["cfg"], config_path=rig["path"], **kw)


def _session(rig):
    return rig["sessions"][0]


# ------------------------------------------------------------------- keys
def test_the_loop_runs_and_exits_on_q(rig):
    assert _run(rig) == 0
    # q on the third frame ends it; the frame after it is never reached.
    assert rig["overlays"][0].calls == 3, "the loop ran past the q"
    assert _session(rig).stopped, "the session was not stopped on the way out"


def test_h_opens_the_help_panel(rig):
    rig["keys"] = [ord("h"), ord("q")]
    _run(rig)
    assert rig["overlays"][0].help_visible is True, "H did not open the help panel"


def test_h_closes_the_help_panel_again(rig):
    rig["keys"] = [ord("h"), ord("h"), ord("q")]
    _run(rig)
    assert rig["overlays"][0].help_visible is False, "H is not a toggle"


def test_m_toggles_the_face_mesh(rig):
    rig["keys"] = [ord("m"), ord("q")]
    _run(rig)
    assert rig["overlays"][0].show_mesh is False, "M did not toggle the mesh"


def test_f_switches_the_layout_and_remembers_it(rig):
    rig["keys"] = [ord("f"), ord("q")]
    _run(rig)
    assert rig["overlays"][0].compact is True, "F did not switch layout"
    assert rig["cfg"].overlay.compact is True
    assert _session(rig).saved >= 1, "the layout choice was not persisted"


def test_r_recentres(rig):
    rig["keys"] = [ord("r"), ord("q")]
    _run(rig)
    assert _session(rig).recentred == 1


def test_c_starts_the_wizard(rig):
    rig["keys"] = [ord("c"), ord("q")]
    _run(rig)
    assert _session(rig).wizard is not None, "C did not open the wizard"


def test_space_captures_only_while_a_wizard_is_open(rig):
    rig["keys"] = [ord(" "), ord("q")]
    _run(rig)
    assert _session(rig).calibrated == 0, "SPACE must not do anything idle"

    rig2 = rig
    rig2["keys"] = [ord("c"), ord(" "), ord("q")]
    rig2["sessions"].clear()
    rig2["overlays"].clear()
    _run(rig2)
    assert _session(rig2).calibrated == 1


def test_escape_skips_setup_but_keeps_tracking_on_a_fresh_install(rig):
    """ESC on first run must not quit - the user has not calibrated yet."""
    rig["keys"] = [27, ord("q")]
    assert _run(rig, first_run=True) == 0
    assert _session(rig).cancelled == 1
    assert rig["overlays"][0].calls == 2, (
        "the app quit instead of skipping into tracking")
    assert rig["cfg"] is not None


def test_escape_still_quits_outside_the_first_run(rig):
    rig["keys"] = [27]
    assert _run(rig) == 0
    assert rig["overlays"][0].calls == 1
    assert _session(rig).cancelled == 0, "ESC must not cancel a wizard that is not there"


# ------------------------------------------------------------------ chips
def test_outputs_reach_the_overlay_as_chips(rig):
    _run(rig)
    overlay = rig["overlays"][0]
    assert overlay.calls > 0
    assert _session(rig).chips(True) == (("TrackIR", True),)


def test_with_no_window_a_stalled_camera_keeps_the_loop_alive(rig):
    """With no overlay there is no keyboard, so the only exit is an interrupt.

    The loop must survive a stalled camera on its own rather than needing a
    key to make progress - and must still shut the session down on the way
    out, or the camera stays locked for the next run.
    """
    rig["cfg"].overlay.enabled = False
    session = FakeSession(rig["cfg"], rig["path"])
    rig["monkeypatch"].setattr(app, "TrackingSession",
                               lambda *a, **k: session)
    calls = {"n": 0}

    def stalling_step():
        calls["n"] += 1
        if calls["n"] > 3:
            raise KeyboardInterrupt          # the only exit a headless run has
        return FakeResult(ok=False)

    session.step = stalling_step

    assert _run(rig) == 0
    assert calls["n"] == 4, "the loop stopped retrying the camera"
    assert session.stopped, "the session was left holding the camera"


# --------------------------------------------------------------- teardown
def test_the_session_is_closed_even_if_drawing_raises(monkeypatch, tmp_path):
    """A crash in the renderer must not leave the camera locked."""
    session = FakeSession(Config(), tmp_path)

    class Exploding(FakeOverlay):
        def draw(self, *_a, **_kw):
            raise RuntimeError("renderer blew up")

    monkeypatch.setattr(app, "TrackingSession", lambda *a, **k: session)
    monkeypatch.setattr(app, "Overlay", lambda *a, **k: Exploding())

    with pytest.raises(RuntimeError):
        app.run(Config(), config_path=tmp_path / "eyetrack.json")
    assert session.stopped, "the session was left running"


def test_the_test_suite_does_not_write_into_the_repository():
    """A test that litters the checkout breaks the release preflight.

    app.run() persists the config it is handed, so a relative config_path
    drops a file in the working tree - which then makes ``release.py``
    refuse to tag anything. Every call has to point somewhere temporary.
    """
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "run"):
            continue
        for kw in node.keywords:
            if kw.arg != "config_path":
                continue
            if isinstance(kw.value, ast.Constant) and isinstance(
                    kw.value.value, str):
                if "/" not in kw.value.value and "\\" not in kw.value.value:
                    offenders.append(kw.value.value)
    assert not offenders, (
        f"app.run(config_path={offenders!r}) is relative, so these tests "
        "write into the repository instead of a tmp_path")