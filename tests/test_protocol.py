import json
import socket

from eyetrack.outputs.udp_json import UdpJsonOutput

POSE = {"yaw": 12.5, "pitch": -4.25, "roll": 0.5, "x": 1.0, "y": 2.0, "z": 3.0}


def _bind(port: int) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", port))
    s.settimeout(2.0)
    return s


def test_udp_json_roundtrip():
    rx = _bind(47891)
    out = UdpJsonOutput(host="127.0.0.1", port=47891, rate_hz=1000)
    out.start()
    out.send(POSE, True, 5.0)
    data, _ = rx.recvfrom(4096)
    rx.close()
    out.close()

    pkt = json.loads(data.decode("utf-8"))
    assert pkt["v"] == 1
    assert pkt["tracking"] is True
    assert pkt["yaw"] == 12.5
    assert pkt["pitch"] == -4.25
    assert pkt["seq"] >= 1
    for key in ("x", "y", "z", "roll", "t"):
        assert key in pkt


def test_udp_json_rate_limit():
    rx = _bind(47892)
    out = UdpJsonOutput(host="127.0.0.1", port=47892, rate_hz=10)
    out.start()
    for i in range(100):
        out.send(POSE, True, i * 0.001)  # 1 kHz worth of calls
    got = 0
    try:
        while True:
            rx.recvfrom(4096)
            got += 1
    except socket.timeout:
        pass
    rx.close()
    out.close()
    assert got <= 3, f"rate limiter let {got} packets through in ~0.1s"
