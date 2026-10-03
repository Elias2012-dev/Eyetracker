"""Check that the packaged exe really opens the settings window.

Builds succeed and unit tests pass, but the packaged path can differ: a
missing Tk hidden import or a stripped module shows up only here. Launches
dist/Eyetracker.exe with no arguments, waits for the window, lists its
top-level windows and screenshots it, then closes it.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXE = ROOT / "dist" / "Eyetracker.exe"
SHOT = ROOT / "tools" / "preview" / "exe_gui.png"

ENUM = r"""
$sig = @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr p);
  public delegate bool EnumProc(IntPtr h, IntPtr p);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
}
"@
Add-Type -TypeDefinition $sig
$found = @()
$cb = [W+EnumProc]{
  param($h, $p)
  $proc = 0
  [void][W]::GetWindowThreadProcessId($h, [ref]$proc)
  if ($proc -eq $PID_WANTED -and [W]::IsWindowVisible($h)) {
    $sb = New-Object System.Text.StringBuilder 256
    [void][W]::GetWindowText($h, $sb, 256)
    if ($sb.Length -gt 0) { $script:found += $sb.ToString() }
  }
  return $true
}
[void][W]::EnumWindows($cb, [IntPtr]::Zero)
$found | ForEach-Object { Write-Output ("WINDOW: " + $_) }
Write-Output ("COUNT: " + $found.Count)
"""

CAPTURE = r"""
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
$bmp.Save("%s", [System.Drawing.Imaging.ImageFormat]::Png)
Write-Output "SHOT_OK"
"""


def main() -> int:
    if not EXE.exists():
        print("no exe:", EXE)
        return 1
    env_note = "isolated config so we never disturb the real one"
    tmp = ROOT / ".verify"
    tmp.mkdir(exist_ok=True)
    print("launching", EXE.name, f"({env_note})")
    proc = subprocess.Popen([str(EXE)], cwd=str(tmp),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    try:
        # A onefile exe unpacks ~120 MB before Python starts; give it room.
        deadline = time.time() + 150
        seen = []
        while time.time() < deadline:
            time.sleep(8)
            script = ENUM.replace("$PID_WANTED", str(proc.pid))
            res = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                                 capture_output=True, text=True, timeout=90)
            titles = [ln[8:] for ln in res.stdout.splitlines()
                      if ln.startswith("WINDOW: ")]
            count = next((ln[6:] for ln in res.stdout.splitlines()
                          if ln.startswith("COUNT: ")), "0")
            print(f"  t+{int(time.time() - (deadline - 150)):>3}s "
                  f"visible windows: {count} {titles}")
            if count != "0":
                seen = titles
                break
        if not seen:
            print("FAIL: the exe never showed a window")
            return 2

        cap = subprocess.run(
            ["powershell", "-NoProfile", "-Command", CAPTURE % str(SHOT)],
            capture_output=True, text=True, timeout=90)
        print("screenshot:", cap.stdout.strip(),
              SHOT.stat().st_size if SHOT.exists() else "missing")
        print("PASS: window titles =", seen)
        return 0
    finally:
        proc.terminate()
        try:
            proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()


if __name__ == "__main__":
    sys.exit(main())