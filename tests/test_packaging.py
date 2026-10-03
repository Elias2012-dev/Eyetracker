"""Packaging: what the frozen build must do, and two bugs it already caused.

Both regressions below were found by running the built exe, not by reading
the code - which is exactly why they get a test each.
"""

import sys
from pathlib import Path

import pytest

from eyetrack import bridge, console

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC = REPO_ROOT / "packaging" / "eyetracker.spec"


@pytest.fixture
def restore_stdio():
    """attach_log() replaces sys.stdout/err - put them back afterwards."""
    out, err = sys.stdout, sys.stderr
    yield
    sys.stdout, sys.stderr = out, err


def test_attach_log_mirrors_into_the_file_and_the_console(tmp_path, monkeypatch,
                                                          capsys, restore_stdio):
    log = tmp_path / "Eyetracker.log"
    monkeypatch.setattr(console, "log_path", lambda: log)

    assert console.attach_log() == log
    print("hello from the tracker")
    sys.stdout.flush()

    assert "hello from the tracker" in log.read_text(encoding="utf-8")
    assert "hello from the tracker" in capsys.readouterr().out


def test_attach_log_keeps_output_when_there_is_no_console(tmp_path, monkeypatch,
                                                          restore_stdio):
    log = tmp_path / "Eyetracker.log"
    monkeypatch.setattr(console, "log_path", lambda: log)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    console.attach_log()
    print("no console here")
    sys.stdout.flush()

    assert "no console here" in log.read_text(encoding="utf-8")
    assert log.read_text(encoding="utf-8").count("=== Eyetracker started") == 1


def test_game_dir_is_the_bundle_when_running_from_source(monkeypatch):
    monkeypatch.setattr(bridge, "FROZEN", False)
    assert bridge.game_dir() == bridge.BRIDGE_DIR


def test_frozen_build_stages_the_dlls_outside_the_temp_folder(monkeypatch, tmp_path):
    """A one-file build unpacks into %TEMP% and is deleted on exit.

    The games must never be pointed at that directory, so the DLLs are
    copied next to the config first.
    """
    unpacked = tmp_path / "_MEI123"
    (unpacked / "bridge").mkdir(parents=True)
    for name in bridge.REQUIRED_DLLS:
        (unpacked / "bridge" / name).write_bytes(b"MZ" + name.encode())
    user_data = tmp_path / "AppData" / "Eyetracker"

    monkeypatch.setattr(bridge, "FROZEN", True)
    monkeypatch.setattr(bridge, "BRIDGE_DIR", unpacked / "bridge")
    monkeypatch.setattr(bridge, "data_dir", lambda: user_data)

    staged = bridge.game_dir()
    assert staged == user_data / "bridge"
    # The invariant is "not the unpacked folder", not "no TEMP anywhere":
    # pytest's own tmp_path lives under %TEMP%.
    assert staged != unpacked / "bridge"
    assert not str(staged).startswith(str(unpacked))
    assert "_MEI" not in str(staged)
    for name in bridge.REQUIRED_DLLS:
        assert (staged / name).exists(), f"{name} was not staged"


def test_staging_refreshes_a_truncated_dll(monkeypatch, tmp_path):
    unpacked = tmp_path / "_MEI"
    (unpacked / "bridge").mkdir(parents=True)
    for name in bridge.REQUIRED_DLLS:
        (unpacked / "bridge" / name).write_bytes(b"x" * 64)
    user_data = tmp_path / "AppData"
    staged = user_data / "bridge"
    staged.mkdir(parents=True)
    (staged / "NPClient64.dll").write_bytes(b"truncated")  # older/partial copy

    monkeypatch.setattr(bridge, "FROZEN", True)
    monkeypatch.setattr(bridge, "BRIDGE_DIR", unpacked / "bridge")
    monkeypatch.setattr(bridge, "data_dir", lambda: user_data)

    assert (bridge.game_dir() / "NPClient64.dll").stat().st_size == 64


# --- the spec itself: two mistakes it has already made -----------------
def test_spec_ships_the_dlls_and_the_model():
    spec = SPEC.read_text(encoding="utf-8")
    for needed in ("NPClient.dll", "NPClient64.dll", "face_landmarker.task",
                   "launcher.py"):
        assert needed in spec, f"the bundle must include {needed}"


def test_spec_does_not_exclude_matplotlib():
    """mediapipe.tasks.python.vision.drawing_utils imports it at load time."""
    spec = SPEC.read_text(encoding="utf-8")
    excludes_line = next(line for line in spec.splitlines()
                         if line.startswith("excludes ="))
    assert "matplotlib" not in excludes_line, (
        "excluding matplotlib breaks HeadPoseEstimator() inside the exe")
    assert '"matplotlib"' in spec, "matplotlib should be an explicit hidden import"


def test_spec_refuses_to_build_without_the_model():
    """A missing model must fail the build, not ship a half-working exe.

    models/*.task is gitignored, so a clean checkout (every CI runner, and
    anyone cloning fresh) has no model. The spec used to skip it silently,
    which produced an exe that built fine and then failed on first launch -
    far worse than a red build.
    """
    spec = SPEC.read_text(encoding="utf-8")
    assert "raise SystemExit" in spec, (
        "the spec must abort when the face model is missing")
    # The guard must be an unconditional append after the check, not another
    # "if it happens to be there".
    assert 'datas.append((str(model), "models"))' in spec


def test_spec_builds_a_windowed_exe():
    spec = SPEC.read_text(encoding="utf-8")
    assert 'name="Eyetracker"' in spec
    assert "console=False" in spec, "the exe must not open a terminal window"


def test_spec_bundles_tkinter():
    """The settings window is Tk, so tkinter has to be in the bundle.

    It used to be in `excludes`, left over from when the overlay was the
    whole UI. The result was an exe that built, passed the --paths smoke
    test, and then died with ModuleNotFoundError on the first double-click -
    the tests all passed and the program was unusable.
    """
    spec = SPEC.read_text(encoding="utf-8")
    excludes_line = next(line for line in spec.splitlines()
                         if line.startswith("excludes ="))
    assert "tkinter" not in excludes_line, (
        "excluding tkinter means the packaged exe cannot open its window")
    assert '"tkinter"' in spec, "tkinter should be an explicit hidden import"
    assert '"tkinter.ttk"' in spec, "the settings window uses ttk widgets"