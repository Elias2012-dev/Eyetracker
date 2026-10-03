"""Does the tracker actually send UDP packets?

The HUD lights the "Minecraft" chip whenever a face is tracked, which
says nothing about whether a datagram ever left the process - the chip
is computed from `tracking`, not from a successful send. So this binds
the mod's port, runs the real session against the real camera, and
reports what actually arrived on the wire.

Usage: check_udp_live.py [seconds] [port]
"""
from __future__ import annotations

import json
import pathlib
import socket
import tempfile
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from eyetrack.config import Config                      # noqa: E402
from eyetrack.session import TrackingSession            # noqa: E402

received: list[dict] = []
stop = threading.Event()


def listen(port: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.settimeout(0.2)
    while not stop.is_set():
        try:
            data, addr = sock.recvfrom(2048)
        except socket.timeout:
            continue
        except OSError:
            break
        try:
            received.append(json.loads(data.decode("utf-8")))
        except ValueError:
            received.append({"_unparsed": data[:80].decode("utf-8", "replace")})
    sock.close()


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 47777

    cfg = Config()
    cfg.udp_json.enabled = True
    cfg.udp_json.port = port
    cfg.overlay.enabled = False

    t = threading.Thread(target=listen, args=(port,), daemon=True)
    t.start()
    time.sleep(0.3)

    print(f"listening on 127.0.0.1:{port}")
    cfg_path = pathlib.Path(tempfile.mkdtemp()) / "eyetrack.json"
    session = TrackingSession(cfg, cfg_path)
    session.start()
    # Outputs are built in start(), not the constructor.
    print("outputs:", session.describe_outputs())
    deadline = time.time() + seconds
    frames = 0
    try:
        while time.time() < deadline:
            res = session.step()
            if res is not None:
                frames += 1
            time.sleep(1 / 60)
    finally:
        session.stop()
        stop.set()
        t.join(timeout=2)

    print(f"frames stepped : {frames}")
    print(f"packets heard  : {len(received)}")
    if received:
        print("first packet   :", json.dumps(received[0])[:200])
        tracked = [p for p in received if p.get("tracking")]
        print(f"tracking=true  : {len(tracked)} packets")
        print("verdict: THE TRACKER SENDS - if Minecraft is still dead, the "
              "fault is in the mod")
        return 0
    print("verdict: NOTHING WAS SENT - the tracker never reaches the wire, "
          "and no amount of Minecraft debugging will help")
    return 1


if __name__ == "__main__":
    sys.exit(main())