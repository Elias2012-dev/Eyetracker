"""Save a still from each camera - is my phone actually streaming?

Writes ``snap.html`` plus one JPEG per camera into ``snapshots/`` and
reports brightness, so you can tell three cases apart without guessing:

* black frame (mean near 0) - the companion app is not streaming
* a picture but no face - the phone is streaming, it just cannot see you
  (bad angle, too dark, too far, or an obstructed lens)

    python tools/snapshots.py
"""

from __future__ import annotations

import argparse
import base64
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402

from eyetrack.cameras import list_devices  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "snapshots"


def capture(index: int, name: str, warmup: int = 20) -> tuple[bool, str]:
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        return False, "could not open"
    frame = None
    for _ in range(warmup):
        ok, img = cap.read()
        if ok:
            frame = img
        time.sleep(0.1)
    cap.release()
    if frame is None:
        return False, "no frames"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"camera{index}.jpg"
    cv2.imwrite(str(path), frame)
    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return True, (f"{frame.shape[1]}x{frame.shape[0]}  "
                  f"mean={grey.mean():5.1f} std={grey.std():5.1f}  -> {path.name}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="snapshots", description=__doc__)
    p.add_argument("--cameras", type=int, default=4, help="how many indices to try")
    args = p.parse_args(argv)

    devices = {d.index: d.name for d in list_devices()}
    rows = []
    for i in range(args.cameras):
        name = devices.get(i, "(name unavailable)")
        ok, info = capture(i, name)
        print(f"  [{i}] {name:42} {info}")
        if ok:
            rows.append((i, name, info))

    if rows:
        parts = [
            "<html><body style='background:#111;color:#eee;"
            "font-family:system-ui;padding:16px'>",
            "<h3>Camera snapshots</h3><div style='display:flex;gap:16px;flex-wrap:wrap'>",
        ]
        for i, name, info in rows:
            # Inline the JPEG: a single self-contained file that can be
            # opened anywhere (or served) without a folder of assets.
            data = base64.b64encode((OUT_DIR / f"camera{i}.jpg").read_bytes()).decode()
            parts.append(
                f"<figure style='margin:0'><img src='data:image/jpeg;base64,{data}' "
                f"style='width:460px;border:1px solid #555'>"
                f"<figcaption>[{i}] {name}<br><small>{info}</small></figcaption></figure>")
        parts.append("</div></body></html>")
        html = OUT_DIR / "snap.html"
        html.write_text("".join(parts), encoding="utf-8")
        print(f"\nopen {html} to look at them")
    else:
        print("\nno camera produced an image")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())