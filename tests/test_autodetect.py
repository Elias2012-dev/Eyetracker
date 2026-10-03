"""First-run camera detection.

The interesting cases here are the ones that bit during development: a
camera that opens successfully and then never delivers a frame, and a
virtual camera that returns frames of nothing but black. Both count as
"opened", and a naive probe would happily pick either.
"""

from __future__ import annotations

import numpy as np
import pytest

from eyetrack import autodetect as A
from eyetrack.cameras import CameraDevice

DEVICES = [
    CameraDevice(0, "Camo"),
    CameraDevice(1, "Pixel 8 (Windows Virtuell Kamera)"),
    CameraDevice(2, "OBS Virtual Camera"),
]


NEVER = object()   # sentinel: a camera that opens but never returns a frame


class FakeClock:
    """Virtual time: a camera that never delivers would otherwise
    busy-wait for the real timeout and make the suite take minutes."""

    def __init__(self, step: float = 1.0):
        self.t = 0.0
        self.step = step

    def __call__(self) -> float:
        self.t += self.step
        return self.t


@pytest.fixture
def clock():
    return FakeClock()


class FakeCapture:
    """Stands in for cv2.VideoCapture with scripted behaviour per index."""

    # index -> list of frames, or NEVER for "opens but never delivers"
    script: dict = {}
    opened: set = set()

    def __init__(self, index, *_args, **_kwargs):
        self.index = index
        self._ok = index in FakeCapture.opened
        planned = FakeCapture.script.get(index, [])
        self._never = planned is NEVER
        self._frames = [] if planned is NEVER else list(planned)
        self.released = False
        self.props_set = []

    def isOpened(self):
        return self._ok

    def set(self, prop, value):
        self.props_set.append((prop, value))

    def read(self):
        if not self._ok or self._never or not self._frames:
            return False, None          # a camera that never delivers
        return True, self._frames.pop(0)

    def release(self):
        self.released = True


@pytest.fixture
def fake_capture(monkeypatch):
    FakeCapture.script = {}
    FakeCapture.opened = set()
    monkeypatch.setattr(A.cv2, "VideoCapture", FakeCapture)
    return FakeCapture


def frame(value=120):
    return np.full((48, 64, 3), value, np.uint8)


# ------------------------------------------------------------- looks_like_black
def test_a_black_frame_is_recognised():
    assert A.looks_like_black(np.zeros((8, 8, 3), np.uint8)) is True


def test_a_lit_frame_is_not_black():
    assert A.looks_like_black(frame(120)) is False


def test_blackness_is_permissive():
    """A dim but real scene must not be thrown away."""
    assert A.looks_like_black(frame(3)) is True
    assert A.looks_like_black(frame(10)) is False


# ---------------------------------------------------------------------- probe
def test_probe_reports_a_camera_that_will_not_open(fake_capture):
    fake_capture.opened = set()
    result = A.probe(2, "OBS Virtual Camera")
    assert result.opened is False
    assert result.delivered is False
    assert result.usable is False


def test_probe_rejects_a_camera_that_opens_but_never_delivers(fake_capture, clock):
    """The exact failure seen on the dev machine: opens, then silence."""
    fake_capture.opened = {0}
    fake_capture.script = {0: NEVER}
    result = A.probe(0, "Camo", clock=clock)
    assert result.opened is True, "it did open - that is the trap"
    assert result.delivered is False
    assert result.usable is False, "must not be considered usable"


def test_probe_accepts_a_camera_that_delivers_frames(fake_capture, clock):
    fake_capture.opened = {0}
    fake_capture.script = {0: [frame(), frame()]}
    result = A.probe(0, "Camo", face_detector=lambda f: True, clock=clock)
    assert result.opened and result.delivered and result.face
    assert result.usable is True


def test_probe_ignores_a_black_frame_when_looking_for_a_face(fake_capture, clock):
    """An unlit camera returns frames; running the landmarker on them is
    wasted time and the frames must not count as 'showing a face'."""
    fake_capture.opened = {0}
    fake_capture.script = {0: [np.zeros((48, 64, 3), np.uint8)]}
    called = []

    def detector(f):
        called.append(f)
        return True

    result = A.probe(0, "Camo", face_detector=detector, clock=clock)
    assert called == [], "the landmarker ran on a black frame"
    assert result.face is False
    assert result.delivered is True, "frames did arrive, they were just black"


