"""Smoothing filters.

The One-Euro filter (Casiez, Cockburn & Vijayalakshmi, CHI 2012) adapts its
cutoff to movement speed: quiet when the head is still, responsive when it
moves.  It is the classic choice for head-tracking pipelines.
"""

from __future__ import annotations

from .config import FilterConfig


class OneEuroFilter:
    """Scalar One-Euro filter."""

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.03, d_cutoff: float = 1.0) -> None:
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self._x_prev: float | None = None
        self._dx_prev: float = 0.0
        self._t_prev: float | None = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * 3.141592653589793 * cutoff)
        return 1.0 / (tau / dt + 1.0)

    def reset(self) -> None:
        self._x_prev = None
        self._dx_prev = 0.0
        self._t_prev = None

    def __call__(self, value: float, t: float) -> float:
        if self._x_prev is None or self._t_prev is None:
            self._x_prev = value
            self._t_prev = t
            return value
        dt = t - self._t_prev
        if dt <= 0.0:
            return self._x_prev
        self._t_prev = t

        dx = (value - self._x_prev) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        dx_hat = a_d * dx + (1.0 - a_d) * self._dx_prev

        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        a = self._alpha(cutoff, dt)
        x_hat = a * value + (1.0 - a) * self._x_prev

        self._x_prev = x_hat
        self._dx_prev = dx_hat
        return x_hat


class PoseFilter:
    """One-Euro filter bank for a 6-DOF pose."""

    AXES = ("yaw", "pitch", "roll", "x", "y", "z")

    def __init__(self, cfg: FilterConfig) -> None:
        self._filters = {axis: OneEuroFilter(cfg.min_cutoff, cfg.beta, cfg.d_cutoff) for axis in self.AXES}

    def reset(self) -> None:
        for f in self._filters.values():
            f.reset()

    def apply(self, pose: dict[str, float], t: float) -> dict[str, float]:
        return {axis: self._filters[axis](pose[axis], t) for axis in self.AXES}
