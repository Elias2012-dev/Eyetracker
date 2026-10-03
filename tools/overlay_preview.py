"""Render the HUD over a real photo so the layout can be judged by eye.

    python tools/overlay_preview.py                    # default sample image
    python tools/overlay_preview.py --image my.jpg --yaw -25 --pitch 12

Writes full/compact PNGs plus a self-contained preview.html into
tools/preview/. Nothing here ships in the tracker; it exists so the HUD
can be iterated on without a live camera in the loop.
"""

from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from eyetrack.overlay import Overlay  # noqa: E402
from eyetrack.pose import HeadPose, HeadPoseEstimator  # noqa: E402

OUT = Path(__file__).resolve().parent / "preview"
DEFAULT_IMAGE = Path(__file__).resolve().parent / "data" / "frontal.jpg"


class _StaticWizard:
    """Minimal stand-in so the wizard panel can be previewed too."""

    def __init__(self, index: int = 1) -> None:
        self.index = index
        self.captures: dict[str, HeadPose] = {}
        self.done = False
        self.cancelled = False

    @property
    def step(self):
        from eyetrack.calibrate import STEPS
        return STEPS[self.index] if self.index < len(STEPS) else None

    @property
    def prompt(self) -> str:
        step = self.step
        return f"[{self.index + 1}/{len(self.captures) + 3}] {step.prompt}" if step else "done"


def letterbox(frame, width: int, height: int):
    """Fit without distorting: a squashed face stops being detected."""
    h, w = frame.shape[:2]
    scale = min(width / w, height / h)
    new = cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale))),
                     interpolation=cv2.INTER_AREA)
    canvas = np.zeros((height, width, 3), np.uint8)
    canvas[:] = (18, 16, 22)
    ox = (width - new.shape[1]) // 2
    oy = (height - new.shape[0]) // 2
    canvas[oy:oy + new.shape[0], ox:ox + new.shape[1]] = new
    return canvas


def webcam_crop(frame, pose, out_w: int = 1280, out_h: int = 720, margin: float = 2.4):
    """Crop a 16:9 window around the face, the way a webcam would frame it.

    Letterboxing the whole photo makes the face too small for the
    landmarker; a crop keeps detection working and matches the framing a
    user actually sees.
    """
    if pose.landmarks is None:
        return letterbox(frame, out_w, out_h)
    lms = np.asarray(pose.landmarks)
    h, w = frame.shape[:2]
    x0, x1 = float(lms[:, 0].min()), float(lms[:, 0].max())
    y0, y1 = float(lms[:, 1].min()), float(lms[:, 1].max())
    face_h = max(1.0, (y1 - y0) * h)
    cx, cy = (x0 + x1) * 0.5 * w, (y0 + y1) * 0.5 * h

    # Window: face height * margin, width from the output aspect ratio,
    # then clamp to what the source actually has.
    ch = min(float(h), face_h * margin)
    cw = min(float(w), ch * (out_w / out_h))
    if cw >= w:                      # wide source: derive height from width
        cw = float(w)
        ch = min(float(h), cw * (out_h / out_w))
    left = int(round(min(max(0.0, cx - cw * 0.5), w - cw)))
    top = int(round(min(max(0.0, cy - ch * 0.5), h - ch)))
    crop = frame[top:top + int(ch), left:left + int(cw)]
    return cv2.resize(crop, (out_w, out_h), interpolation=cv2.INTER_AREA)


CHIPS = (("TrackIR", True), ("Minecraft", True), ("Mouse", False))


def render(image_path: Path, out_values: dict[str, float], *, compact: bool,
           wizard: bool = False, status: str = "", tracking: bool = True,
           help_: bool = False) -> np.ndarray:
    frame = cv2.imread(str(image_path))
    if frame is None:
        raise SystemExit(f"cannot read {image_path}")

    est = HeadPoseEstimator()
    pose = est.process(frame, t_ms=1)
    est.close()
    print(f"  source face: {'found' if pose.detected else 'MISSING'}")
    frame = webcam_crop(frame, pose)

    est = HeadPoseEstimator()
    pose2 = est.process(frame, t_ms=1)
    est.close()
    print(f"  landmarks: {'found' if pose2.detected else 'MISSING'}  "
          f"yaw={pose2.yaw:+.1f} pitch={pose2.pitch:+.1f} roll={pose2.roll:+.1f}")
    pose = pose2

    if compact:
        return Overlay(show_mesh=True, compact=True)._compact_canvas(
            out_values, tracking, 58.4, status, CHIPS)

    ov = Overlay(show_mesh=True, compact=False)
    ov.help_visible = help_
    # A few frames of trail, so the render shows a path rather than a dot.
    for dy, dx in ((0, 0), (.1, -.05), (.2, -.12), (.15, -.22), (.05, -.3)):
        ov._update_trail({"yaw": out_values["yaw"] + dy * 40,
                          "pitch": out_values["pitch"] + dx * 30}, True)
    return ov._compose(frame.copy(), pose, out_values, tracking, 58.4, False,
                       _StaticWizard() if wizard else None, status, CHIPS)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="overlay_preview", description=__doc__)
    p.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    p.add_argument("--yaw", type=float, default=-18.5)
    p.add_argument("--pitch", type=float, default=7.0)
    p.add_argument("--roll", type=float, default=-9.0)
    p.add_argument("--x", type=float, default=-4.2)
    p.add_argument("--y", type=float, default=1.5)
    p.add_argument("--z", type=float, default=8.0)
    args = p.parse_args(argv)

    if not args.image.exists():
        raise SystemExit(f"{args.image} not found - pass --image <path>")
    OUT.mkdir(parents=True, exist_ok=True)

    values = {"yaw": args.yaw, "pitch": args.pitch, "roll": args.roll,
              "x": args.x, "y": args.y, "z": args.z}
    shots = []
    for name, kwargs in (
        ("full", dict(compact=False)),
        ("wizard", dict(compact=False, wizard=True)),
        ("noface", dict(compact=False, tracking=False)),
        ("help", dict(compact=False, help_=True)),
        ("compact", dict(compact=True, status="calibration saved")),
    ):
        print(f"rendering {name}...")
        img = render(args.image, values, **kwargs)
        path = OUT / f"{name}.png"
        cv2.imwrite(str(path), img)
        shots.append((name, path))
        print(f"  -> {path}")

    parts = ["<!doctype html><meta charset='utf-8'>"
             "<meta name='viewport' content='width=device-width,initial-scale=1'>"
             "<body style='margin:0;background:#0b0b10;color:#ddd;"
             "font-family:system-ui;padding:10px'>"
             "<style>img{width:100%;max-width:1280px;display:block;"
             "border:1px solid #333}h3{margin:12px 0 5px;font-weight:600}</style>"]
    for name, path in shots:
        img = cv2.imread(str(path))
        # Wide enough to judge the HUD, small enough that the preview webview
        # keeps compositing (a fat data: URL stops it painting entirely).
        thumb = cv2.resize(img, (900, int(img.shape[0] * 900 / img.shape[1])),
                           interpolation=cv2.INTER_AREA)
        small = OUT / f"{name}_small.jpg"
        cv2.imwrite(str(small), thumb, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
        data = base64.b64encode(small.read_bytes()).decode()
        parts.append(f"<h3>{name}</h3>"
                     f"<img src='data:image/jpeg;base64,{data}'>")
    parts.append("</body>")
    html = OUT / "preview.html"
    html.write_text("".join(parts), encoding="utf-8")
    print(f"\nopen {html}  ({html.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())