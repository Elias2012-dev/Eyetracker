"""Camera discovery.

Lets the tracker pick a camera **by device name** instead of a bare index,
which matters for phone-as-webcam setups: Iriun, DroidCam, Camo, EpocCam,
OBS Virtual Camera and friends all register a *Windows virtual camera* that
sits at an arbitrary index.

Names come from DirectShow (pygrabber, pure Python, no compiler needed);
the DirectShow order matches OpenCV's ``CAP_DSHOW`` index order on a typical
machine.  Everything degrades gracefully when the helper is unavailable.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass


@dataclass
class CameraDevice:
    index: int
    name: str

    def __str__(self) -> str:
        return f"[{self.index}] {self.name}"


def list_devices() -> list[CameraDevice]:
    """DirectShow capture devices in index order (Windows only)."""
    if sys.platform != "win32":
        return []
    try:
        from pygrabber.dshow_graph import FilterGraph
    except ImportError:
        return []
    try:
        names = FilterGraph().get_input_devices()
    except Exception:
        return []
    return [CameraDevice(i, n) for i, n in enumerate(names)]


def resolve_index(device_name: str | None, index: int) -> int:
    """Pick the camera index: substring match on ``device_name`` wins.

    Falls back to the configured numeric ``index`` when no name is given or
    nothing matches (so a plain ``--camera 2`` still works).
    """
    if device_name:
        needle = device_name.casefold()
        for dev in list_devices():
            if needle in dev.name.casefold():
                print(f"[camera] using '{dev.name}' (index {dev.index})")
                return dev.index
        print(f"[camera] no device matching '{device_name}' - "
              f"using index {index} instead (run --list-cameras to see names)")
    return index
