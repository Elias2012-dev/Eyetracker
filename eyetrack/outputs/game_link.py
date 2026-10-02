"""Direct "game link" output for ETS2 / ATS (Windows only, fully our own).

The tracker maintains a shared memory block (``EBT_GameLink_v1``) that our
own bridge DLL (``bridge/NPClient64.dll``, built from ``bridge/src``) reads
inside the game process.  The game finds the DLL through two HKCU registry
values written by :mod:`eyetrack.bridge`.  No third-party tracker software
is involved - the DLL is compiled from source in this repository.

Block layout (56 bytes, must match ``bridge/src/ebt_npclient.c``)::

    uint32 frame_id        bumped every frame
    uint32 tracking        1 = fresh pose, 0 = face lost
    float  yaw_deg         + = turned to the user's LEFT
    float  pitch_deg       + = looking UP
    float  roll_deg
    float  x_cm, y_cm, z_cm  +right, +up, +away from screen (centred)
    int32  game_id         written by the game (profile id)
    int32  game_id_ack     echoed back by us
    uint32 reserved[4]
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from .base import BaseOutput

LINK_NAME = "EBT_GameLink_v1"
LINK_MUTEX = "EBT_GameLink_v1_Mutex"

PAGE_READWRITE = 0x00000004
FILE_MAP_WRITE = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
WAIT_OBJECT_0 = 0x00000000


class EBTLink(ctypes.Structure):
    _fields_ = [
        ("frame_id", ctypes.c_uint32),
        ("tracking", ctypes.c_uint32),
        ("yaw_deg", ctypes.c_float),
        ("pitch_deg", ctypes.c_float),
        ("roll_deg", ctypes.c_float),
        ("x_cm", ctypes.c_float),
        ("y_cm", ctypes.c_float),
        ("z_cm", ctypes.c_float),
        ("game_id", ctypes.c_int32),
        ("game_id_ack", ctypes.c_int32),
        ("reserved", ctypes.c_uint32 * 4),
    ]


assert ctypes.sizeof(EBTLink) == 56, "layout must match the bridge DLL"


class GameLinkOutput(BaseOutput):
    name = "game-link"

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("The game link is Windows-only")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._handle = None
        self._mutex = None
        self._ptr: int | None = None
        self._link: EBTLink | None = None

    # ------------------------------------------------------------------
    def start(self) -> None:
        k = self._kernel32
        k.CreateFileMappingW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                         wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR]
        k.CreateFileMappingW.restype = wintypes.HANDLE
        k.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.DWORD, ctypes.c_size_t]
        k.MapViewOfFile.restype = ctypes.c_void_p
        k.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        k.CreateMutexW.restype = wintypes.HANDLE
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.ReleaseMutex.argtypes = [wintypes.HANDLE]
        k.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        k.UnmapViewOfFile.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL

        handle = k.CreateFileMappingW(INVALID_HANDLE_VALUE, None, PAGE_READWRITE,
                                      0, ctypes.sizeof(EBTLink), LINK_NAME)
        if not handle:
            raise OSError(f"CreateFileMapping failed: {ctypes.get_last_error()}")
        ptr = k.MapViewOfFile(handle, FILE_MAP_WRITE, 0, 0, ctypes.sizeof(EBTLink))
        if not ptr:
            raise OSError(f"MapViewOfFile failed: {ctypes.get_last_error()}")
        mutex = k.CreateMutexW(None, False, LINK_MUTEX)
        if not mutex:
            raise OSError(f"CreateMutex failed: {ctypes.get_last_error()}")

        self._handle = handle
        self._mutex = mutex
        self._ptr = ptr
        self._link = ctypes.cast(ptr, ctypes.POINTER(EBTLink)).contents
        print(f"[game-link] shared memory ready ({LINK_NAME})")

    def _locked(self):
        output = self

        class _Ctx:
            def __enter__(self):
                return output._kernel32.WaitForSingleObject(output._mutex, 50) == WAIT_OBJECT_0

            def __exit__(self, *exc):
                output._kernel32.ReleaseMutex(output._mutex)
                return False

        return _Ctx()

    # ------------------------------------------------------------------
    def send(self, pose: dict[str, float], tracking: bool, t: float) -> None:
        if self._link is None:
            return
        with self._locked() as ok:
            if not ok:
                return
            link = self._link
            link.yaw_deg = float(pose["yaw"])
            link.pitch_deg = float(pose["pitch"])
            link.roll_deg = float(pose["roll"])
            link.x_cm = float(pose["x"])
            link.y_cm = float(pose["y"])
            link.z_cm = float(pose["z"])
            link.tracking = 1 if tracking else 0
            # Acknowledge the game's profile id (echo for our own diagnostics).
            link.game_id_ack = int(link.game_id)
            link.frame_id = (int(link.frame_id) + 1) & 0xFFFFFFFF

    def close(self) -> None:
        k = self._kernel32
        if self._ptr:
            k.UnmapViewOfFile(self._ptr)
            self._ptr = None
            self._link = None
        if self._mutex:
            k.CloseHandle(self._mutex)
            self._mutex = None
        if self._handle:
            k.CloseHandle(self._handle)
            self._handle = None
