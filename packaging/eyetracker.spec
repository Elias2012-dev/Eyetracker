# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: a standalone, windowed Eyetracker.exe.

Run through build_exe.bat (it creates the venv, installs PyInstaller and
sets the mode). Set EYE_TRACKER_ONEDIR=1 for a folder build instead of a
single file.

Two modes, one trade-off:

* onefile (default) - one Eyetracker.exe to copy anywhere. It unpacks
  itself into a temp directory on every launch, so startup costs a few
  seconds and RAM equal to the bundle size.
* onedir (--onedir) - a folder that starts instantly and updates in
  place, at the cost of being harder to move around.

Whatever lands in the bundle must survive being read-only and temporary:
only the bridge DLLs and the model are packaged (see eyetrack/paths.py -
anything the app *writes* goes to %APPDATA%\\Eyetracker instead).
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent          # injected by PyInstaller
ONEDIR = os.environ.get("EYE_TRACKER_ONEDIR") == "1"

# --- data files: read-only resources the app expects to find next to it
datas = [
    (str(ROOT / "bridge" / "NPClient.dll"), "bridge"),
    (str(ROOT / "bridge" / "NPClient64.dll"), "bridge"),
    (str(ROOT / "bridge" / "NPClient64.def"), "bridge"),
]
model = ROOT / "models" / "face_landmarker.task"
if model.exists():
    # Bundled so a fresh exe works offline; eyetrack.paths prefers it.
    datas.append((str(model), "models"))

# --- imports that static analysis cannot see
# MediaPipe builds its graph runners at runtime and imports pieces lazily.
mediapipe_bin, mediapipe_datas, mediapipe_hidden = collect_all("mediapipe")
datas += mediapipe_datas

hiddenimports = list(mediapipe_hidden) + [
    "mediapipe.tasks.python",
    "mediapipe.tasks.python.vision",
    "mediapipe.tasks.python.core.base_options",
    # Pulled in by mediapipe's drawing utils; needed at landmarker load.
    "matplotlib",
    "matplotlib.pyplot",
    # DirectShow device names (camera.device_name / --camera-name).
    "pygrabber",
    "pygrabber.dshow_graph",
    "comtypes",
    "comtypes.client",
]
for pkg in ("pygrabber", "comtypes"):
    _bin, _data, _hidden = collect_all(pkg)
    hiddenimports += list(_hidden)

# Nothing here uses these; dropping them keeps the bundle smaller.
# Do NOT exclude matplotlib: mediapipe.tasks.python.vision.drawing_utils
# imports it, so the face landmarker fails to load without it.
excludes = ["tkinter", "IPython", "pytest", "sphinx", "notebook"]

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=mediapipe_bin,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

if ONEDIR:
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name="Eyetracker",
        console=False,          # no terminal: the OpenCV overlay is the UI
        disable_windowed_traceback=False,
        upx=False,
    )
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Eyetracker")
else:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name="Eyetracker",
        console=False,          # no terminal: the OpenCV overlay is the UI
        disable_windowed_traceback=False,
        upx=False,
    )