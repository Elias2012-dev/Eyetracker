"""Tests for the setup splash.

The splash exists for one reason: on a fresh install, probing the cameras
takes several seconds per device, and with nothing on screen that reads as
a hung app. So the tests here are about it actually saying something, and
about staying out of the way when there is no display to draw on.
"""

from __future__ import annotations

import numpy as np
import pytest

from eyetrack.splash import W, H, Splash, render


def _bright(img: np.ndarray) -> np.ndarray:
    return img.max(axis=2)


def test_a_splash_frame_is_a_real_image():
    img = render("Looking for your camera", "testing Camo...")
    assert img.shape == (H, W, 3)
    assert img.dtype == np.uint8
    assert len(np.unique(img.reshape(-1, 3), axis=0)) > 3, "nothing was drawn"


def test_the_title_and_detail_are_actually_painted():
    """A splash that renders an empty card is worse than no splash."""
    blank = render("x")
    titled = render("Looking for your camera")
    assert _bright(titled).sum() > _bright(blank).sum(), "the title is not drawn"
    with_detail = render("Looking for your camera", "testing Camo...")
    assert _bright(with_detail).sum() > _bright(titled).sum(), \
        "the detail line is not drawn"


def test_the_spinner_animates_so_the_window_is_never_motionless():
    frames = {render("Looking for your camera", frame=i).tobytes()
              for i in range(len("|/-\\"))}
    assert len(frames) > 1, "a frozen spinner looks exactly like a hang"


def test_done_ends_the_spinner_and_fills_the_bar():
    busy, done = render("Camera found", "Camo - a face"), render(
        "Camera found", "Camo - a face", done=True)
    assert not np.array_equal(busy, done)
    # The finished bar is green; the working one is not.
    bar = done[H - 46:H - 40, W // 2 - 20:W // 2 + 20]
    assert bar[:, :, 1].min() > bar[:, :, 0].max(), "the done bar is not green"


def test_a_disabled_splash_never_opens_a_window(monkeypatch):
    """Headless launches must not die trying to draw a progress bar."""
    import eyetrack.splash as splash_mod

    def explode(*_a, **_k):
        raise AssertionError("tried to draw a window")

    monkeypatch.setattr(splash_mod.cv2, "namedWindow", explode)
    monkeypatch.setattr(splash_mod.cv2, "imshow", explode)

    s = Splash(enabled=False)
    s.update("Looking for your camera", "testing Camo...")
    s.finish("Camera found", "Camo")
    s.close()


def test_an_opencv_failure_disables_the_splash_instead_of_propagating(monkeypatch):
    """A display that refuses the window is not a reason to abort setup."""
    import eyetrack.splash as splash_mod

    monkeypatch.setattr(splash_mod.cv2, "namedWindow",
                        lambda *_a, **_k: (_ for _ in ()).throw(
                            splash_mod.cv2.error("no display")))
    s = Splash()
    s.update("Looking for your camera")          # must not raise
    assert s.enabled is False, "it should have given up quietly"


def test_the_splash_is_shown_while_the_camera_is_being_found(monkeypatch, tmp_path):
    """app._auto_detect has to report progress, not sit in silence."""
    import eyetrack.app as app

    seen: list[tuple[str, str]] = []

    class _Recorder:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def update(self, title, detail="", note=""):
            seen.append((title, detail))

        def finish(self, title, detail=""):
            seen.append((title, detail))

        def close(self):
            pass

    monkeypatch.setattr("eyetrack.splash.Splash", lambda **_k: _Recorder())

    from eyetrack.cameras import CameraDevice
    from eyetrack.config import Config

    calls: list[object] = []

    def fake_detect(devices=None, *, on_progress=None, **_kw):
        calls.append(devices)
        if on_progress is not None:
            on_progress(CameraDevice(index=0, name="Camo"))
        return None

    monkeypatch.setattr("eyetrack.autodetect.detect_camera", fake_detect)

    app._auto_detect(Config(), tmp_path / "eyetrack.json",
                     estimator=object())

    assert seen, "nothing was reported to the user while probing"
    assert any("Camo" in detail for _, detail in seen), (
        f"the device under test was never named: {seen}")
    assert seen[-1][0] in ("No camera found", "Camera found"), \
        f"the outcome was never stated: {seen}"


@pytest.mark.parametrize("frame", [0, 1, 2, 3, 99])
def test_the_splash_never_draws_outside_its_window(frame):
    """A stray marker off the card would paint over the desktop."""
    img = render("Looking for your camera", "testing a very long device name " * 3,
                 frame=frame)
    painted = np.argwhere(img.max(axis=2) > 0)
    assert painted[:, 0].min() >= 0 and painted[:, 0].max() < H
    assert painted[:, 1].min() >= 0 and painted[:, 1].max() < W