"""UDP JSON output - consumed by the Minecraft Fabric mod (and anything else).

Datagram format (UTF-8 JSON, one object per packet)::

    {"v":1,"t":171234.5,"seq":42,"tracking":true,
     "yaw":12.3,"pitch":-4.5,"roll":0.2,"x":1.2,"y":-0.5,"z":0.0}

Angles are degrees (yaw + = head turned to subject's left, pitch + = up),
translations are centimetres relative to the calibrated centre.
"""

from __future__ import annotations

import json
import socket

from .base import BaseOutput


class UdpJsonOutput(BaseOutput):
    name = "udp-json"

    def __init__(self, host: str = "127.0.0.1", port: int = 47777, rate_hz: int = 60) -> None:
        self.host = host
        self.port = port
        self.min_interval = 1.0 / max(rate_hz, 1)
        self._sock: socket.socket | None = None
        self._seq = 0
        self._last_send = 0.0

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Loopback-only traffic by default; allow broadcast for LAN use.
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    def send(self, pose: dict[str, float], tracking: bool, t: float) -> None:
        if self._sock is None:
            return
        if t - self._last_send < self.min_interval:
            return
        self._last_send = t
        self._seq = (self._seq + 1) & 0x7FFFFFFF
        packet = {
            "v": 1,
            "t": round(t, 4),
            "seq": self._seq,
            "tracking": bool(tracking),
            "yaw": round(pose["yaw"], 3),
            "pitch": round(pose["pitch"], 3),
            "roll": round(pose["roll"], 3),
            "x": round(pose["x"], 3),
            "y": round(pose["y"], 3),
            "z": round(pose["z"], 3),
        }
        data = json.dumps(packet, separators=(",", ":")).encode("utf-8")
        try:
            self._sock.sendto(data, (self.host, self.port))
        except OSError:
            pass  # never let telemetry kill the tracking loop

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None
