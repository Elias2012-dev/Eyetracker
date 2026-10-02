"""Full chain test: our writer -> shared memory -> our bridge DLL -> game API.

This exercises exactly what ETS2/ATS do (minus the game process itself):
load NPClient64.dll, call NP_GetData, and validate scaling, status, frame,
checksum, signature and profile-id echo against an independent Python
implementation of the ABI.
"""

import _ctypes
import ctypes
import struct
import sys
from pathlib import Path

import pytest

from eyetrack.outputs.game_link import (
    EBTLink,
    FILE_MAP_WRITE,
    LINK_NAME,
    PAGE_READWRITE,
    GameLinkOutput,
)

DLL_PATH = Path(__file__).resolve().parent.parent / "bridge" / "NPClient64.dll"

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or not DLL_PATH.exists(),
    reason="Windows + built bridge DLL required",
)


class TirData(ctypes.Structure):
    _fields_ = [
        ("status", ctypes.c_short),
        ("frame", ctypes.c_short),
        ("cksum", ctypes.c_uint),
        ("roll", ctypes.c_float),
        ("pitch", ctypes.c_float),
        ("yaw", ctypes.c_float),
        ("tx", ctypes.c_float),
        ("ty", ctypes.c_float),
        ("tz", ctypes.c_float),
        ("padding", ctypes.c_float * 9),
    ]


assert ctypes.sizeof(TirData) == 68, "TrackIR data block layout"


# --- independent implementation of the ABI checksum -----------------------

def _i32(x: int) -> int:
    x &= 0xFFFFFFFF
    return x - 0x100000000 if x & 0x80000000 else x


def np_cksum(buf: bytes) -> int:
    size = len(buf)
    if size == 0:
        return 0
    rounds, rem = size >> 2, size & 3
    c = size
    pos = 0
    for _ in range(rounds):
        a0 = int.from_bytes(buf[pos:pos + 2], "little", signed=True)
        a2 = int.from_bytes(buf[pos + 2:pos + 4], "little", signed=True)
        pos += 4
        c = _i32(c + a0)
        a2 = _i32(a2 ^ _i32(c << 5))
        a2 = _i32(a2 << 11)
        c = _i32(c ^ a2)
        c = _i32(c + (c >> 11))
    a2 = 0
    if rem == 3:
        a0 = int.from_bytes(buf[pos:pos + 2], "little", signed=True)
        a2 = buf[pos + 2] - 256 if buf[pos + 2] > 127 else buf[pos + 2]
        c = _i32(c + a0)
        a2 = _i32(_i32(a2 << 2) ^ c)
        c = _i32(c ^ _i32(a2 << 16))
        a2 = _i32(c >> 11)
    elif rem == 2:
        a2 = int.from_bytes(buf[pos:pos + 2], "little", signed=True)
        c = _i32(c + a2)
        c = _i32(c ^ _i32(c << 11))
        a2 = _i32(c >> 17)
    elif rem == 1:
        a2 = buf[pos] - 256 if buf[pos] > 127 else buf[pos]
        c = _i32(c + a2)
        c = _i32(c ^ _i32(c << 10))
        a2 = _i32(c >> 1)
    if rem:
        c = _i32(c + a2)
    c = _i32(c ^ _i32(c << 3))
    c = _i32(c + (c >> 5))
    c = _i32(c ^ _i32(c << 4))
    c = _i32(c + (c >> 17))
    c = _i32(c ^ _i32(c << 25))
    c = _i32(c + (c >> 6))
    return c & 0xFFFFFFFF


def _load_dll():
    lib = ctypes.CDLL(str(DLL_PATH))
    lib.NP_GetData.argtypes = [ctypes.POINTER(TirData)]
    lib.NP_GetData.restype = ctypes.c_int
    lib.NP_QueryVersion.argtypes = [ctypes.POINTER(ctypes.c_ushort)]
    lib.NP_RegisterProgramProfileID.argtypes = [ctypes.c_ushort]
    return lib


def _unload(lib) -> None:
    _ctypes.FreeLibrary(lib._handle)


def _reader_view():
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
    assert handle
    ptr = k.MapViewOfFile(handle, FILE_MAP_WRITE, 0, 0, ctypes.sizeof(EBTLink))
    assert ptr
    return k, handle, ctypes.cast(ptr, ctypes.POINTER(EBTLink)).contents


