"""Command line entry point: ``python -m eyetrack``."""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
from pathlib import Path

from . import __version__
from .config import Config
from .console import attach_log, show_message
from .paths import config_path

DEFAULT_CONFIG = config_path()


def _report(title: str, fn) -> int:
    """Run a short command and mirror its output.

    In a console build the text simply prints. The windowed exe has no
    console, so the same text is shown as a message box (and written to
    the log).
    """
    buf = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(buf):
        try:
            fn()
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            buf.write(f"\n{exc}\n")
    text = buf.getvalue().strip()
    if text:
        print(text)
    show_message(title, text)
    return code


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="eyetrack",
        description="Free webcam head-tracking for Minecraft, TrackIR games "
                    "and mouse-look games.",
    )
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                   help=f"config file (default: {DEFAULT_CONFIG.name})")
    p.add_argument("--camera", type=int, help="camera index")
    p.add_argument("--camera-name", type=str, metavar="NAME",
                   help="select camera by device name substring "
                        "(e.g. \"Iriun\", \"DroidCam\", \"iPhone\", \"OBS\")")
    p.add_argument("--width", type=int, help="capture width")
    p.add_argument("--height", type=int, help="capture height")
    p.add_argument("--fps", type=int, help="capture fps")
    p.add_argument("--no-overlay", action="store_true", help="disable the preview window")
    p.add_argument("--udp-port", type=int, help="Minecraft UDP output port")
    p.add_argument("--udp-host", help="Minecraft UDP output host")
    p.add_argument("--no-udp", action="store_true", help="disable the UDP JSON output")
    p.add_argument("--game-link", dest="game_link", action="store_true", default=None,
                   help="enable the built-in game link for TrackIR games "
                        "(ETS2/ATS/DCS/MSFS/... - default on Windows)")
    p.add_argument("--no-game-link", dest="game_link", action="store_false", default=None,
                   help="disable the TrackIR game link")
    p.add_argument("--mouse", dest="mouse", action="store_true", default=None,
                   help="move the mouse with your head - works with any game "
                        "that has no TrackIR support (F9 arms/disarms)")
    p.add_argument("--no-mouse", dest="mouse", action="store_false", default=None,
                   help="disable the mouse-emulation output")
    p.add_argument("--game", metavar="KEY", type=str,
                   help="apply a game preset and print its setup notes "
                        "(see --list-games)")
    p.add_argument("--list-games", action="store_true",
                   help="list the built-in game support and exit")
    p.add_argument("--no-auto-bridge", dest="auto_bridge", action="store_false", default=None,
                   help="do not auto-register the registry bridge on startup")
    p.add_argument("--freetrack", action="store_true", help=argparse.SUPPRESS)  # old alias
    p.add_argument("--opentrack", action="store_true",
                   help="escape hatch: also stream to an existing opentrack "
                        "install - no game in the built-in list needs it")
    p.add_argument("--install-bridge", action="store_true",
                   help="register our NPClient DLL for ETS2/ATS and exit")
    p.add_argument("--uninstall-bridge", action="store_true",
                   help="restore registry values changed by --install-bridge and exit")
    p.add_argument("--calibrate", action="store_true", help="start in the calibration wizard")
    p.add_argument("--recenter-on-start", action="store_true",
                   help="re-centre once the first face frame arrives")
    p.add_argument("--list-cameras", action="store_true", help="probe cameras and exit")
    p.add_argument("--paths", action="store_true",
                   help="show where config, model, DLLs and the log live, and exit")
    p.add_argument("--version", action="version", version=f"eyetrack {__version__}")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    attach_log()  # no-op unless this is the windowed exe
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    if args.paths:
        from .paths import describe
        return _report("Eyetracker - paths", lambda: print(describe()))
    if args.uninstall_bridge:
        from .bridge import uninstall
        return _report("Eyetracker - bridge removed", uninstall)
    if args.install_bridge:
        from .bridge import install
        return _report("Eyetracker - bridge installed", install)
    if args.list_cameras:
        from .app import list_cameras
        return _report("Eyetracker - cameras", list_cameras)
    if args.list_games:
        from .presets import print_catalog
        return _report("Eyetracker - game support", print_catalog)

    cfg = Config.load(args.config)

    # CLI overrides (persisted so the next run keeps them).
    changed = False
    if args.game is not None:
        from .presets import apply_preset
        try:
            preset = apply_preset(args.game, cfg)
        except SystemExit as exc:
            print(str(exc))
            show_message("Eyetracker - unknown game", str(exc))
            return 2
        print(f"[eyetrack] game preset: {preset.title}")
        for note in preset.notes:
            print(f"  - {note}")
    if args.camera is not None:
        cfg.camera.index, changed = args.camera, True
    if args.camera_name is not None:
        cfg.camera.device_name, changed = args.camera_name, True
    if args.width is not None:
        cfg.camera.width, changed = args.width, True
    if args.height is not None:
        cfg.camera.height, changed = args.height, True
    if args.fps is not None:
        cfg.camera.fps, changed = args.fps, True
    if args.no_overlay:
        cfg.overlay.enabled, changed = False, True
    if args.udp_port is not None:
        cfg.udp_json.port, changed = args.udp_port, True
    if args.udp_host is not None:
        cfg.udp_json.host, changed = args.udp_host, True
    if args.no_udp:
        cfg.udp_json.enabled, changed = False, True
    if args.game_link is not None:
        cfg.game_link.enabled, changed = args.game_link, True
    if args.mouse is not None:
        cfg.mouse.enabled, changed = args.mouse, True
    if args.auto_bridge is not None:
        cfg.game_link.auto_bridge, changed = args.auto_bridge, True
    if args.freetrack:
        print("[eyetrack] note: --freetrack is now --game-link (same built-in output)")
        cfg.game_link.enabled, changed = True, True
    if args.opentrack:
        cfg.opentrack_udp.enabled, changed = True, True

    if changed or not args.config.exists():
        try:
            cfg.save(args.config)
        except OSError as exc:
            print(f"[eyetrack] warning: could not save config: {exc}")

    from .app import run
    return run(cfg, config_path=args.config,
               start_wizard=args.calibrate,
               recenter_on_start=args.recenter_on_start)


if __name__ == "__main__":
    raise SystemExit(main())
