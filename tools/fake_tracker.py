"""Send synthetic head-pose packets to the Minecraft mod (no camera needed).

Runs a gentle figure-of-eight with the "head" so you can verify the mod is
receiving before pointing a webcam at your face:

    python tools/fake_tracker.py [--port 47777] [--yaw 25] [--pitch 15]
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import time


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=47777)
    p.add_argument("--yaw", type=float, default=25.0, help="yaw amplitude, degrees")
    p.add_argument("--pitch", type=float, default=15.0, help="pitch amplitude, degrees")
    p.add_argument("--rate", type=float, default=60.0, help="packets per second")
    p.add_argument("--hold", type=float, default=0.0,
                   help="pause at centre before moving (seconds)")
    args = p.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    period = 1.0 / args.rate
    t0 = time.monotonic()
    seq = 0
    print(f"sending fake pose to {args.host}:{args.port} at {args.rate:.0f} Hz - Ctrl+C to stop")
    try:
        while True:
            t = time.monotonic() - t0
            seq = (seq + 1) & 0x7FFFFFFF
            if t < args.hold:
                yaw = pitch = roll = 0.0
            else:
                u = t - args.hold
                yaw = args.yaw * math.sin(u * 0.7)
                pitch = args.pitch * math.sin(u * 1.3 + 1.0)
                roll = 5.0 * math.sin(u * 0.5)
            pkt = {
                "v": 1, "t": round(time.time(), 4), "seq": seq, "tracking": True,
                "yaw": round(yaw, 3), "pitch": round(pitch, 3), "roll": round(roll, 3),
                "x": 0.0, "y": 0.0, "z": 0.0,
            }
            sock.sendto(json.dumps(pkt, separators=(",", ":")).encode(), (args.host, args.port))
            time.sleep(period)
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
