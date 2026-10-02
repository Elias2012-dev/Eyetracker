import numpy as np

from eyetrack.pose import (
    EYE_INNER_L,
    EYE_INNER_R,
    EYE_OUTER_L,
    EYE_OUTER_R,
    NOSE_TIP,
    geometry_proxies,
)


def _neutral_landmarks() -> np.ndarray:
    lms = np.zeros((478, 3))
    # Eyes on a horizontal line, nose centred between eyes and mouth.
    lms[EYE_OUTER_R] = (0.35, 0.45, 0.0)   # image-left eye
    lms[EYE_OUTER_L] = (0.65, 0.45, 0.0)   # image-right eye
    lms[EYE_INNER_R] = (0.42, 0.45, 0.0)
    lms[EYE_INNER_L] = (0.58, 0.45, 0.0)
    lms[NOSE_TIP] = (0.50, 0.55, -0.1)
    lms[61] = (0.42, 0.68, 0.0)   # mouth corners
    lms[291] = (0.58, 0.68, 0.0)
    return lms


def test_neutral_pose_is_centred():
    py, pp, roll, eye_dist = geometry_proxies(_neutral_landmarks())
    assert abs(py) < 1e-9
    assert abs(roll) < 1e-6
    assert abs(eye_dist - 0.30) < 1e-5  # epsilon guards divide-by-zero
    # Neutral ratio: (0.55-0.45)/(0.68-0.45) ~ 0.43
    assert 0.40 < pp < 0.46


def test_turn_left_gives_positive_yaw():
    lms = _neutral_landmarks()
    lms[NOSE_TIP, 0] = 0.56          # nose drifts toward image right
    py, *_ = geometry_proxies(lms)
    assert py > 0, "turning to the subject's left must read positive yaw"


def test_turn_right_gives_negative_yaw():
    lms = _neutral_landmarks()
    lms[NOSE_TIP, 0] = 0.44
    py, *_ = geometry_proxies(lms)
    assert py < 0


def test_look_up_shrinks_pitch_ratio():
    lms = _neutral_landmarks()
    base = geometry_proxies(_neutral_landmarks())[1]
    lms[NOSE_TIP, 1] = 0.51           # nose rises toward the eye line
    up = geometry_proxies(lms)[1]
    assert up < base, "looking up must shrink the eye->nose->mouth ratio"


def test_roll_sign_when_image_right_eye_lower():
    lms = _neutral_landmarks()
    lms[EYE_OUTER_L, 1] = 0.50        # subject's left eye lower in image
    _, _, roll, _ = geometry_proxies(lms)
    assert roll > 0
