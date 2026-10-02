"""Game-link shared memory tests (Windows only - skipped elsewhere)."""

import ctypes
import sys

import pytest

from eyetrack.outputs.game_link import (
    EBTLink,
    FILE_MAP_WRITE,
    LINK_MUTEX,
    LINK_NAME,
    PAGE_READWRITE,
    GameLinkOutput,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")

POSE = {"yaw": 33.0, "pitch": -10.0, "roll": 1.5, "x": 5.0, "y": 2.0, "z": -1.0}


def _open_reader():
    """Second view of the block - exactly what the in-game DLL sees."""
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32")
    k.CreateFileMappingW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR]
    k.CreateFileMappingW.restype = wintypes.HANDLE
    k.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                wintypes.DWORD, ctypes.c_size_t]
    k.MapViewOfFile.restype = ctypes.c_void_p
    k.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = k.CreateFileMappingW(ctypes.c_void_p(-1).value, None, PAGE_READWRITE,
                                  0, ctypes.sizeof(EBTLink), LINK_NAME)
    assert handle, "could not open the game-link block - has start() run?"
    ptr = k.MapViewOfFile(handle, FILE_MAP_WRITE, 0, 0, ctypes.sizeof(EBTLink))
    assert ptr
    return k, handle, ctypes.cast(ptr, ctypes.POINTER(EBTLink)).contents


def test_struct_layout():
    assert ctypes.sizeof(EBTLink) == 56


def test_send_and_read_back():
    out = GameLinkOutput()
    out.start()
    k, handle, view = _open_reader()
    try:
        view.game_id = 77          # a game registering its profile id
        out.send(POSE, True, 0.0)

        assert abs(view.yaw_deg - 33.0) < 1e-6
        assert abs(view.pitch_deg - (-10.0)) < 1e-6
        assert abs(view.roll_deg - 1.5) < 1e-6
        assert abs(view.x_cm - 5.0) < 1e-6
        assert abs(view.z_cm - (-1.0)) < 1e-6
        assert view.tracking == 1
        assert view.frame_id >= 1
        assert view.game_id_ack == 77, "profile id must be echoed"

        prev = view.frame_id
        out.send(POSE, False, 0.0)  # face lost: flag flips, frame keeps ticking
        assert view.tracking == 0
        assert view.frame_id == prev + 1
    finally:
        out.close()
        k.UnmapViewOfFile(ctypes.addressof(view))
        k.CloseHandle(handle)


def test_cold_start_creates_mapping():
    out = GameLinkOutput()
    out.start()
    out.close()
