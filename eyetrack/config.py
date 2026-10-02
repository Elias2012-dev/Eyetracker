"""Configuration model + (de)serialization for the tracker.

The config lives in a plain JSON file (default: ``eyetrack.json`` next to the
repository root).  Unknown keys are ignored so newer configs keep working with
older code and vice versa.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CameraConfig:
    index: int = 0
    # Select by device name instead of index - ideal for phone-as-webcam
    # virtual cameras (Iriun, DroidCam, Camo, OBS Virtual Camera, ...).
    # Case-insensitive substring match; empty string = use ``index``.
    device_name: str = ""
    width: int = 1280
    height: int = 720
    fps: int = 60
    mirror: bool = True  # show a selfie-style preview


@dataclass
class PoseConfig:
    # Comfortable head-turn ranges (degrees) used by the calibration wizard
    # and as defaults before calibration has been run.
    yaw_range: float = 40.0
    pitch_range: float = 30.0
    roll_range: float = 20.0
    # Seated distance used for the translation (X/Y/Z) reference, in cm.
    reference_distance_cm: float = 60.0
    # Gains applied to the calibrated pose before sending (1.0 = raw).
    yaw_gain: float = 1.0
    pitch_gain: float = 1.0
    roll_gain: float = 1.0
    x_gain: float = 1.0
    y_gain: float = 1.0
    z_gain: float = 1.0
    # Sign flips, only needed if a game's axes feel inverted.
    invert_yaw: bool = False
    invert_pitch: bool = False
    invert_roll: bool = False
    invert_x: bool = False
    invert_y: bool = False
    invert_z: bool = False


@dataclass
class FilterConfig:
    min_cutoff: float = 1.0
    beta: float = 0.03
    d_cutoff: float = 1.0


@dataclass
class UdpJsonConfig:
    """JSON datagrams consumed by the Minecraft mod (and anything else)."""

    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 47777
    rate_hz: int = 60


@dataclass
class GameLinkConfig:
    """Direct in-game link for ETS2 / ATS via our own NPClient DLL.

    Windows only; enabled by default there.  The registry bridge is set up
    automatically on startup (previous values are backed up).
    """

    enabled: bool = field(default_factory=lambda: sys.platform == "win32")
    auto_bridge: bool = True


@dataclass
class MouseConfig:
    """Head pose -> relative mouse motion for games *without* TrackIR.

    Windows only (SendInput); disabled by default.  The output starts
    disarmed - press ``toggle_key`` to arm/disarm it at runtime.
    """

    enabled: bool = False
    sensitivity: float = 5.0  # pixels per degree of yaw
    v_sensitivity: float = 5.0  # pixels per degree of pitch
    deadzone_deg: float = 2.0  # ignore head jitter around the neutral pose
    rate_hz: int = 60
    invert_x: bool = False
    invert_y: bool = False
    toggle_key: str = "F9"  # "none" = no toggle, always active


@dataclass
class OverlayConfig:
    enabled: bool = True
    show_mesh: bool = True
    window: str = "eyetrack"
    # Small numbers-only panel instead of the camera view, for keeping the
    # axes in peripheral vision while a game owns the focus.
    compact: bool = False
    # Stay above the game window. Off by default: it is a preference, and
    # silently stealing z-order is surprising.
    top_most: bool = False


@dataclass
class Config:
    camera: CameraConfig = field(default_factory=CameraConfig)
    pose: PoseConfig = field(default_factory=PoseConfig)
    filter: FilterConfig = field(default_factory=FilterConfig)
    udp_json: UdpJsonConfig = field(default_factory=UdpJsonConfig)
    game_link: GameLinkConfig = field(default_factory=GameLinkConfig)
    mouse: MouseConfig = field(default_factory=MouseConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)

    # ------------------------------------------------------------------
    @staticmethod
    def _merge(obj: dict[str, Any], cls: type) -> Any:
        """Build a dataclass from a dict, keeping defaults for missing keys.

        Unknown keys are dropped so configs written for newer versions keep
        loading here.
        """
        valid = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in obj.items() if k in valid})

    @classmethod
    def load(cls, path: Path | str) -> "Config":
        path = Path(path)
        cfg = cls()
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SystemExit(f"Could not read config {path}: {exc}") from exc
            for name in (
                "camera", "pose", "filter", "udp_json",
                "game_link", "mouse", "overlay",
            ):
                if isinstance(raw.get(name), dict):
                    sub_cls = getattr(cls, "__dataclass_fields__")[name].default_factory  # type: ignore[attr-defined]
                    setattr(cfg, name, cls._merge(raw[name], sub_cls))
        return cfg

    def save(self, path: Path | str) -> None:
        path = Path(path)
        data = dataclasses.asdict(self)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
