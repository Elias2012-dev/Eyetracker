"""Command line entry point: ``python -m eyetrack``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import Config

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "eyetrack.json"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="eyetrack",
        description="Free webcam head-tracking for Minecraft, ETS2 and ATS.",
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
                   help="enable the built-in game link for ETS2/ATS (default on Windows)")
    p.add_argument("--no-game-link", dest="game_link", action="store_false", default=None,
                   help="disable the ETS2/ATS game link")
    p.add_argument("--no-auto-bridge", dest="auto_bridge", action="store_false", default=None,
                   help="do not auto-register the registry bridge on startup")
    p.add_argument("--freetrack", action="store_true", help=argparse.SUPPRESS)  # old alias
    p.add_argument("--opentrack", action="store_true",
                   help="enable opentrack UDP output (optional, for other games)")
    p.add_argument("--install-bridge", action="store_true",
                   help="register our NPClient DLL for ETS2/ATS and exit")
    p.add_argument("--uninstall-bridge", action="store_true",
                   help="restore registry values changed by --install-bridge and exit")
    p.add_argument("--calibrate", action="store_true", help="start in the calibration wizard")
    p.add_argument("--recenter-on-start", action="store_true",
                   help="re-centre once the first face frame arrives")
    p.add_argument("--list-cameras", action="store_true", help="probe cameras and exit")
    p.add_argument("--version", action="version", version=f"eyetrack {__version__}")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    if args.uninstall_bridge:
        from .bridge import uninstall
        uninstall()
        return 0
    if args.install_bridge:
        from .bridge import install
        install()
        return 0
    if args.list_cameras:
        from .app import list_cameras
        list_cameras()
        return 0

    cfg = Config.load(args.config)

    # CLI overrides (persisted so the next run keeps them).
    changed = False
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
