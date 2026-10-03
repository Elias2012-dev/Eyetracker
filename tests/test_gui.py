"""Tests for the settings window.

A Tk window cannot be asserted on in CI, so the logic worth testing is
split out into :class:`eyetrack.gui.TrackerController` and the argument
routing that decides between the window and the console tracker. Those two
are what this file drives; the widgets themselves are covered by opening
the real window on a machine that has a display (``test_gui_smoke``).

The routing matters most: if a bare ``Eyetracker.exe`` stopped opening the
settings window, or if ``--paths`` ever fell through into it, the packaged
app would either regress for users or hang the release smoke test.
"""

from __future__ import annotations

import os

import pytest

from eyetrack.config import Config


# --------------------------------------------------------------- routing
@pytest.mark.parametrize("argv,expect_gui", [
    ([], True),
    (["--gui"], True),
    (["--no-gui"], False),
    (["--compact"], True),
    (["--mouse"], True),
    (["--game", "ets2"], True),
    (["--first-run"], False),
    (["--calibrate"], False),
    (["--pick-camera"], False),
])
def test_a_bare_launch_opens_the_settings_window(argv, expect_gui, monkeypatch,
                                                 tmp_path):
    import eyetrack.__main__ as cli

    seen = {}

    def fake_gui(cfg, path):
        seen["gui"] = True
        return 0

    def fake_run(cfg, *, config_path, **kw):
        seen["gui"] = False
        return 0

    import eyetrack.gui as gui_mod
    import eyetrack.app as app_mod
    monkeypatch.setattr(gui_mod, "main", fake_gui)
    monkeypatch.setattr(app_mod, "run", fake_run)

    code = cli.main(argv + ["--config", str(tmp_path / "eyetrack.json")])
    assert code == 0
    assert seen.get("gui") is expect_gui, (
        f"{argv} should {'open the settings window' if expect_gui else 'track directly'}")


@pytest.mark.parametrize("flag", ["--paths", "--version", "--list-games"])
def test_a_command_that_exits_never_opens_the_window(flag, monkeypatch, tmp_path):
    """--paths and friends must return before the GUI branch is reached.

    The release smoke test runs the packaged exe with --paths; if that ever
    fell through into the settings window it would hang the build instead
    of failing.
    """
    import eyetrack.__main__ as cli
    import eyetrack.gui as gui_mod

    def explode(*_a, **_k):  # pragma: no cover - must never run
        raise AssertionError(f"the GUI opened for {flag}")

    monkeypatch.setattr(gui_mod, "main", explode)
    monkeypatch.setattr("eyetrack.console.show_message", lambda *a, **k: None)
    argv = [flag, "--config", str(tmp_path / "eyetrack.json")]
    if flag == "--version":
        # argparse's version action exits; it must still exit, not open a
        # window on the way out.
        with pytest.raises(SystemExit) as exc:
            cli.main(argv)
        assert exc.value.code == 0
        return
    cli.main(argv)


# ------------------------------------------------------------ controller
class _FakeSession:
    instances: list["_FakeSession"] = []

    def __init__(self, cfg, config_path, *, start_wizard=False,
                 recenter_on_start=False):
        self.cfg = cfg
        self.config_path = config_path
        self.start_wizard = start_wizard
        self.recenter_on_start = recenter_on_start
        self.calib = type("C", (), {"valid": True})()
        self.wizard = None
        self.running = False
        self.stopped = 0
        self.saved = 0
        _FakeSession.instances.append(self)

    def start(self):
        self.running = True

    def stop(self):
        self.running = False
        self.stopped += 1

    def save_config(self):
        self.saved += 1

    def recentre(self):
        return True

    def begin_calibration(self):
        self.wizard = object()
        return self.wizard

    def submit_calibration(self):
        return False

    def describe_outputs(self):
        return "udp"


@pytest.fixture
def ctrl(monkeypatch, tmp_path):
    import eyetrack.gui as gui_mod

    _FakeSession.instances.clear()
    monkeypatch.setattr(gui_mod, "TrackingSession", _FakeSession)
    cfg = Config()
    c = gui_mod.TrackerController(cfg, tmp_path / "eyetrack.json")
    c.config_path = tmp_path / "eyetrack.json"
    return c


