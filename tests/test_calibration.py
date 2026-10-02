from eyetrack.calibrate import Calibration, CalibrationWizard
from eyetrack.pose import HeadPose


def _pose(proxy_yaw=0.0, proxy_pitch=0.45, roll=0.0, tx=0.0, ty=0.0, eye=0.3) -> HeadPose:
    p = HeadPose(detected=True)
    p.proxy_yaw = proxy_yaw
    p.proxy_pitch = proxy_pitch
    p.roll = roll
    p.tx = tx
    p.ty = ty
    p.face_px = eye
    return p


def _run_wizard(yaw_range=40.0, pitch_range=30.0) -> Calibration:
    w = CalibrationWizard(yaw_range, pitch_range, reference_distance_cm=50.0)
    captures = {
        "center": _pose(proxy_yaw=0.10, proxy_pitch=0.45),
        "left": _pose(proxy_yaw=0.60, proxy_pitch=0.45),
        "right": _pose(proxy_yaw=-0.40, proxy_pitch=0.45),
        "up": _pose(proxy_yaw=0.10, proxy_pitch=0.30),
        "down": _pose(proxy_yaw=0.10, proxy_pitch=0.60),
    }
    for key in ("center", "left", "right", "up", "down"):
        assert w.step is not None and w.step.key == key
        w.submit(captures[key])
    assert w.done
    cal = w.result()
    assert cal is not None
    return cal


def test_wizard_scales_and_centres():
    cal = _run_wizard()
    # yaw half-span = (0.60 - (-0.40)) / 2 = 0.50 -> scale = 40 / 0.5 = 80
    assert abs(cal.yaw_scale - 80.0) < 1e-9
    # pitch half-span = (0.60 - 0.30) / 2 = 0.15 -> scale = 30 / 0.15 = 200
    assert abs(cal.pitch_scale - 200.0) < 1e-9
    assert cal.valid

    # Neutral pose maps to (nearly) zero.
    neutral = cal.apply(_pose(proxy_yaw=0.10, proxy_pitch=0.45))
    assert abs(neutral["yaw"]) < 1e-9
    assert abs(neutral["pitch"]) < 1e-9

    # 0.15 proxy units above centre on a 80 deg/unit scale -> +12 degrees.
    turned = cal.apply(_pose(proxy_yaw=0.25, proxy_pitch=0.45))
    assert abs(turned["yaw"] - 12.0) < 1e-6

    # Smaller pitch ratio (nose higher) -> positive pitch (looking up).
    looked_up = cal.apply(_pose(proxy_yaw=0.10, proxy_pitch=0.45 - 0.1))
    assert looked_up["pitch"] > 0


def test_translation_centring_and_depth():
    cal = _run_wizard()
    p = _pose(proxy_yaw=0.10, proxy_pitch=0.45, tx=4.0, ty=-2.0, eye=0.3)
    out = cal.apply(p)
    assert out["x"] == 4.0
    assert out["y"] == -2.0
    assert abs(out["z"]) < 1e-9

    # Face twice as big = half the distance = -reference_distance_cm / 2 ...
    closer = cal.apply(_pose(proxy_yaw=0.10, proxy_pitch=0.45, eye=0.6))
    assert closer["z"] < 0
    # ... face half as big -> z = 50 * (0.3/0.15 - 1) = +50 cm
    farther = cal.apply(_pose(proxy_yaw=0.10, proxy_pitch=0.45, eye=0.15))
    assert abs(farther["z"] - 50.0) < 1e-6


def test_calibration_roundtrip(tmp_path):
    cal = _run_wizard()
    path = tmp_path / "calibration.json"
    cal.save(path)
    loaded = Calibration.load(path)
    assert loaded.yaw_scale == cal.yaw_scale
    assert loaded.proxy_yaw_center == cal.proxy_yaw_center
    assert loaded.valid


def test_wizard_cancel():
    w = CalibrationWizard(40, 30)
    w.cancel()
    assert w.step is None
    assert w.result() is None
