"""Centre + range calibration.

The tracker works without calibration (heuristic scales), but a 30-second
wizard makes the mapping accurate: it captures the neutral pose, comfortable
left/right/up/down extents, and derives degrees-per-proxy-unit scales.

Persistence is a small JSON file (``calibration.json``).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import calibration_path
from .pose import DEFAULT_PITCH_SCALE, DEFAULT_YAW_SCALE, HeadPose

CALIBRATION_FILE = calibration_path()


@dataclass
class Calibration:
    # Proxy-space centres (neutral pose).
    proxy_yaw_center: float = 0.0
    proxy_pitch_center: float = 0.0
    roll_center: float = 0.0
    x_center: float = 0.0
    y_center: float = 0.0
    # Reference face size at the neutral depth (pixels, outer eye corners).
    face_px_ref: float = 0.0
    reference_distance_cm: float = 60.0
    # Degrees per proxy unit (wizard-measured; heuristics until then).
    yaw_scale: float = DEFAULT_YAW_SCALE
    pitch_scale: float = DEFAULT_PITCH_SCALE
    valid: bool = False

    # ------------------------------------------------------------------
    def center_from(self, pose: HeadPose) -> None:
        """Re-centre on the current (assumed neutral) pose.

        Deliberately does *not* set ``valid``. Centring is not calibrating:
        the wizard measures the yaw/pitch scale, and only
        :meth:`CalibrationWizard.result` may claim that. Marking a bare
        centre as valid meant the first detected frame of the very first run
        saved a "calibrated" file, so every later launch skipped the wizard.
        """
        self.proxy_yaw_center = pose.proxy_yaw
        self.proxy_pitch_center = pose.proxy_pitch
        self.roll_center = pose.roll
        self.x_center = pose.tx
        self.y_center = pose.ty
        if pose.face_px > 0:
            self.face_px_ref = pose.face_px

    def apply(self, pose: HeadPose) -> dict[str, float]:
        """Turn a raw :class:`HeadPose` into a centred 6-DOF pose dict."""
        yaw = (pose.proxy_yaw - self.proxy_yaw_center) * self.yaw_scale
        pitch = (self.proxy_pitch_center - pose.proxy_pitch) * self.pitch_scale
        roll = pose.roll - self.roll_center
        x = pose.tx - self.x_center
        y = pose.ty - self.y_center
        if self.face_px_ref > 0 and pose.face_px > 0:
            z = self.reference_distance_cm * (self.face_px_ref / pose.face_px - 1.0)
        else:
            z = 0.0
        return {"yaw": yaw, "pitch": pitch, "roll": roll, "x": x, "y": y, "z": z}

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path = CALIBRATION_FILE) -> "Calibration":
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                valid = {f.name for f in cls.__dataclass_fields__.values()}
                return cls(**{k: v for k, v in raw.items() if k in valid})
            except (OSError, json.JSONDecodeError, TypeError):
                pass
        return cls()

    def save(self, path: Path = CALIBRATION_FILE) -> None:
        path.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")


# Wizard -------------------------------------------------------------------

@dataclass
class WizardStep:
    key: str
    prompt: str
    dot: tuple[float, float] | None  # normalised target position (None = centre)


STEPS: list[WizardStep] = [
    WizardStep("center", "Look straight ahead at the camera - hold still", (0.5, 0.5)),
    WizardStep("left", "Turn your head LEFT (body still) - hold still", (0.85, 0.5)),
    WizardStep("right", "Turn your head RIGHT - hold still", (0.15, 0.5)),
    WizardStep("up", "Look UP (chin up) - hold still", (0.5, 0.15)),
    WizardStep("down", "Look DOWN (chin down) - hold still", (0.5, 0.85)),
]


class CalibrationWizard:
    """Walks through :data:`STEPS`, then produces a :class:`Calibration`."""

    def __init__(self, yaw_range_deg: float, pitch_range_deg: float,
                 reference_distance_cm: float = 60.0) -> None:
        self.yaw_range = yaw_range_deg
        self.pitch_range = pitch_range_deg
        self.reference_distance = reference_distance_cm
        self.index = 0
        self.captures: dict[str, HeadPose] = {}
        self.done = False
        self.cancelled = False

    # ------------------------------------------------------------------
    @property
    def step(self) -> WizardStep | None:
        if self.done or self.cancelled or self.index >= len(STEPS):
            return None
        return STEPS[self.index]

    @property
    def prompt(self) -> str:
        if self.cancelled:
            return "Calibration cancelled (C to retry)"
        step = self.step
        if step is None:
            return "Calibration saved"
        return f"[{self.index + 1}/{len(STEPS)}] {step.prompt}"

    # ------------------------------------------------------------------
    def submit(self, pose: HeadPose) -> None:
        """Capture the current pose for the active step and advance."""
        step = self.step
        if step is None or not pose.detected:
            return
        self.captures[step.key] = pose
        self.index += 1
        if self.index >= len(STEPS):
            self.done = True

    def cancel(self) -> None:
        self.cancelled = True

    # ------------------------------------------------------------------
    def result(self, base: Calibration | None = None) -> Calibration | None:
        """Derive scales/centres from the captures (wizard must be done)."""
        if not self.done:
            return None
        need = ("center", "left", "right", "up", "down")
        if any(k not in self.captures for k in need):
            return None
        c = self.captures["center"]
        left, right = self.captures["left"], self.captures["right"]
        up, down = self.captures["up"], self.captures["down"]

        yaw_half = (left.proxy_yaw - right.proxy_yaw) * 0.5
        pitch_half = (down.proxy_pitch - up.proxy_pitch) * 0.5

        cal = base or Calibration()
        cal.proxy_yaw_center = c.proxy_yaw
        cal.proxy_pitch_center = c.proxy_pitch
        cal.roll_center = c.roll
        cal.x_center = c.tx
        cal.y_center = c.ty
        cal.face_px_ref = c.face_px
        cal.reference_distance_cm = self.reference_distance
        if abs(yaw_half) > 0.01:
            cal.yaw_scale = self.yaw_range / yaw_half
        if abs(pitch_half) > 0.01:
            cal.pitch_scale = self.pitch_range / pitch_half
        cal.valid = True
        return cal
