"""Camera discovery and selection.

The picker is the only way a user with several webcams picks one, and it
runs inside a GUI-less console - so the cases that matter are the awkward
ones: no TTY, a cancelled prompt, an out-of-range index, and a name that
matches more than one device.
"""

from __future__ import annotations

from unittest import mock

import pytest

from eyetrack import cameras as C
from eyetrack.cameras import CameraDevice, find_by_name

DEVICES = [
    CameraDevice(0, "Camo"),
    # Shaped like a real phone-as-webcam entry: Windows names the virtual
    # camera after the device model.
    CameraDevice(1, "Pixel 8 (Windows Virtuell Kamera)"),
    CameraDevice(2, "OBS Virtual Camera"),
]


@pytest.fixture
def devices(monkeypatch):
    """Pretend Windows reports DEVICES, whatever the host really has.

    A fresh list per test: these tests must not be able to perturb each
    other by editing the module-level DEVICES.
    """
    snapshot = list(DEVICES)
    monkeypatch.setattr(C, "list_devices", lambda: list(snapshot))
    return snapshot


# ------------------------------------------------------------------ naming
def test_device_renders_as_index_and_name():
    assert str(DEVICES[0]) == "[0] Camo"


def test_find_by_name_matches_case_insensitively(devices):
    assert find_by_name("camo").index == 0
    assert find_by_name("OBS").index == 2


def test_find_by_name_prefers_an_exact_match(monkeypatch):
    """A saved full name must not drift onto a longer sibling.

    Picking a saved "OBS Virtual Camera" must never land on an
    "OBS Virtual Camera 2" that happens to be listed first.
    """
    monkeypatch.setattr(C, "list_devices", lambda: [
        CameraDevice(9, "OBS Virtual Camera 2"),
        CameraDevice(2, "OBS Virtual Camera"),
    ])
    assert find_by_name("OBS Virtual Camera").index == 2


def test_find_by_name_returns_none_when_absent(devices):
    assert find_by_name("webcam that does not exist") is None
    assert find_by_name("") is None


def test_resolve_index_prefers_the_name_over_the_stored_index(devices, capsys):
    assert C.resolve_index("OBS", 0) == 2
    assert "OBS Virtual Camera" in capsys.readouterr().out


def test_resolve_index_falls_back_when_nothing_matches(devices, capsys):
    assert C.resolve_index("nope", 3) == 3
    out = capsys.readouterr().out
    assert "no device matching" in out, "the fallback must be explained"


def test_resolve_index_with_no_name_uses_the_index(devices):
    assert C.resolve_index("", 2) == 2
    assert C.resolve_index(None, 2) == 2


# ------------------------------------------------------------------ picker
def _answer(monkeypatch, text: str) -> None:
    """Make the prompt interactive and return ``text`` for it."""
    tty = mock.Mock()
    tty.isatty.return_value = True
    monkeypatch.setattr(C.sys, "stdin", tty)
    monkeypatch.setattr("builtins.input", lambda *_a: text)


def test_picker_returns_none_without_a_tty(devices, monkeypatch, capsys):
    """A piped/redirected stdin must never block waiting for an answer.

    The tracker can be launched by something that owns stdin, and a hang
    there looks like a frozen application.
    """
    piped = mock.Mock()
    piped.isatty.return_value = False
    monkeypatch.setattr(C.sys, "stdin", piped)
    monkeypatch.setattr("builtins.input",
                        lambda *_a: pytest.fail("must not prompt without a TTY"))
    assert C.choose_interactively() is None
    assert "not an interactive session" in capsys.readouterr().out


def test_picker_returns_none_when_no_devices(monkeypatch, capsys):
    monkeypatch.setattr(C, "list_devices", list)
    assert C.choose_interactively() is None
    assert "no camera names available" in capsys.readouterr().out


@pytest.mark.parametrize("text,expected", [
    ("0", 0),          # by number
    ("1", 1),
    ("Camo", 0),       # by full name
    ("camo", 0),       # case-insensitive
    ("OBS", 2),        # by fragment
    ("windows", 1),    # fragment from the middle of a name
])
def test_picker_accepts_a_number_or_a_name(devices, monkeypatch, text, expected):
    _answer(monkeypatch, text)
    assert C.choose_interactively().index == expected


@pytest.mark.parametrize("text", ["", "   ", "99", "not a camera"])
def test_picker_returns_none_for_unusable_answers(devices, monkeypatch, text):
    """Cancel, out-of-range and no-match all fall back to the config."""
    _answer(monkeypatch, text)
    assert C.choose_interactively() is None


def test_picker_lists_every_device(devices, monkeypatch, capsys):
    _answer(monkeypatch, "")
    C.choose_interactively()
    out = capsys.readouterr().out
    for d in devices:
        assert d.name in out, f"{d.name!r} must be offered to the user"


def test_picker_survives_ctrl_c(devices, monkeypatch):
    _answer(monkeypatch, "")
    monkeypatch.setattr("builtins.input",
                        mock.Mock(side_effect=KeyboardInterrupt))
    assert C.choose_interactively() is None
