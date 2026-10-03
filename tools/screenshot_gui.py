"""Screenshot the settings window so the layout can be reviewed.

Launches the GUI for real, waits for it to settle, grabs the screen, then
closes it. Used by hand, not by CI (CI has no desktop session).
"""
from __future__ import annotations

import pathlib
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
SHOT = ROOT / "tools" / "preview" / "gui.png"
PS = r"""
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
$bmp.Save("%s", [System.Drawing.Imaging.ImageFormat]::Png)
Write-Output ("saved {0}x{1}" -f $b.Width, $b.Height)
"""

SHOT.parent.mkdir(parents=True, exist_ok=True)
proc = subprocess.Popen(
    [str(ROOT / ".venv/Scripts/python.exe"), "-m", "eyetrack", "--no-first-run"],
    cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
try:
    time.sleep(14)
    win = subprocess.run(
        ["powershell", "-NoProfile", "-Command", PS % str(SHOT).replace("\\", "\\")],
        capture_output=True, text=True, timeout=90)
    print(win.stdout.strip() or win.stderr.strip())
    print("exists:", SHOT.exists(), SHOT.stat().st_size if SHOT.exists() else 0)
finally:
    proc.terminate()
    try:
        out, _ = proc.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
    tail = [ln for ln in (out or "").splitlines()
            if "camera" in ln or "error" in ln.lower() or "Traceback" in ln]
    print("--- app log (filtered) ---")
    print("\n".join(tail[-12:]) or "(no matching lines)")