def test_start_then_stop(ctrl):
    assert ctrl.start() is True
    assert ctrl.running is True
    assert ctrl.status == "Tracking"
    ctrl.stop()
    assert ctrl.running is False
    assert ctrl.status == "Stopped"


def test_toggle_flips_the_state(ctrl):
    assert ctrl.toggle() is True
    assert ctrl.toggle() is False


def test_starting_twice_creates_only_one_session(ctrl):
    ctrl.start()
    ctrl.start()
    assert len(_FakeSession.instances) == 1, "the second Start leaked a session"


def test_a_failed_start_reports_instead_of_crashing(ctrl, monkeypatch):
    """A missing webcam must not take the window down with it."""

    class Refuses:
        def __init__(self, *a, **k):
            pass

        def start(self):
            raise SystemExit("Could not open camera index 3")

    monkeypatch.setattr("eyetrack.gui.TrackingSession", Refuses)
    assert ctrl.start() is False
    assert ctrl.running is False
    assert ctrl.status == "Could not start"
    assert "camera index 3" in ctrl.detail


def test_a_failed_start_reports_an_unexpected_error_too(ctrl, monkeypatch):
    class Explodes:
        def __init__(self, *a, **k):
            pass

        def start(self):
            raise RuntimeError("driver exploded")

    monkeypatch.setattr("eyetrack.gui.TrackingSession", Explodes)
    assert ctrl.start() is False
    assert "driver exploded" in ctrl.detail


def test_settings_are_persisted_to_disk(ctrl, tmp_path):
    ctrl.apply(**{"camera.device_name": "Camo", "camera.index": 2})
    reloaded = Config.load(ctrl.config_path)
    assert reloaded.camera.device_name == "Camo"
    assert reloaded.camera.index == 2


def test_settings_changed_while_tracking_are_saved_through_the_session(ctrl):
    ctrl.start()
    ctrl.apply(**{"overlay.compact": True, "overlay.show_mesh": False})
    assert ctrl.session.saved >= 1
    assert ctrl.cfg.overlay.compact is True
    assert ctrl.cfg.overlay.show_mesh is False


@pytest.mark.parametrize("key", ["camera.nonexistent", "nosuchsection.compact",
                                 "compact"])
def test_an_unknown_setting_is_rejected_not_silently_ignored(ctrl, key):
    """Config is a plain dataclass, so setattr would invent a field.

    A typo'd key that "works" and then reverts on reload is worse than a
    loud failure, so apply() checks the field names first.
    """
    with pytest.raises(KeyError):
        ctrl.apply(**{key: 1})


def test_recentre_before_starting_is_harmless(ctrl):
    assert ctrl.recentre() is False


def test_commands_before_starting_are_harmless(ctrl):
    assert ctrl.recentre() is False
    assert ctrl.submit_calibration() is False
    ctrl.begin_calibration()
    assert ctrl.wizard is None


def test_wizard_is_exposed_only_while_running(ctrl):
    assert ctrl.wizard is None
    ctrl.start()
    ctrl.begin_calibration()
    assert ctrl.wizard is not None
    ctrl.stop()
    assert ctrl.wizard is None


# ------------------------------------------------------------------- smoke
@pytest.mark.skipif(os.environ.get("EYETRACK_HEADLESS") == "1",
                    reason="headless run")
def test_gui_smoke_the_real_window_opens_and_closes(tmp_path):
    """Open the actual Tk window once, to prove the widget tree builds.

    A typo in a widget option only shows up when the window is really
    constructed, and nothing else in the suite touches Tk.
    """
    import tkinter as tk

    try:
        probe = tk.Tk()
    except Exception as exc:  # pragma: no cover - depends on the machine
        pytest.skip(f"no display available: {exc}")
    probe.destroy()

    from eyetrack.gui import TrackerWindow

    cfg = Config()
    win = TrackerWindow(cfg, tmp_path / "eyetrack.json")
    try:
        assert win.start_button.cget("text") == "Start tracking"
        assert win.camera_list.size() >= 1, "the camera list is empty"
        assert win.controller.running is False
        win.on_mirror()
        assert win.cfg.camera.mirror == win.mirror_var.get()
        win.on_ranges()
        assert win.cfg.pose.yaw_range == round(win.yaw_var.get())
        win._refresh_status()
    finally:
        win.on_close()