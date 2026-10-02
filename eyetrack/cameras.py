"""Camera discovery and selection.

Lets the tracker pick a camera **by device name** instead of a bare index,
which matters for phone-as-webcam setups: Iriun, DroidCam, Camo, EpocCam,
OBS Virtual Camera and friends all register a *Windows virtual camera* that
sits at an arbitrary index.

Names come from DirectShow (pygrabber, pure Python, no compiler needed);
the DirectShow order matches OpenCV's ``CAP_DSHOW`` index order on a typical
machine.  Everything degrades gracefully when the helper is unavailable.

Choosing between the cameras Windows reports can be done three ways, in
descending order of how much the user has to remember:

* :func:`choose_interactively` - shows every device and takes a number or a
  name fragment at the prompt.  This is the one that needs no prior
  knowledge of indices at all.
* ``--camera-name`` / ``camera.device_name`` - a substring saved to the
  config so it only has to be picked once.
* ``--camera N`` - the raw index, for when a device name is unavailable.
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


def find_by_name(device_name: str) -> CameraDevice | None:
    """First device whose name contains ``device_name`` (case-insensitive).

    An exact (case-insensitive) match always wins over a substring one, so
    saving the full name never accidentally picks a longer sibling such as
    "OBS Virtual Camera" when "OBS" was meant.
    """
    if not device_name:
        return None
    needle = device_name.casefold()
    devices = list_devices()
    for dev in devices:
        if dev.name.casefold() == needle:
            return dev
    for dev in devices:
        if needle in dev.name.casefold():
            return dev
    return None


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
        dev = find_by_name(device_name)
        if dev is not None:
            print(f"[camera] using '{dev.name}' (index {dev.index})")
            return dev.index
        print(f"[camera] no device matching '{device_name}' - "
              f"using index {index} instead (run --list-cameras to see names)")
    return index


def choose_interactively(prompt: str = "camera") -> CameraDevice | None:
    """List every camera Windows reports and take a number or name.

    Returns ``None`` if the user cancels (empty input / Ctrl-C), so the
    caller can fall back to whatever the config already says.  Non-
    interactive sessions (no TTY, as when the tracker is launched by
    another program) return ``None`` immediately instead of hanging on a
    read that nothing will ever answer.
    """
    devices = list_devices()
    if not devices:
        print("[camera] no camera names available from Windows "
              "(pygrabber missing, or not on Windows).")
        return None
    if not sys.stdin or not sys.stdin.isatty():
        print("[camera] not an interactive session - skipping camera prompt.")
        return None

    print("\nCameras detected by Windows:")
    width = max(len(str(d.index)) for d in devices)
    for d in devices:
        print(f"  {str(d.index).rjust(width)})  {d.name}")
    print(f"\nEnter a number, or part of a name, for the {prompt} "
          f"(blank to keep the current one):")

    try:
        answer = input("  > ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if not answer:
        return None

    if answer.isdigit():
        wanted = int(answer)
        for d in devices:
            if d.index == wanted:
                return d
        print(f"[camera] no camera with index {wanted}.")
        return None

    dev = find_by_name(answer)
    if dev is None:
        print(f"[camera] no camera matching '{answer}'.")
        return None
    return dev
