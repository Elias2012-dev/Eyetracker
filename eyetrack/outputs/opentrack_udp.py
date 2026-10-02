"""opentrack "UDP over network" output.

Sends exactly what opentrack's tracker-udp input expects: a single datagram
of six little-endian float64 values ``x, y, z, yaw, pitch, roll``
(translation in centimetres, angles in degrees), port 4242 by default.

Use this when you would rather let an existing opentrack installation drive
the game side (its profiles, curves and filters).
"""

from __future__ import annotations

import socket
import struct

from .base import BaseOutput

_PACKET = struct.Struct("<6d")


class OpentrackUdpOutput(BaseOutput):
    name = "opentrack-udp"

    def __init__(self, host: str = "127.0.0.1", port: int = 4242, rate_hz: int = 60) -> None:
        self.host = host
        self.port = port
        self.min_interval = 1.0 / max(rate_hz, 1)
        self._sock: socket.socket | None = None
        self._last_send = 0.0

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, pose: dict[str, float], tracking: bool, t: float) -> None:
        if self._sock is None or not tracking:
            return
        if t - self._last_send < self.min_interval:
            return
        self._last_send = t
        data = _PACKET.pack(
            pose["x"], pose["y"], pose["z"],
            pose["yaw"], pose["pitch"], pose["roll"],
        )
        try:
            self._sock.sendto(data, (self.host, self.port))
        except OSError:
            pass

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None