def test_full_roundtrip_writer_to_dll():
    out = GameLinkOutput()
    out.start()
    lib = _load_dll()
    k, handle, view = _reader_view()
    try:
        # 1. tracker streams a pose (degrees / centimetres)
        out.send({"yaw": 90.0, "pitch": 30.0, "roll": -45.0,
                  "x": 10.0, "y": -5.0, "z": 50.0}, True, 0.0)

        # 2. the game polls NP_GetData
        d = TirData()
        rc = lib.NP_GetData(ctypes.byref(d))
        assert rc == 0 and d.status == 0, "must report active tracking"

        # 3. scaling: deg/180*16383, cm*16383/50 (clamped)
        assert abs(d.yaw - (90.0 / 180.0 * 16383)) < 0.51
        assert abs(d.pitch - (30.0 / 180.0 * 16383)) < 0.51
        assert abs(d.roll - (-45.0 / 180.0 * 16383)) < 0.51
        assert abs(d.tx - (10.0 * 16383 / 50.0)) < 0.51
        assert abs(d.ty - (-5.0 * 16383 / 50.0)) < 0.51
        assert d.tz == pytest.approx(16383.0, abs=0.51)  # 50 cm = full scale

        # 4. frame id travels through (low 16 bits)
        assert d.frame == (view.frame_id & 0xFFFF)

        # 5. checksum must equal our independent implementation
        expected = struct.pack(
            "<hhI15f",
            d.status, d.frame, 0,
            d.roll, d.pitch, d.yaw, d.tx, d.ty, d.tz,
            *([0.0] * 9),
        )
        assert d.cksum == np_cksum(expected), "ABI checksum mismatch"

        # 6. version + signature
        ver = ctypes.c_ushort(0)
        lib.NP_QueryVersion(ctypes.byref(ver))
        assert ver.value == 0x0500

        class Sig(ctypes.Structure):
            _fields_ = [("DllSignature", ctypes.c_char * 200),
                        ("AppSignature", ctypes.c_char * 200)]
        sig = Sig()
        lib.NP_GetSignature(ctypes.byref(sig))

        # expectations derived from the generated ABI header, not magic constants
        header = (Path(__file__).resolve().parent.parent /
                  "bridge" / "src" / "ebt_signatures.h").read_text()
        import re
        arrays = {}
        for name in ("part1_1", "part1_2", "part2_1", "part2_2"):
            m = re.search(r"%s\[200\] = \{([^}]*)\}" % name, header)
            arrays[name] = bytes(int(x, 16) for x in re.findall(r"0x[0-9a-fA-F]{2}", m.group(1)))
            arrays[name] = arrays[name].ljust(200, b"\x00")
        expect_dll = bytes(a ^ b for a, b in zip(arrays["part1_2"], arrays["part1_1"]))
        expect_app = bytes(a ^ b for a, b in zip(arrays["part2_1"], arrays["part2_2"]))
        # ctypes c_char*N stops at the first NUL - compare the C strings
        assert sig.DllSignature == expect_dll.split(b"\x00")[0], "signature blob mismatch"
        assert sig.AppSignature == expect_app.split(b"\x00")[0], "signature blob mismatch"

        # 7. game registers its profile id -> echoed by the writer
        lib.NP_RegisterProgramProfileID(77)
        assert view.game_id == 77
        out.send({"yaw": 1.0, "pitch": 0, "roll": 0,
                  "x": 0, "y": 0, "z": 0}, True, 0.0)
        assert view.game_id_ack == 77

        # 8. face lost -> game sees "not tracking"
        out.send({"yaw": 1.0, "pitch": 0, "roll": 0,
                  "x": 0, "y": 0, "z": 0}, False, 0.0)
        rc = lib.NP_GetData(ctypes.byref(d))
        assert rc == 1 and d.status == 1
    finally:
        out.close()
        _unload(lib)
        k.UnmapViewOfFile(ctypes.addressof(view))
        k.CloseHandle(handle)


def test_yaw_clamps_at_axis_max():
    out = GameLinkOutput()
    out.start()
    lib = _load_dll()
    try:
        out.send({"yaw": 400.0, "pitch": -400.0, "roll": 0,
                  "x": 0, "y": 0, "z": 0}, True, 0.0)
        d = TirData()
        lib.NP_GetData(ctypes.byref(d))
        assert d.yaw == 16383.0
        assert d.pitch == -16383.0
    finally:
        out.close()
        _unload(lib)
