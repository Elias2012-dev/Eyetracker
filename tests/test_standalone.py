"""The tracker must stand on its own: no opentrack, no FreeTrack, no driver.

The opentrack UDP sink is kept as an escape hatch for people who already run
opentrack, but nothing in the project may *depend* on it. These tests fail if
that ever changes by accident - e.g. someone making the sink default-on,
importing opentrack, or pointing the registry bridge at an installed copy.
"""

import re

from eyetrack.__main__ import _parse_args
from eyetrack.config import Config
from eyetrack.outputs import build_outputs

PACKAGE = __import__("pathlib").Path(__file__).resolve().parent.parent / "eyetrack"


def test_opentrack_is_off_by_default():
    assert Config().opentrack_udp.enabled is False


def test_no_opentrack_sink_without_asking_for_one():
    names = [o.name for o in build_outputs(Config())]
    assert "opentrack-udp" not in names
    # The defaults still produce the two paths that matter, so the project
    # is useful with the escape hatch closed.
    assert "game-link" in names
    assert "udp-json" in names


def test_opentrack_still_available_as_an_opt_in():
    cfg = Config()
    cfg.opentrack_udp.enabled = True
    assert "opentrack-udp" in [o.name for o in build_outputs(cfg)]


def test_opentrack_flag_is_explicit_opt_in():
    assert _parse_args([]).opentrack is False
    assert _parse_args(["--opentrack"]).opentrack is True


def test_nothing_in_the_package_imports_opentrack():
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.match(r"\s*(import opentrack|from opentrack)\b", line):
                offenders.append(f"{path.name}:{lineno}")
    assert not offenders, f"the tracker must not depend on opentrack: {offenders}"


def test_registry_bridge_targets_our_own_dll():
    from eyetrack import bridge

    assert bridge.BRIDGE_DIR.name == "bridge"
    assert (bridge.BRIDGE_DIR / "NPClient64.dll").exists()
    assert "opentrack" not in str(bridge.BRIDGE_DIR).lower(), (
        "the bridge must point at the DLLs in this repository, never at an "
        "installed tracker"
    )


def test_opentrack_is_not_a_dependency_of_the_requirements():
    reqs = (PACKAGE.parent / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "opentrack" not in reqs