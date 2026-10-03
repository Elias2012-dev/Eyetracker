"""The tracking loop, with no UI in it.

Both front ends - the console/overlay runner and the settings window - need
exactly the same machine: open a camera, turn frames into a head pose, apply
calibration and smoothing, fan the result out to every output, and stop
cleanly. Keeping that in one place means the GUI can drive tracking from a
worker thread without a second, subtly different copy of the pipeline.

Nothing here imports a GUI toolkit or calls ``cv2.imshow``; the caller owns
the window and the keyboard. :meth:`TrackingSession.step` is the whole
contract: read a frame, do the work, return what happened.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2

from .calibrate import Calibration, CalibrationWizard
from .config import Config
from .filters import PoseFilter
from .outputs import build_outputs
from .pose import HeadPose, HeadPoseEstimator

# Short labels for the HUD's output chips. The full names are too long to
# fit three to a row, and "the stream is on" is the question being asked.
# How long a pose must hold still before a calibration step captures itself,
# and how much movement counts as "still". The pose is in proxy units where
# a full comfortable head turn is a couple of units, so 0.02 is well below
# breathing and well above tracker jitter.
CALM_HOLD_S = 1.0
CALM_MOVE_EPS = 0.02

CHIP_LABELS = {
    "game-link": "TrackIR",
    "udp": "Minecraft",
    "mouse": "Mouse",
}

ZERO_POSE = {"yaw": 0.0, "pitch": 0.0, "roll": 0.0, "x": 0.0, "y": 0.0, "z": 0.0}


def apply_gains(cfg: Config, pose: dict[str, float]) -> dict[str, float]:
    """Per-axis gains and sign flips, applied before smoothing."""
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


def output_chips(outputs, tracking: bool) -> tuple:
    """``(label, active)`` pairs for the HUD's output chips.

    An output is only "active" while a face is being tracked, because that
    is exactly when it is actually sending: a disabled-but-configured sink
    would otherwise look like it is working.
    """
    chips = []
    for out in outputs:
        label = CHIP_LABELS.get(out.name, out.name)
        if out.name == "mouse":
            active = tracking and getattr(out, "active", True)
        else:
            active = tracking
        chips.append((label, bool(active)))
    return tuple(chips)


def open_camera(cfg: Config):
    """Open the configured camera, by name when one is saved."""
    from .cameras import resolve_index

    index = resolve_index(cfg.camera.device_name, cfg.camera.index)
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        raise SystemExit(f"Could not open camera index {index} "
                         f"(name filter: {cfg.camera.device_name or '-'}). "
                         "Pick another in Settings, or run --pick-camera.")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.camera.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.camera.height)
    cap.set(cv2.CAP_PROP_FPS, cfg.camera.fps)
    try:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    except cv2.error:
        pass
    return cap


@dataclass
class FrameResult:
    """Everything one :meth:`TrackingSession.step` produced."""

    ok: bool = False                     # a frame was read from the camera
    frame: object = None                 # the raw BGR frame, or None
    pose: HeadPose = field(default_factory=HeadPose)
    values: dict = field(default_factory=lambda: dict(ZERO_POSE))
    tracking: bool = False
    fps: float = 0.0
    message: str = ""                    # one-off text for the HUD/status bar


class TrackingSession:
    """Camera -> pose -> calibration -> filter -> outputs, started and stopped.

    Safe to call :meth:`start`/:meth:`stop` repeatedly; that is what the
    GUI's Start/Stop button does. Not thread-safe on its own - the GUI owns
    one and confines it to its worker thread.
    """

    def __init__(self, cfg: Config, config_path: Path, *, start_wizard: bool = False,
                 recenter_on_start: bool = False) -> None:
        self.cfg = cfg
        self.config_path = Path(config_path)
        self.recenter_on_start = recenter_on_start

        self.estimator: HeadPoseEstimator | None = None
        self.cap = None
        self.outputs: list = []
        self.calib = Calibration.load()
        self.calib.reference_distance_cm = cfg.pose.reference_distance_cm
        self.filt = PoseFilter(cfg.filter)
        self.wizard: CalibrationWizard | None = (
            CalibrationWizard(cfg.pose.yaw_range, cfg.pose.pitch_range,
                             cfg.pose.reference_distance_cm)
            if start_wizard else None)
        self.first_run = start_wizard

        self.running = False
        self.last_raw: HeadPose | None = None
        self.values = dict(ZERO_POSE)
        self.fps_ema = 0.0
        self._t_last = 0.0
        self._auto_recentered = False
        self._bridge_ready = False
        # (yaw, pitch, t) of the pose we are waiting to become "settled".
        self._calib_ref: tuple[float, float, float] | None = None
        self.status = ""

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Open the camera, build the estimator and start every output."""
        if self.running:
            return
        self.estimator = HeadPoseEstimator()
        self.cap = open_camera(self.cfg)
        self.calib = Calibration.load()
        self.calib.reference_distance_cm = self.cfg.pose.reference_distance_cm
        self.filt = PoseFilter(self.cfg.filter)
        self.filt.reset()

        if self.cfg.game_link.enabled and not self._bridge_ready:
            from .bridge import ensure as ensure_bridge
            ensure_bridge(auto=self.cfg.game_link.auto_bridge)
            self._bridge_ready = True

        self.outputs = build_outputs(self.cfg)
        for out in self.outputs:
            out.start()
            print(f"[eyetrack] output enabled: {out.name}")

        self._t_last = time.monotonic()
        self._auto_recentered = False
        self.running = True

    def stop(self) -> None:
        """Release the camera and close every output. Idempotent."""
        self.running = False
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None
        for out in self.outputs:
            try:
                out.close()
            except Exception:
                pass
        self.outputs = []
        if self.estimator is not None:
            try:
                self.estimator.close()
            except Exception:
                pass
            self.estimator = None
        try:
            self.calib.save()
        except OSError:
            pass

    def save_calibration(self) -> None:
        self.calib.save()
        self.filt.reset()

    def save_config(self) -> None:
        try:
            self.cfg.save(self.config_path)
        except OSError as exc:
            print(f"[eyetrack] warning: could not save config: {exc}")

    # ------------------------------------------------------------------
    # the loop, one frame at a time
    # ------------------------------------------------------------------
    def step(self) -> FrameResult:
        out = FrameResult()
        if not self.running or self.cap is None:
            return out

        t = time.monotonic()
        dt = t - self._t_last
        self._t_last = t
        if dt > 0:
            self.fps_ema = (self.fps_ema * 0.9 + (1.0 / dt) * 0.1
                            if self.fps_ema else 1.0 / dt)

        ok, frame = self.cap.read()
        out.ok = bool(ok) and frame is not None
        if not out.ok:
            self._fan_out(dict(ZERO_POSE), False, t)
            return out

        pose = self.estimator.process(frame, int(t * 1000)) if self.estimator else HeadPose()
        out.pose = pose
        out.fps = self.fps_ema
        out.frame = frame

        if pose.detected:
            self.last_raw = pose
            if not self.calib.valid:
                # First run: assume the user starts facing the screen.
                self.calib.center_from(pose)
                self.calib.save()
                out.message = "auto-centred on first frame"
            elif self.recenter_on_start and not self._auto_recentered:
                self.calib.center_from(pose)
                self.filt.reset()
                self._auto_recentered = True
                out.message = "re-centred"
            values = apply_gains(self.cfg, self.calib.apply(pose))
            self.values = self.filt.apply(values, t)
            self._autostep_calibration(t)

        out.values = self.values
        out.tracking = bool(pose.detected)
        self._fan_out(self.values, out.tracking, t)
        return out

    def _fan_out(self, values: dict, tracking: bool, t: float) -> None:
        for sink in self.outputs:
            sink.send(values, tracking, t)

    def _autostep_calibration(self, now: float) -> bool:
        """Capture a calibration step by holding still.

        A five-pose wizard that needs five SPACE presses is a wizard people
        never finish, especially the first time. Holding each pose for a
        moment is unambiguous - the face has stopped moving - so the step
        takes itself and the keys stay optional.
        """
        wizard = self.wizard
        pose = self.last_raw
        if wizard is None or wizard.done or wizard.cancelled or pose is None:
            return False

        yaw, pitch = pose.proxy_yaw, pose.proxy_pitch
        if self._calib_ref is None:
            self._calib_ref = (yaw, pitch, now)
            return False

        ref_yaw, ref_pitch, ref_at = self._calib_ref
        if abs(yaw - ref_yaw) + abs(pitch - ref_pitch) > CALM_MOVE_EPS:
            self._calib_ref = (yaw, pitch, now)      # still moving; restart
            return False
        if now - ref_at < CALM_HOLD_S:
            return False
        self._calib_ref = None
        return self.submit_calibration()

    def chips(self, tracking: bool) -> tuple:
        return output_chips(self.outputs, tracking)

    # ------------------------------------------------------------------
    # commands the UI can issue
    # ------------------------------------------------------------------
    def recentre(self) -> bool:
        if self.last_raw is None:
            return False
        self.calib.center_from(self.last_raw)
        self.calib.save()
        self.filt.reset()
        return True

    def begin_calibration(self) -> CalibrationWizard:
        self.wizard = CalibrationWizard(self.cfg.pose.yaw_range,
                                        self.cfg.pose.pitch_range,
                                        self.cfg.pose.reference_distance_cm)
        self._calib_ref = None
        return self.wizard

    def cancel_calibration(self) -> None:
        if self.wizard is not None:
            self.wizard.cancel()
        self._calib_ref = None

    def submit_calibration(self) -> bool:
        """Capture the current pose; returns True once it produced a result."""
        if self.wizard is None or self.last_raw is None:
            return False
        self.wizard.submit(self.last_raw)
        if not self.wizard.done:
            return False
        result = self.wizard.result(self.calib)
        if result is None:
            print("[eyetrack] calibration incomplete - press C to retry")
            return False
        self.calib = result
        self.calib.save()
        self.filt.reset()
        self.save_config()
        print("[eyetrack] calibration saved")
        return True

    def describe_outputs(self) -> str:
        return ", ".join(o.name for o in self.outputs) or "none"