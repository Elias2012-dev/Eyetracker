"""Tests for the settings-window camera preview.

The encoder is a pure function, so these tests need neither a camera nor a
Tk display - which matters, because CI has neither.
"""

from __future__ import annotations

import numpy as np
import pytest

from eyetrack.config import Config
from eyetrack.preview import (PREVIEW_MAX_H, PREVIEW_MAX_W, CameraPreview,
                              fit_size, frame_to_ppm)


def parse_ppm(blob: bytes):
    """Split a binary PPM into (width, height, rgb bytes) the way Tk would."""
    assert blob.startswith(b"P6\n")
    head, _, rest = blob[3:].partition(b"\n255\n")
    w, h = (int(part) for part in head.split())
    return w, h, rest


# ---------------------------------------------------------------- fit_size
def test_fit_size_keeps_aspect_and_fits():
    # 16:9 in a 320x240 box is width-limited: 320x180, not a squashed 320x240.
    w, h = fit_size(1920, 1080)
    assert (w, h) == (PREVIEW_MAX_W, 180)


def test_fit_size_never_upscales():
    # A small camera stays its own size instead of being smeared up.
    assert fit_size(160, 120) == (160, 120)


def test_fit_size_handles_portrait():
    w, h = fit_size(480, 640)
    assert (w, h) == (180, 240)
    assert w <= PREVIEW_MAX_W and h <= PREVIEW_MAX_H


def test_fit_size_rounds_and_never_returns_zero():
    # An extreme aspect ratio must not collapse a dimension to 0.
    w, h = fit_size(2000, 3)
    assert w >= 1 and h >= 1


@pytest.mark.parametrize("bad", [(0, 100), (100, 0), (-1, 5)])
def test_fit_size_rejects_degenerate_frames(bad):
    with pytest.raises(ValueError):
        fit_size(*bad)


def test_fit_size_rejects_degenerate_targets():
    with pytest.raises(ValueError):
        fit_size(100, 100, max_w=0)


# ------------------------------------------------------------- frame_to_ppm
def test_frame_to_ppm_emits_valid_header_and_size():
    frame = np.zeros((480, 640, 3), np.uint8)
    blob, w, h = frame_to_ppm(frame)
    assert (w, h) == (PREVIEW_MAX_W, 240)
    pw, ph, body = parse_ppm(blob)
    assert (pw, ph) == (w, h)
    assert len(body) == w * h * 3


def test_frame_to_ppm_converts_bgr_to_rgb():
    # One blue pixel. OpenCV says BGR, PPM says RGB - if this is skipped the
    # preview shows every colour with red and blue swapped.
    frame = np.zeros((2, 2, 3), np.uint8)
    frame[0, 0] = (255, 0, 0)
    _blob, _w, _h = frame_to_ppm(frame)
    body = parse_ppm(_blob)[2]
    px = 0
    assert (body[px], body[px + 1], body[px + 2]) == (0, 0, 255)


def test_frame_to_ppm_mirrors_when_asked():
    left = np.zeros((2, 4, 3), np.uint8)
    left[:, 0] = 255          # white column on the left
    last_pixel = (0 * 4 + 3) * 3      # top-right pixel in a 4-wide image
    plain = parse_ppm(frame_to_ppm(left)[0])[2]
    flipped = parse_ppm(frame_to_ppm(left, mirror=True)[0])[2]
    assert plain[0] == 255 and plain[last_pixel] == 0
    assert flipped[0] == 0 and flipped[last_pixel] == 255


def test_frame_to_ppm_preserves_size_when_it_fits():
    blob, w, h = frame_to_ppm(np.zeros((120, 160, 3), np.uint8))
    assert (w, h) == (160, 120)
    assert len(parse_ppm(blob)[2]) == 160 * 120 * 3


def test_frame_to_ppm_rejects_empty_frames():
    with pytest.raises(ValueError):
        frame_to_ppm(np.zeros((0, 0, 3), np.uint8))
    with pytest.raises(ValueError):
        frame_to_ppm(None)


# ------------------------------------------------------------ CameraPreview
class FakeCap:
    """Stands in for cv2.VideoCapture."""

    def __init__(self, frames=None, read_error=None):
        self._frames = list(frames or [])
        self._read_error = read_error
        self.released = False
        self.requested = None

    def isOpened(self):
        return not self.released

    def set(self, prop, value):
        pass

    def read(self):
        if self._read_error:
            raise self._read_error
        if self._frames:
            return True, self._frames.pop(0)
        return False, None

    def release(self):
        self.released = True


def test_preview_opens_via_the_shared_opener(monkeypatch):
    """Preview and tracking must agree on the camera, so both call the
    same opener - a preview that opened differently would lie."""
    import eyetrack.session as session

    cap = FakeCap()
    monkeypatch.setattr(session, "open_camera", lambda cfg: cap)

    prev = CameraPreview(Config())
    assert prev.open() is True
    assert prev.opened and prev.error == ""
    assert prev.cap is cap


def test_preview_soft_fails_instead_of_raising(monkeypatch):
    import eyetrack.session as session

    def boom(cfg):
        raise SystemExit("Could not open camera index 3")

    monkeypatch.setattr(session, "open_camera", boom)
    prev = CameraPreview(Config())
    assert prev.open() is False
    assert "index 3" in prev.error
    assert prev.opened is False


def test_preview_read_returns_none_when_closed():
    prev = CameraPreview(Config())
    assert prev.read() is None        # never opened


def test_preview_read_surfaces_stalled_camera(monkeypatch):
    import eyetrack.session as session

    monkeypatch.setattr(session, "open_camera", lambda cfg: FakeCap())
    prev = CameraPreview(Config())
    prev.open()
    assert prev.read() is None        # FakeCap yields no frames


def test_preview_reports_driver_errors_without_crashing(monkeypatch):
    import eyetrack.session as session

    cap = FakeCap(read_error=RuntimeError("driver went away"))
    monkeypatch.setattr(session, "open_camera", lambda cfg: cap)
    prev = CameraPreview(Config())
    prev.open()
    assert prev.read() is None
    assert "driver went away" in prev.error


def test_preview_close_releases_the_device(monkeypatch):
    import eyetrack.session as session

    cap = FakeCap()
    monkeypatch.setattr(session, "open_camera", lambda cfg: cap)
    prev = CameraPreview(Config())
    prev.open()
    prev.close()
    assert cap.released is True
    assert prev.opened is False


def test_preview_reopen_releases_the_previous_device(monkeypatch):
    """Reopening must not leak the first handle: Windows cameras are
    exclusive, so a leaked one blocks tracking entirely."""
    import eyetrack.session as session

    caps = [FakeCap(), FakeCap()]
    monkeypatch.setattr(session, "open_camera", lambda cfg: caps.pop(0))
    prev = CameraPreview(Config())
    prev.open()
    prev.open()
    assert caps == []
    assert prev.cap is not None


def test_preview_double_close_is_safe():
    prev = CameraPreview(Config())
    prev.close()
    prev.close()