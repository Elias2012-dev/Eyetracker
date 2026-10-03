"""Console/overlay entry point: camera -> pose -> calibrate -> filter -> outputs.

The pipeline itself lives in :mod:`eyetrack.session`; this module is the
front end that owns the HUD window and the keyboard, and the one-shot
commands (``--list-cameras``, ``--paths``, ``--pick-camera``). The settings
window in :mod:`eyetrack.gui` drives the very same session, so there is
only ever one implementation of the tracking machine.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import cv2

from .autodetect import CameraProbe
from .config import Config
from .overlay import Overlay
from .pose import HeadPoseEstimator
from .session import TrackingSession

SETUP_STATUS = "first run - C skips setup and starts tracking anyway"


def _prompt_for_camera(cfg: Config, config_path: Path) -> None:
    """Let the user pick from every camera Windows reports, and remember it.

    The choice is saved as a *name* rather than an index: indices are not
    stable across reboots or USB reordering, but the device name is, so
    this keeps working when the same webcam comes back at a different
    index.
    """
    from .cameras import choose_interactively

    dev = choose_interactively()
    if dev is None:
        print("[camera] keeping the camera from the config "
              f"(index {cfg.camera.index}"
              f"{', name ' + cfg.camera.device_name if cfg.camera.device_name else ''}).")
        return
    cfg.camera.index = dev.index
    cfg.camera.device_name = dev.name
    print(f"[camera] selected '{dev.name}' (index {dev.index}) - saving it.")
    try:
        cfg.save(config_path)
    except OSError as exc:
        print(f"[eyetrack] warning: could not save config: {exc}")


def list_cameras(max_index: int = 10) -> None:
    from .cameras import list_devices

    devices = list_devices()
    if devices:
        print("Camera devices (use the name with --camera-name or camera.device_name):")
        for d in devices:
            print(f"  {d}")
    elif sys.platform == "win32":
        print("(device names unavailable - pygrabber not installed; "
              "run: .venv\\Scripts\\python -m pip install pygrabber)")

    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            found.append((i, w, h))
            cap.release()
    if not found:
        print("No cameras found.")
    print("OpenCV index probe:")
    for i, w, h in found:
        print(f"  camera {i}: {w}x{h}")


def _auto_detect(cfg: Config, config_path: Path, estimator) -> CameraProbe | None:
    """Find a camera that actually shows a face, and remember it.

    Deliberately conservative about what counts: a virtual camera that
    opens but returns nothing usable would otherwise win, and the user
    would land in the wizard with no image to calibrate against.
    """
    from .autodetect import detect_camera
    from .splash import Splash

    def detect(frame):
        return estimator.process(frame, int(time.monotonic() * 1000)).detected

    tried: list[str] = []

    def progress(dev) -> None:
        tried.append(getattr(dev, "name", "") or f"camera {getattr(dev, 'index', '?')}")
        splash.update(
            "Looking for your camera",
            f"testing {tried[-1]}...",
            note=f"{len(tried)} device{'' if len(tried) == 1 else 's'} tried"
                 f"{' - this one is being given a few seconds' if len(tried) == 1 else ''}")
        print(f"[setup] trying camera {tried[-1]}")

    with Splash() as splash:
        found = detect_camera(
            face_detector=detect,
            prefer_name=cfg.camera.device_name,
            width=cfg.camera.width, height=cfg.camera.height, fps=cfg.camera.fps,
            on_progress=progress)

    if found is None:
        splash.finish("No camera found",
                      "connect a webcam, or run the app again with --pick-camera")
        print("[setup] no camera found - open the app's camera settings "
              "or run --pick-camera to choose one manually")
        return None

    cfg.camera.index = found.index
    if found.name:
        cfg.camera.device_name = found.name
    kind = "a face" if found.face else "frames, but no face yet"
    splash.finish("Camera found", f"{found.name or found.index} - {kind}")
    print(f"[setup] using '{found.name or found.index}' - {kind} "
          f"({found.seconds:.1f}s)")
    try:
        cfg.save(config_path)
    except OSError as exc:
        print(f"[eyetrack] warning: could not save config: {exc}")
    return found


def run(cfg: Config, *, config_path: Path, start_wizard: bool = False,
        recenter_on_start: bool = False, pick_camera: bool = False,
        first_run: bool = False) -> int:
    """Run the tracker with the HUD until the user quits.

    This is the front end used when the app is launched without a display
    to put a settings panel in - from a terminal, a shortcut, or a script.
    """
    estimator = None
    if first_run:
        # Before the camera is opened: picking the right index first avoids
        # failing on a camera that was never going to work.
        estimator = HeadPoseEstimator()
        _auto_detect(cfg, config_path, estimator)
        estimator.close()
        estimator = None
        # Always calibrate on a fresh install - the default pose mapping is
        # a guess, and one wrong guess means every game looks wrong.
        start_wizard = True
    elif pick_camera:
        _prompt_for_camera(cfg, config_path)

    print("[eyetrack] starting camera", cfg.camera.index)
    session = TrackingSession(cfg, config_path, start_wizard=start_wizard,
                              recenter_on_start=recenter_on_start)
    session.start()

    overlay = Overlay(cfg.overlay.window, cfg.overlay.show_mesh,
                      top_most=cfg.overlay.top_most, compact=cfg.overlay.compact,
                      yaw_range=cfg.pose.yaw_range,
                      pitch_range=cfg.pose.pitch_range,
                      roll_range=cfg.pose.roll_range) if cfg.overlay.enabled else None

    status = SETUP_STATUS if (first_run and session.wizard is not None) else ""
    skipped_setup = False

    try:
        while True:
            res = session.step()

            if res.message:
                status = res.message

            if not res.ok and overlay is None:
                print("[eyetrack] camera read failed, retrying...")
                time.sleep(0.2)
                continue

            if overlay is not None and res.frame is not None:
                key = overlay.draw(res.frame, res.pose, res.values, res.tracking,
                                   res.fps, cfg.camera.mirror, session.wizard,
                                   status, session.chips(res.tracking))
                status = ""
                # On a fresh install ESC means "skip setup and just track":
                # quitting outright leaves someone who only wanted to try it
                # with no camera chosen and no calibration at all.
                if key == 27 and first_run and not skipped_setup \
                        and session.wizard is not None:
                    session.cancel_calibration()
                    session.wizard = None
                    skipped_setup = True
                    status = "setup skipped - press C any time to calibrate"
                    print("[setup] skipped calibration; tracking anyway "
                          "(press C to redo)")
                    continue
                if overlay.window_closed() or key in (ord("q"), 27):
                    break
                if key == ord("c"):
                    session.begin_calibration()
                    status = SETUP_STATUS if first_run else "calibration started"
                elif key == ord("r"):
                    status = "re-centred" if session.recentre() else "no face yet"
                elif key == ord("h"):
                    status = ("help hidden - press H to show it again"
                              if overlay.toggle_help() else "")
                elif key == ord("m"):
                    status = "face mesh off" if not overlay.toggle_mesh() else ""
                elif key == ord("f"):
                    cfg.overlay.compact = overlay.set_compact(not overlay.compact)
                    cfg.overlay.show_mesh = overlay.show_mesh
                    status = ("compact HUD - press F for the camera view"
                              if overlay.compact else "camera view")
                    session.save_config()
                elif key == ord(" ") and session.wizard is not None:
                    session.submit_calibration()
    except KeyboardInterrupt:
        print("\n[eyetrack] interrupted")
    finally:
        session.stop()
        if overlay is not None:
            overlay.close()
    print("[eyetrack] stopped")
    return 0