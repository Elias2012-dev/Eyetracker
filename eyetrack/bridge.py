"""Registry bridge for ETS2 / ATS - points the games at OUR NPClient DLL.

SCS games load a head-tracking client DLL by looking up these registry keys
(under HKEY_CURRENT_USER - no admin rights needed):

* ``Software\\NaturalPoint\\NATURALPOINT\\NPClient Location`` -> ``Path``
* ``Software\\Freetrack\\FreetrackClient`` -> ``Path`` (legacy lookup some
  clients still probe; we write it for compatibility, nothing FreeTrack is
  installed or used)

Both point at ``bridge/`` containing ``NPClient.dll`` / ``NPClient64.dll`` -
our own 6.5 KB bridge compiled from ``bridge/src/ebt_npclient.c``.  The DLL
reads the ``EBT_GameLink_v1`` shared memory the tracker writes.

Only ever writes to HKCU and remembers previous values so
``--uninstall-bridge`` can restore them.  On startup with the game link
enabled, :func:`ensure` registers the bridge automatically (backing up
whatever was there).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from .paths import FROZEN, backup_path, bridge_dir, data_dir

# Read-only: shipped with the app (repo root, or the frozen bundle).
BRIDGE_DIR = bridge_dir()
# Writable: lives next to the config, not inside the bundle.
BACKUP_FILE = backup_path()

KEYS = [
    (r"Software\NaturalPoint\NATURALPOINT\NPClient Location", "Path"),
    (r"Software\Freetrack\FreetrackClient", "Path"),
]

REQUIRED_DLLS = ["NPClient.dll", "NPClient64.dll"]


def _missing_dlls() -> list[str]:
    return [n for n in REQUIRED_DLLS if not (BRIDGE_DIR / n).exists()]


def game_dir() -> Path:
    """The folder whose path we hand to the games.

    From source that is the repository's ``bridge/``. A one-file build is
    different: it unpacks into ``%TEMP%\\_MEIxxxxx`` and that directory is
    deleted when the tracker quits, so registering it would leave the games
    pointing at nothing after the first exit. Frozen builds therefore copy
    the two DLLs next to the config first - a stable, writable location
    that outlives the process.
    """
    if not FROZEN:
        return BRIDGE_DIR
    target = data_dir() / "bridge"
    try:
        target.mkdir(parents=True, exist_ok=True)
        for name in REQUIRED_DLLS:
            src = BRIDGE_DIR / name
            dst = target / name
            if src.exists() and (not dst.exists()
                                 or dst.stat().st_size != src.stat().st_size):
                shutil.copy2(src, dst)
    except OSError as exc:
        print(f"[game-link] could not stage the DLLs in {target}: {exc} - "
              "falling back to the bundled folder (the games will only see "
              "tracking while the tracker is running)")
        return BRIDGE_DIR
    return target


def _current_path(subkey: str, value_name: str) -> str | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_READ) as k:
            value, _ = winreg.QueryValueEx(k, value_name)
            return str(value)
    except OSError:
        return None


def install() -> None:
    if sys.platform != "win32":
        raise SystemExit("The game-link bridge can only be installed on Windows.")
    missing = _missing_dlls()
    if missing:
        raise SystemExit(
            "Bridge DLLs missing from bridge/: " + ", ".join(missing) +
            "\nBuild them with bridge\\build.bat (TinyCC is bundled in bridge\\tools)."
        )

    import winreg

    target = str(game_dir())
    backup: dict[str, str | None] = {}
    if BACKUP_FILE.exists():
        try:
            backup = json.loads(BACKUP_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            backup = {}

    for subkey, value_name in KEYS:
        key = f"{subkey}\\{value_name}"
        prev = _current_path(subkey, value_name)
        if key not in backup:  # keep the very first original owner
            backup[key] = prev
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, subkey) as k:
            winreg.SetValueEx(k, value_name, 0, winreg.REG_SZ, target)
        shown = prev if prev else "(none)"
        print(f"[game-link] HKCU\\{key} -> {target}   (was: {shown})")

    BACKUP_FILE.write_text(json.dumps(backup, indent=2), encoding="utf-8")
    print("[game-link] bridge registered - ETS2/ATS will pick it up on next launch.")


def uninstall() -> None:
    if sys.platform != "win32":
        raise SystemExit("Windows only.")
    import winreg

    if not BACKUP_FILE.exists():
        raise SystemExit(f"No backup found - nothing to restore ({BACKUP_FILE.name} missing).")
    backup = json.loads(BACKUP_FILE.read_text(encoding="utf-8"))

    for subkey, value_name in KEYS:
        key = f"{subkey}\\{value_name}"
        prev = backup.get(key, None)
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, subkey) as k:
                if prev is None:
                    try:
                        winreg.DeleteValue(k, value_name)
                        print(f"[game-link] removed {key}")
                    except OSError:
                        pass
                else:
                    winreg.SetValueEx(k, value_name, 0, reg_sz(), prev)
                    print(f"[game-link] restored {key} -> {prev}")
        except OSError as exc:
            print(f"[game-link] could not touch {key}: {exc}")

    BACKUP_FILE.unlink(missing_ok=True)


def reg_sz() -> int:
    import winreg
    return winreg.REG_SZ


def ensure(auto: bool = True) -> None:
    """Called at startup while the game link is enabled."""
    if sys.platform != "win32":
        return
    missing = _missing_dlls()
    if missing:
        print("[game-link] WARNING: bridge DLLs missing ("
              + ", ".join(missing) + ") - run bridge\\build.bat. "
              "The tracker will still stream; games just cannot see it yet.")
        return
    target = str(game_dir())
    owned = all(_current_path(sk, vn) == target for sk, vn in KEYS)
    if owned:
        return
    if auto:
        print(f"[game-link] registering our NPClient bridge for TrackIR games -> {target}")
        install()
    else:
        print("[game-link] bridge not registered - run once: run.bat --install-bridge")
