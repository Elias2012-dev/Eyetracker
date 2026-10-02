"""Head-pose estimation from webcam frames (MediaPipe Face Landmarker).

Signals are derived from face-landmark geometry rather than euler
decomposition of the facial transformation matrix: the signs are then
anatomically provable and immune to canonical-space conventions.

Conventions (after centering/calibration):

* ``yaw``   degrees, positive when the subject turns their head to THEIR
            left (nose drifts toward image right).
* ``pitch`` degrees, positive when the subject looks UP.
* ``roll``  degrees, positive when the subject's left ear tilts down.
* ``tx/ty`` centimetres, +right / +up from the calibrated centre.
* ``tz``    centimetres, positive = leaning BACK (away from the camera).

The raw values are *unscaled-but-linearised*: ``yaw_scale`` /
``pitch_scale`` (degrees per proxy unit) come from the calibration wizard
and default to sane heuristics until calibrated.
"""

from __future__ import annotations

import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .paths import data_dir, model_path

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)
DEFAULT_MODEL_PATH = model_path()

# --- 478-point face mesh landmark indices (verified with tools/inspect_pose.py)
NOSE_TIP = 4
CHIN = 152
EYE_OUTER_R = 33    # outer corner of the eye on the IMAGE LEFT (subject's right)
EYE_OUTER_L = 263   # outer corner of the eye on the IMAGE RIGHT (subject's left)
EYE_INNER_R = 133
EYE_INNER_L = 362

# Heuristic scales: degrees per normalised proxy unit (tuned by calibration).
# Derived from head geometry: d(proxy_yaw)/d(yaw) ~ sin(theta)*L/D with
# L~4.5 cm nose lever and D~6.4 cm eye span => ~0.012 per degree.
DEFAULT_YAW_SCALE = 80.0
DEFAULT_PITCH_SCALE = 110.0


def geometry_proxies(lms: np.ndarray) -> tuple[float, float, float, float]:
    """Pure landmark geometry -> (proxy_yaw, proxy_pitch, roll_deg, eye_dist).

    * ``proxy_yaw``   positive when the nose sits toward image right, which
      happens when the subject turns their head to THEIR left.
    * ``proxy_pitch`` nose/eye/mouth vertical ratio; shrinks as the subject
      looks up.
    * ``roll``        eye-corner line angle in image space, positive when the
      subject's left (image-right) eye sits lower.
    """
    eye_r = lms[EYE_OUTER_R]      # image-left eye
    eye_l = lms[EYE_OUTER_L]      # image-right eye
    nose = lms[NOSE_TIP]

    eye_mid_x = (eye_r[0] + eye_l[0]) * 0.5
    eye_mid_y = (eye_r[1] + eye_l[1]) * 0.5
    eye_dist = float(np.hypot(eye_l[0] - eye_r[0], eye_l[1] - eye_r[1])) + 1e-6

    proxy_yaw = (nose[0] - eye_mid_x) / eye_dist
    mouth_y = (lms[61][1] + lms[291][1]) * 0.5
    denom = (mouth_y - eye_mid_y) + 1e-6
    proxy_pitch = (nose[1] - eye_mid_y) / denom
    roll = float(np.degrees(np.arctan2(eye_l[1] - eye_r[1], eye_l[0] - eye_r[0])))
    return proxy_yaw, proxy_pitch, roll, eye_dist


def ensure_model(path: Path | None = None) -> Path:
    path = Path(path) if path else model_path()
    if path.exists():
        return path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"[pose] downloading face landmarker model -> {path}")
        urllib.request.urlretrieve(MODEL_URL, path)
        return path
    except OSError:
        # Read-only bundle directory: retry in the writable per-user one.
        fallback = data_dir() / "models" / "face_landmarker.task"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        print(f"[pose] downloading face landmarker model -> {fallback}")
        urllib.request.urlretrieve(MODEL_URL, fallback)
        return fallback


@dataclass
class HeadPose:
    detected: bool = False
    yaw: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    tx: float = 0.0
    ty: float = 0.0
    tz: float = 0.0
    face_px: float = 0.0          # inter-outer-eye distance in pixels
    proxy_yaw: float = 0.0        # normalised nose offset (pre-scale)
    proxy_pitch: float = 0.0      # nose/eye/mouth ratio (pre-scale)
    landmarks: np.ndarray | None = None   # (N, 3) normalised x, y, z
    matrix: np.ndarray | None = None      # 4x4 facial transform (debug)


class HeadPoseEstimator:
    """Wraps the MediaPipe Face Landmarker task in VIDEO mode."""

    def __init__(self, model_path: Path | None = None, num_faces: int = 1) -> None:
        import mediapipe as mp
        from mediapipe.tasks.python import vision

        model = ensure_model(model_path)
        options = vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model)),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=num_faces,
            output_facial_transformation_matrixes=True,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self._vision = vision
        self._mp = mp
        self._landmarker = vision.FaceLandmarker.create_from_options(options)
        self._last_ts = -1

    # ------------------------------------------------------------------
    def process(self, bgr: np.ndarray, t_ms: int) -> HeadPose:
        """Estimate head pose for one BGR frame.

        ``t_ms`` must be monotonically increasing (VIDEO mode requirement).
        """
        t_ms = max(int(t_ms), self._last_ts + 1)
        self._last_ts = t_ms

        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self._landmarker.detect_for_video(image, t_ms)

        if not result.face_landmarks:
            return HeadPose()

        lms = np.array([[l.x, l.y, l.z] for l in result.face_landmarks[0]], dtype=np.float64)
        matrix = None
        if result.facial_transformation_matrixes:
            matrix = np.asarray(result.facial_transformation_matrixes[0], dtype=np.float64)

        pose = HeadPose(detected=True, landmarks=lms, matrix=matrix)
        proxy_yaw, proxy_pitch, roll, eye_dist = geometry_proxies(lms)
        eye_r = lms[EYE_OUTER_R]
        eye_l = lms[EYE_OUTER_L]
        eye_mid_x = (eye_r[0] + eye_l[0]) * 0.5
        eye_mid_y = (eye_r[1] + eye_l[1]) * 0.5

        pose.face_px = eye_dist
        pose.proxy_yaw = proxy_yaw
        pose.proxy_pitch = proxy_pitch
        pose.roll = roll

        # --- scaled outputs ---------------------------------------------
        # Offsets are absolute here; the calibration centre is subtracted
        # downstream, which also removes anatomy-specific constant bias.
        pose.yaw = pose.proxy_yaw * DEFAULT_YAW_SCALE
        pose.pitch = -pose.proxy_pitch * DEFAULT_PITCH_SCALE

        # --- translation (rough metric estimate) ------------------------
        # Lateral/vertical offset of the face centre in image space, scaled
        # to cm assuming a ~60 deg horizontal FOV at the reference depth.
        hfov = np.radians(60.0)
        k = 2.0 * np.tan(hfov / 2) * 60.0  # cm across the frame at 60 cm depth
        pose.tx = float((eye_mid_x - 0.5) * k)
        pose.ty = float((0.5 - eye_mid_y) * k)
        pose.tz = 0.0  # filled by the app from face_px vs calibrated reference

        return pose

    def close(self) -> None:
        self._landmarker.close()
