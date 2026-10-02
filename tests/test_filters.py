import random

from eyetrack.filters import OneEuroFilter, PoseFilter
from eyetrack.config import FilterConfig


def test_constant_signal_passes_unchanged():
    f = OneEuroFilter(min_cutoff=1.0, beta=0.03)
    for i in range(50):
        out = f(42.0, i * 0.016)
    assert abs(out - 42.0) < 1e-9


def test_step_converges():
    f = OneEuroFilter(min_cutoff=1.0, beta=0.05)
    out = 0.0
    for i in range(500):
        out = f(10.0 if i > 0 else 0.0, i * 0.016)
    assert abs(out - 10.0) < 1e-3


def test_smoothing_reduces_noise_variance():
    random.seed(7)
    f = OneEuroFilter(min_cutoff=0.8, beta=0.01)
    raw, smooth = [], []
    for i in range(600):
        v = 10.0 + random.uniform(-1.0, 1.0)
        raw.append(v)
        smooth.append(f(v, i * 0.016))
    def var(xs):
        m = sum(xs) / len(xs)
        return sum((x - m) ** 2 for x in xs) / len(xs)
    assert var(smooth) < var(raw) * 0.5


def test_reset_forgets_state():
    f = OneEuroFilter()
    f(100.0, 0.0)
    f.reset()
    out = f(5.0, 1.0)  # first sample after reset passes through
    assert out == 5.0


def test_pose_filter_keys():
    pf = PoseFilter(FilterConfig())
    out = pf.apply({"yaw": 1, "pitch": 2, "roll": 3, "x": 4, "y": 5, "z": 6}, 0.0)
    assert set(out) == set(PoseFilter.AXES)
    assert out["yaw"] == 1
