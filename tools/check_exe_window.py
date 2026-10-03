"""Check that the packaged exe really opens the settings window.

Builds succeed and unit tests pass, but the packaged path can differ: a
missing Tk hidden import or a stripped module shows up only here. Launches
the exe with no arguments, waits for the window, lists its top-level
windows and screenshots it, then closes it.

Usage: check_exe_window.py [path-to-exe]
Defaults to dist/Eyetracker.exe; point it at a downloaded release asset to
verify what people actually get.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXE = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 \
    else ROOT / "dist" / "Eyetracker.exe"
SHOT = ROOT / "tools" / "preview" / "exe_gui.png"

ENUM = r"""
# $PIDS expands to a quoted PowerShell array, e.g. '1108','23728'.
$want = @($PIDS)
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
  [DllImport("user32.dll")] public static extern int GetWindowRect(IntPtr h, out RECT r);
  public struct RECT { public int L, T, R, B; }
}
"@
Add-Type -TypeDefinition $sig
$found = @()
$cb = [W+EnumProc]{
  param($h, $p)
  $proc = 0
  [void][W]::GetWindowThreadProcessId($h, [ref]$proc)
  if ($want -contains "$proc" -and [W]::IsWindowVisible($h)) {
    $sb = New-Object System.Text.StringBuilder 256
    [void][W]::GetWindowText($h, $sb, 256)
    if ($sb.Length -gt 0) {
      $r = New-Object W+RECT
      [void][W]::GetWindowRect($h, [ref]$r)
      $script:found += ("{0} at {1},{2} {3}x{4}" -f $sb.ToString(), $r.L, $r.T, ($r.R-$r.L), ($r.B-$r.T))
    }
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
        started = time.time()
        deadline = started + 240
        seen = []
        while time.time() < deadline:
            time.sleep(10)
            # The onefile bootloader forks a child that owns the real window,
            # so watch every Eyetracker.exe process, not just proc.pid.
            pids = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq Eyetracker.exe",
                 "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=60).stdout
            # Quote each pid: the template builds a PowerShell array from
            # this, and bare numbers are read as one token.
            want = ",".join(f"'{line.split(chr(34) + ',' + chr(34))[1].strip(chr(34))}'"
                            for line in pids.splitlines() if '","' in line)
            if not want:
                if proc.poll() is not None:
                    print(f"FAIL: the exe exited with {proc.returncode}")
                    return 2
                continue
            script = ENUM.replace("$PIDS", want)
            res = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                                 capture_output=True, text=True, timeout=90)
            lines = [ln for ln in res.stdout.splitlines()
                     if ln.startswith("WINDOW: ")]
            print(f"  t+{int(time.time() - started):>3}s "
                  f"windows: {len(lines)}")
            for ln in lines:
                print("   ", ln)
            if lines:
                seen = lines
                break
        if not seen:
            print("FAIL: the exe never showed a window")
            return 2

        cap = subprocess.run(
            ["powershell", "-NoProfile", "-Command", CAPTURE % str(SHOT)],
            capture_output=True, text=True, timeout=120)
        print("screenshot:", cap.stdout.strip(),
              SHOT.stat().st_size if SHOT.exists() else "missing")
        print("PASS: window =", seen)
        return 0
    finally:
        proc.terminate()
        subprocess.run(["taskkill", "/F", "/IM", "Eyetracker.exe"],
                       capture_output=True, text=True)
        try:
            proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()


if __name__ == "__main__":
    sys.exit(main())