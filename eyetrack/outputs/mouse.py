"""Mouse emulation output: head pose -> relative mouse motion (any game).

The universal fallback for games that have **no** TrackIR support:
anything you can normally look around with the mouse.  Yaw/pitch become small
relative cursor moves through Windows ``SendInput`` - exactly what a physical
mouse does, so no driver or extra software is involved.

Behaviour:

* Deltas are computed against the previously *emitted* angle, not the raw
  per-frame difference: holding a pose holds the view, returning to centre
  returns the view, and nothing drifts.
* ``deadzone_deg`` swallows tracker jitter around the neutral pose so the
  cursor sits still while you face the screen.
* Sub-pixel remainders carry over between frames - no rounding loss.
* ``toggle_key`` (F9 by default) arms/disarms the output so the desktop stays
  usable; it starts **disarmed** and says so on the console and overlay.
"""

from __future__ import annotations

import math
import sys

from ..config import MouseConfig
from .base import BaseOutput

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    ULONG_PTR = ctypes.c_size_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    INPUT_MOUSE = 0
    MOUSEEVENTF_MOVE = 0x0001

    _USER32 = ctypes.WinDLL("user32", use_last_error=True)
    _USER32.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
    _USER32.SendInput.restype = wintypes.UINT
    _USER32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _USER32.GetAsyncKeyState.restype = ctypes.c_short

    def _send_relative(dx: int, dy: int) -> None:
        inp = INPUT()
        inp.type = INPUT_MOUSE
        inp.mi.dx = int(dx)
        inp.mi.dy = int(dy)
        inp.mi.dwFlags = MOUSEEVENTF_MOVE
        sent = _USER32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        if sent != 1:
            raise OSError(f"SendInput injected {sent}/1 events "
                          f"(err {ctypes.get_last_error()})")

    def _key_is_down(vk: int) -> bool:
        return bool(_USER32.GetAsyncKeyState(vk) & 0x8000)
else:  # pragma: no cover - the tracker itself only runs on Windows
    def _send_relative(dx: int, dy: int) -> None:
        raise OSError("mouse emulation requires Windows")

    def _key_is_down(vk: int) -> bool:
        return False

#--------------------------------------------------------------------------
# Toggle key parsing: "F9", "f10", "HOME", "K", "none" -> virtual-key code.

_VK_ALIASES = {
    "INSERT": 0x2D, "HOME": 0x24, "END": 0x23,
    "PGUP": 0x21, "PRIOR": 0x21, "PGDN": 0x22, "NEXT": 0x22,
    "SCROLLLOCK": 0x91, "SCROLL": 0x91, "NUMLOCK": 0x90,
    "PAUSE": 0x13, "APPS": 0x5D, "MENU": 0x5D,
    "CAPSLOCK": 0x14, "TAB": 0x09,
}
_NO_KEYS = {"", "NONE", "OFF", "ALWAYS"}


def parse_toggle_key(name: str) -> int | None:
    """Map a key name to a Windows virtual-key code (None = always on)."""
    n = (name or "").strip().upper()
    if n in _NO_KEYS:
        return None
    if n.startswith("F") and n[1:].isdigit():
        num = int(n[1:])
        if 1 <= num <= 12:
            return 0x6F + num  # F1 = 0x70
    if n in _VK_ALIASES:
        return _VK_ALIASES[n]
    if len(n) == 1 and ("A" <= n <= "Z" or "0" <= n <= "9"):
        return ord(n)
    raise ValueError(f"unknown mouse toggle key {name!r} "
                     "(try F9/F10/..., HOME, a letter, or 'none')")


def _shape(value: float, deadzone: float) -> float:
    """Swallow movement inside the neutral-pose deadzone (0 = passthrough)."""
    if deadzone <= 0.0:
        return value
    excess = abs(value) - deadzone
    return 0.0 if excess <= 0.0 else math.copysign(excess, value)


