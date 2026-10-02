"""Independence: no external tracker is involved, anywhere.

This project used to ship an output that fed another tracking program on
the user's machine. That path is gone - there is no code, no config
section, no command-line flag, no documentation and no dependency on any
external tracker, because the whole design is that a webcam plus this
repository is the entire installation.

These tests keep it that way. The name is assembled at runtime so this
file does not itself contain the string it forbids, and the scan covers
every text file in the tree, so a stray mention in a comment or a README
line fails the build as loudly as a resurrected dependency would.
"""

import dataclasses
from pathlib import Path

from eyetrack.__main__ import _parse_args
from eyetrack.config import Config
from eyetrack.outputs import build_outputs

REPO_ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN = ("open" + "track").casefold()
OLD_PORT = "42" + "42"          # the removed sink's default port

SCAN_SUFFIXES = {".py", ".md", ".txt", ".c", ".h", ".def", ".bat", ".gradle",
                 ".properties", ".json", ".cfg", ".toml"}
# Generated, vendored or third-party trees that are not part of the project.
SKIP_DIRS = {"__pycache__", ".venv", ".jdk", ".gradle", "build", "dist", "run",
             "models", "research", ".git", ".pytest_cache", "snapshots", "libs"}
# Files written at runtime: a config from an older version is a user artefact,
# not project content, so it must not fail the suite.
SKIP_FILES = {"eyetrack.json", "calibration.json", "bridge_registry_backup.json"}


def _text_files() -> list[Path]:
    files = []
    for path in REPO_ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name in SKIP_FILES or not path.is_file():
            continue
        if path.suffix.lower() in SCAN_SUFFIXES:
            files.append(path)
    return files


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def test_the_name_appears_nowhere_in_the_tree():
    offenders = [str(p.relative_to(REPO_ROOT)) for p in _text_files()
                 if FORBIDDEN in _read(p).casefold()]
    assert not offenders, f"external tracker references must stay gone: {offenders}"


def test_the_old_port_is_unused():
    offenders = [str(p.relative_to(REPO_ROOT)) for p in _text_files()
                 if OLD_PORT in _read(p)]
    assert not offenders, f"port {OLD_PORT} belonged to the removed sink: {offenders}"


def test_config_has_no_section_for_an_external_tracker():
    cfg = Config()
    assert getattr(cfg, FORBIDDEN + "_udp", None) is None
    assert not [f.name for f in dataclasses.fields(Config)
                if FORBIDDEN in f.name]


def test_cli_has_no_flag_for_an_external_tracker():
    assert not [name for name in vars(_parse_args([])) if FORBIDDEN in name]


def test_the_three_remaining_sinks_still_cover_every_target():
    cfg = Config()
    names = sorted(o.name for o in build_outputs(cfg))
    assert names == ["game-link", "udp-json"]      # mouse is opt-in
    cfg.mouse.enabled = True
    assert sorted(o.name for o in build_outputs(cfg)) == [
        "game-link", "mouse", "udp-json"]


def test_requirements_list_only_real_dependencies():
    reqs = _read(REPO_ROOT / "requirements.txt").casefold()
    assert FORBIDDEN not in reqs
    for expected in ("mediapipe", "opencv", "numpy"):
        assert expected in reqs


def test_registry_bridge_targets_our_own_dll():
    from eyetrack import bridge

    assert (bridge.BRIDGE_DIR / "NPClient64.dll").exists()
    assert FORBIDDEN not in str(bridge.BRIDGE_DIR).casefold(), (
        "the bridge must point at the DLLs in this repository, never at an "
        "installed tracker")