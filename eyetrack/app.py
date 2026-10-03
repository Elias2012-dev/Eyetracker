"""Main application loop: camera -> pose -> calibrate -> filter -> outputs."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import cv2

from .autodetect import CameraProbe
from .calibrate import Calibration, CalibrationWizard
from .config import Config
from .filters import PoseFilter
from .overlay import Overlay
from .outputs import build_outputs
from .pose import HeadPose, HeadPoseEstimator

HELP_STATUS = "keys:  C calibrate   R recenter   Q quit"
SETUP_STATUS = ("first run - C skips setup and starts tracking anyway"
                "   SPACE captures")


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


def open_camera(cfg: Config):
    from .cameras import resolve_index

    index = resolve_index(cfg.camera.device_name, cfg.camera.index)
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        raise SystemExit(f"Could not open camera index {index} "
                         f"(name filter: {cfg.camera.device_name or '-'}). "
                         "Try --pick-camera to choose from a list, "
                         "--list-cameras, --camera N or --camera-name NAME.")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.camera.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.camera.height)
    cap.set(cv2.CAP_PROP_FPS, cfg.camera.fps)
    try:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    except cv2.error:
        pass
    return cap


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


def _apply_gains(cfg: Config, pose: dict[str, float]) -> dict[str, float]:
    p = cfg.pose

    def s(v: float, gain: float, invert: bool) -> float:
        return -v * gain if invert else v * gain

    return {
        "yaw": s(pose["yaw"], p.yaw_gain, p.invert_yaw),
        "pitch": s(pose["pitch"], p.pitch_gain, p.invert_pitch),
        "roll": s(pose["roll"], p.roll_gain, p.invert_roll),
        "x": s(pose["x"], p.x_gain, p.invert_x),
        "y": s(pose["y"], p.y_gain, p.invert_y),
        "z": s(pose["z"], p.z_gain, p.invert_z),
    }


def _auto_detect(cfg: Config, config_path: Path, estimator) -> CameraProbe | None:
    """Find a camera that actually shows a face, and remember it.

    Deliberately conservative about what counts: a virtual camera that
    opens but returns nothing usable would otherwise win, and the user
    would land in the wizard with no image to calibrate against.
    """
    from .autodetect import detect_camera

    def detect(frame):
        return estimator.process(frame, int(time.monotonic() * 1000)).detected

    found = detect_camera(
        face_detector=detect,
        prefer_name=cfg.camera.device_name,
        width=cfg.camera.width, height=cfg.camera.height, fps=cfg.camera.fps,
        on_progress=lambda d: print(f"[setup] trying camera {d}"))
    if found is None:
        print("[setup] no camera found - open the app's camera settings "
              "or run --pick-camera to choose one manually")
        return None

    cfg.camera.index = found.index
    if found.name:
        cfg.camera.device_name = found.name
    kind = "a face" if found.face else "frames, but no face yet"
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
    estimator = HeadPoseEstimator()
    if first_run:
        # Before the camera is opened: picking the right index first avoids
        # failing on a camera that was never going to work.
        _auto_detect(cfg, config_path, estimator)
        # Always calibrate on a fresh install - the default pose mapping is
        # a guess, and one wrong guess means every game looks wrong.
        start_wizard = True
    elif pick_camera:
        _prompt_for_camera(cfg, config_path)
    print("[eyetrack] starting camera", cfg.camera.index)
    cap = open_camera(cfg)

    calib = Calibration.load()
    calib.reference_distance_cm = cfg.pose.reference_distance_cm
    filt = PoseFilter(cfg.filter)

    if cfg.game_link.enabled:
        from .bridge import ensure as ensure_bridge
        ensure_bridge(auto=cfg.game_link.auto_bridge)

    outputs = build_outputs(cfg)
    for out in outputs:
        out.start()
        print(f"[eyetrack] output enabled: {out.name}")

    mouse_out = next((o for o in outputs if o.name == "mouse"), None)

    overlay = Overlay(cfg.overlay.window, cfg.overlay.show_mesh,
                      top_most=cfg.overlay.top_most, compact=cfg.overlay.compact,
                      yaw_range=cfg.pose.yaw_range,
                      pitch_range=cfg.pose.pitch_range,
                      roll_range=cfg.pose.roll_range) if cfg.overlay.enabled else None
    wizard = CalibrationWizard(cfg.pose.yaw_range, cfg.pose.pitch_range,
                               cfg.pose.reference_distance_cm) if start_wizard else None
    if wizard is not None:
        status = SETUP_STATUS if first_run else "calibration started"

    last_raw: HeadPose | None = None
    last_out = {"yaw": 0.0, "pitch": 0.0, "roll": 0.0, "x": 0.0, "y": 0.0, "z": 0.0}
    fps_ema = 0.0
    t_last = time.monotonic()
    auto_recentered = False
    status = HELP_STATUS

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                if overlay is None:
                    print("[eyetrack] camera read failed, retrying...")
                    time.sleep(0.2)
                    continue
                frame = None  # will be handled below

            t = time.monotonic()
            dt = t - t_last
            t_last = t
            if dt > 0:
                fps_ema = fps_ema * 0.9 + (1.0 / dt) * 0.1 if fps_ema else 1.0 / dt

            pose = HeadPose()
            if frame is not None:
                pose = estimator.process(frame, int(t * 1000))

            if pose.detected:
                last_raw = pose
                if not calib.valid:
                    # First run: assume the user starts facing the screen.
                    calib.center_from(pose)
                    calib.save()
                    status = "auto-centred on first frame"
                elif recenter_on_start and not auto_recentered:
                    calib.center_from(pose)
                    filt.reset()
                    auto_recentered = True
                    status = "re-centred"
                last_out = _apply_gains(cfg, calib.apply(pose))
                last_out = filt.apply(last_out, t)

            tracking = bool(pose.detected)
            for out in outputs:
                out.send(last_out, tracking, t)

            if overlay is not None and frame is not None:
                status_line = status
                if mouse_out is not None:
                    status_line += "   " + mouse_out.status_text()
                key = overlay.draw(frame, pose, last_out, tracking, fps_ema,
                                   cfg.camera.mirror, wizard, status_line)
                status = HELP_STATUS
                # On a fresh install ESC means "skip setup and just track":
                # quitting outright leaves someone who only wanted to try it
                # with no camera chosen and no calibration at all.
                if key == 27 and first_run and wizard is not None:
                    wizard.cancel()
                    first_run = False
                    status = "setup skipped - press C any time to calibrate"
                    print("[setup] skipped calibration; tracking anyway "
                          "(press C to redo)")
                    continue
                if overlay.window_closed() or key in (ord("q"), 27):
                    break
                if key == ord("c"):
                    wizard = CalibrationWizard(cfg.pose.yaw_range, cfg.pose.pitch_range,
                                               cfg.pose.reference_distance_cm)
                    status = SETUP_STATUS if first_run else "calibration started"
                elif key == ord("r"):
                    if last_raw is not None:
                        calib.center_from(last_raw)
                        calib.save()
                        filt.reset()
                        status = "re-centred"
                elif key == ord(" ") and wizard is not None:
                    if last_raw is not None:
                        wizard.submit(last_raw)
                    if wizard.done:
                        result = wizard.result(calib)
                        if result is not None:
                            calib = result
                            calib.save()
                            filt.reset()
                            try:
                                cfg.save(config_path)
                            except OSError:
                                pass
                            print("[eyetrack] calibration saved")
                        else:
                            print("[eyetrack] calibration incomplete - press C to retry")
                elif overlay.window_closed():
                    break
    except KeyboardInterrupt:
        print("\n[eyetrack] interrupted")
    finally:
        cap.release()
        estimator.close()
        for out in outputs:
            out.close()
        if overlay is not None:
            overlay.close()
        calib.save()
    print("[eyetrack] stopped")
    return 0