class MouseMapper:
    """Pure pose -> pixel-delta maths: no OS calls, unit-testable.

    Sign convention (matching the tracker's pose output):

    * yaw ``+`` = head turned to the user's LEFT  -> look left  -> dx negative
    * pitch ``+`` = head looking UP               -> look up    -> dy negative

    ``invert_x`` / ``invert_y`` flip either axis.
    """

    def __init__(self, sensitivity: float = 5.0, v_sensitivity: float = 5.0,
                 deadzone_deg: float = 2.0, invert_x: bool = False,
                 invert_y: bool = False) -> None:
        self.sensitivity = float(sensitivity)
        self.v_sensitivity = float(v_sensitivity)
        self.deadzone = float(deadzone_deg)
        self._sx = 1.0 if invert_x else -1.0
        self._sy = 1.0 if invert_y else -1.0
        self._ax: float | None = None  # last emitted (shaped) yaw/pitch
        self._ay: float | None = None
        self._rx = 0.0                 # sub-pixel remainders
        self._ry = 0.0

    def reset(self) -> None:
        """Forget the anchor: the next frame recentres without jumping."""
        self._ax = self._ay = None
        self._rx = self._ry = 0.0

    def update(self, pose: dict[str, float], tracking: bool = True) -> tuple[int, int]:
        if not tracking:
            self.reset()
            return 0, 0
        tx = _shape(float(pose["yaw"]), self.deadzone)
        ty = _shape(float(pose["pitch"]), self.deadzone)
        if self._ax is None or self._ay is None:
            self._ax, self._ay = tx, ty  # first frame only anchors
            return 0, 0
        self._rx += (tx - self._ax) * self._sx * self.sensitivity
        self._ry += (ty - self._ay) * self._sy * self.v_sensitivity
        self._ax, self._ay = tx, ty
        dx = int(self._rx)
        dy = int(self._ry)
        self._rx -= dx
        self._ry -= dy
        return dx, dy


class MouseOutput(BaseOutput):
    name = "mouse"

    def __init__(self, cfg: MouseConfig) -> None:
        if sys.platform != "win32":
            raise RuntimeError("The mouse output is Windows-only")
        self.cfg = cfg
        self.toggle_key = (cfg.toggle_key or "none").strip()
        self._vk = parse_toggle_key(self.toggle_key)
        self.mapper = MouseMapper(cfg.sensitivity, cfg.v_sensitivity,
                                  cfg.deadzone_deg, cfg.invert_x, cfg.invert_y)
        self.min_interval = 1.0 / max(int(cfg.rate_hz), 1)
        self.active = self._vk is None  # no toggle key => always on
        self._key_was_down = False
        self._last_send = 0.0
        self._warned = False

    # ------------------------------------------------------------------
    def start(self) -> None:
        rate = f"{self.cfg.sensitivity:g} px/deg"
        if self._vk is None:
            print(f"[mouse] cursor control ALWAYS ON ({rate}; toggle key disabled)")
        else:
            want = "turn it OFF" if self.active else "turn it ON"
            print(f"[mouse] cursor control {'ON' if self.active else 'off'} - "
                  f"press {self.toggle_key.upper()} to {want} ({rate})")

    def status_text(self) -> str:
        """Short overlay line, e.g. ``mouse: off [F9]``."""
        if self._vk is None:
            return "mouse: ON"
        return f"mouse: {'ON' if self.active else 'off'} [{self.toggle_key.upper()}]"

    def _key_pressed(self) -> bool:
        return self._vk is not None and _key_is_down(self._vk)

    # ------------------------------------------------------------------
    def send(self, pose: dict[str, float], tracking: bool, t: float) -> None:
        if self._vk is not None:
            down = self._key_pressed()
            if down and not self._key_was_down:  # rising edge = key press
                self.active = not self.active
                self.mapper.reset()
                print(f"[mouse] cursor control {'ON' if self.active else 'OFF'} "
                      f"(toggle: {self.toggle_key.upper()})")
            self._key_was_down = down
        if not self.active or not tracking:
            self.mapper.reset()  # arming or face recovery never jumps
            return
        if t - self._last_send < self.min_interval:
            return  # anchor stays put, so the deferred delta is not lost
        self._last_send = t
        dx, dy = self.mapper.update(pose, tracking)
        if dx or dy:
            self._emit(dx, dy)

    def _emit(self, dx: int, dy: int) -> None:
        try:
            _send_relative(dx, dy)
        except OSError as exc:
            if not self._warned:  # never let telemetry kill the tracking loop
                print(f"[mouse] could not move the cursor: {exc}")
                self._warned = True

    def close(self) -> None:
        self.mapper.reset()