def test_probe_stops_early_once_a_face_is_found(fake_capture, clock):
    fake_capture.opened = {0}
    fake_capture.script = {0: [frame()] * 20}
    result = A.probe(0, "Camo", face_detector=lambda f: True, clock=clock)
    assert result.usable is True
    assert result.seconds < 5


def test_probe_releases_the_camera(fake_capture, monkeypatch, clock):
    """A leaked handle keeps the device busy and breaks the next probe."""
    released = []
    real_release = FakeCapture.release

    def record(self):
        released.append(self.index)
        real_release(self)

    monkeypatch.setattr(FakeCapture, "release", record)
    fake_capture.opened = {0}
    fake_capture.script = {0: [frame()]}
    A.probe(0, "Camo", clock=clock)
    assert released == [0], "the camera was never released"


# ------------------------------------------------------------- detect_camera
def test_detect_prefers_a_camera_that_shows_a_face(fake_capture, clock):
    """Index 0 delivers frames but shows no face; index 1 shows a face.

    One detector has to tell them apart, so each frame is tagged in its
    top-left pixel with the index it came from.
    """
    fake_capture.opened = {0, 1, 2}
    no_face = frame(50)
    no_face[0, 0, 0] = 1
    has_face = frame(200)
    has_face[0, 0, 0] = 2
    fake_capture.script = {0: [no_face] * 5, 1: [has_face] * 5, 2: NEVER}

    result = A.detect_camera(devices=DEVICES,
                             face_detector=lambda f: f[0, 0, 0] == 2)
    assert result is not None
    assert result.usable is True
    assert result.index == 1, "picked the camera without a face"


def test_detect_returns_none_when_nothing_delivers(fake_capture, clock):
    fake_capture.opened = {0, 1}
    fake_capture.script = {0: NEVER, 1: NEVER}
    assert A.detect_camera(devices=DEVICES,
                           face_detector=lambda f: True, clock=clock) is None


def test_detect_returns_none_with_no_devices(fake_capture):
    assert A.detect_camera(devices=[]) is None


def test_detect_falls_back_to_a_camera_with_frames_but_no_face(fake_capture, clock):
    """Better than nothing: the wizard can start and ask the user to sit in
    frame, instead of claiming no camera was found."""
    fake_capture.opened = {0, 1}
    fake_capture.script = {0: NEVER, 1: [frame()] * 3}
    result = A.detect_camera(devices=DEVICES,
                             face_detector=lambda f: False, clock=clock)
    assert result is not None
    assert result.delivered is True
    assert result.face is False
    assert result.usable is False


def test_detect_tries_the_saved_name_first(fake_capture, clock):
    """Indices shuffle; the name is the stable identifier, so a returning
    user must keep their camera even if a lower index also works."""
    fake_capture.opened = {0, 1}
    fake_capture.script = {0: [frame()] * 3, 1: [frame()] * 3}
    result = A.detect_camera(devices=DEVICES,
                             prefer_name="Pixel 8",
                             face_detector=lambda f: True, clock=clock)
    assert result is not None
    assert result.name == "Pixel 8 (Windows Virtuell Kamera)", (
        "the saved camera was not preferred over a lower index")


def test_detect_does_not_probe_more_than_the_cap(fake_capture, monkeypatch, clock):
    """An endless device list must not turn a cold start into a coffee break."""
    many = [CameraDevice(i, f"Cam {i}") for i in range(30)]
    fake_capture.opened = set(range(30))
    fake_capture.script = {i: NEVER for i in range(30)}

    probed = []

    class Counting(FakeCapture):
        def __init__(self, index, *a, **k):
            probed.append(index)
            super().__init__(index, *a, **k)

    monkeypatch.setattr(A.cv2, "VideoCapture", Counting)
    A.detect_camera(devices=many, face_detector=lambda f: True, clock=clock)
    assert probed == list(range(A.MAX_CANDIDATES)), (
        f"probed {probed} - expected at most {A.MAX_CANDIDATES}")


def test_probe_is_bounded_in_time(fake_capture, clock):
    """A camera that blocks must not hang the first run."""
    fake_capture.opened = {0}
    fake_capture.script = {0: NEVER}
    result = A.probe(0, "slow", clock=clock)
    assert result.seconds <= A.OPEN_TIMEOUT_S + 2, (
        f"probe took {result.seconds:.1f}s, budget is {A.OPEN_TIMEOUT_S}s")