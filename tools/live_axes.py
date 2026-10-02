"""Live axis check: watch yaw/pitch/roll move while you move your head.

Run it, then turn your head left / right / up / down on cue and see which
way the numbers go. That is how the axis signs get pinned down on a real
face (a phone camera counts) instead of guessed from theory::

    # which camera currently shows a face?
    python tools/live_axes.py --scan

    # 30 seconds of live readings (Ctrl+C or wait for it to finish)
    python tools/live_axes.py --camera-name "Elias A56" --seconds 30

    # timed sweep that ends with a verdict on every axis
    python tools/live_axes.py --camera-name Camo --script

``--script`` walks through a fixed sequence (centre, left, centre, right,
...) and compares each pose to the neutral one, printing which
``pose.invert_*`` flags - if any - are needed. Raw pose, no calibration and
no filtering, so what it reports is exactly what the tracker sees.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402

from eyetrack.cameras import list_devices, resolve_index  # noqa: E402
from eyetrack.pose import HeadPoseEstimator  # noqa: E402


def open_camera(name: str, index: int):
    idx = resolve_index(name, index)
    cap = cv2.VideoCapture(idx)
    if not cap.isOpened():
        raise SystemExit(f"could not open camera {idx} (name filter: {name or '-'})")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    return cap, idx


def scan(max_index: int, seconds: float) -> int:
    """Report which camera shows a face right now."""
    devices = {d.index: d.name for d in list_devices()}
    found = 0
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        if not cap.isOpened():
            cap.release()
            continue
        est = HeadPoseEstimator()
        deadline = time.monotonic() + seconds
        best = 0.0
        ok, frame = cap.read()
        while ok and time.monotonic() < deadline:
            pose = est.process(frame, int(time.monotonic() * 1000))
            if pose.detected:
                best = max(best, pose.face_px)
            ok, frame = cap.read()
        est.close()
        cap.release()
        verdict = f"FACE ({best:.0f} px)" if best else "no face"
        print(f"  [{i}] {devices.get(i, '(name unavailable)'):40} {verdict}")
        found += bool(best)
    print("no camera is showing a face - start the phone app so it streams")
    return found


# (label, seconds) - neutral poses are interleaved so each move is measured
# against a fresh baseline instead of a drifting one.
SCRIPT: list[tuple[str, float]] = [
    ("centre", 6.0), ("left", 5.0), ("centre", 3.0), ("right", 5.0),
    ("centre", 3.0), ("up", 5.0), ("centre", 3.0), ("down", 5.0),
    ("centre", 3.0), ("roll-left", 5.0), ("centre", 3.0), ("roll-right", 5.0),
]

# Big on-screen cue so the person moving their head can follow the sweep in
# the Preview tab - the console output is not visible to them.
CUE_FILE = Path(__file__).resolve().parent / "live_cue.html"

CUE_TEXT = {
    "centre": "STILL - look straight ahead",
    "left": "turn your head to YOUR LEFT",
    "right": "turn your head to YOUR RIGHT",
    "up": "tilt your head UP",
    "down": "tilt your head DOWN",
    "roll-left": "tilt your head to the left (ear towards shoulder)",
    "roll-right": "tilt your head to the right (ear towards shoulder)",
}


def write_cue(label: str, remaining: float, total: float) -> None:
    """Update the cue page; failures are ignored (it is a convenience)."""
    text = CUE_TEXT.get(label, label.upper())
    try:
        CUE_FILE.write_text(f"""<!doctype html><meta charset="utf-8">
<body style="margin:0;height:100vh;display:flex;align-items:center;
justify-content:center;background:#0b0b10;color:#f2f2f5;
font-family:system-ui,Segoe UI,sans-serif;text-align:center">
<div>
  <div style="font-size:12px;letter-spacing:3px;color:#8a8aa0">FOLLOW THIS</div>
  <div style="font-size:{72 if label == 'centre' else 58}px;font-weight:700;
       margin:12px 0">{text}</div>
  <div style="height:10px;width:520px;background:#222;border-radius:6px;
       overflow:hidden;margin:0 auto">
    <div style="height:100%;width:{max(0.0, remaining / total) * 100:.0f}%;
         background:{'#3f3f52' if label == 'centre' else '#59d98a'}"></div>
  </div>
  <div style="margin-top:14px;font-size:20px;color:#9a9ab0">
    {remaining:4.1f}s left in this step</div>
</div></body>""", encoding="utf-8")
    except OSError:
        pass


def run_script(cap, est) -> int:
    """Walk the sweep and report which axes need inverting."""
    print("scripted sweep - follow along, roughly:\n")
    samples: dict[str, tuple[float, float, float]] = {}
    missing: list[str] = []
    total = sum(s for _, s in SCRIPT)
    clock = time.monotonic()
    write_cue("centre", SCRIPT[0][1], SCRIPT[0][1])
    for label, seconds in SCRIPT:
        print(f"  t+{clock - time.monotonic() + seconds:5.1f}s  >>> {label.upper()} "
              f"({seconds:g}s)", flush=True)
        clock = time.monotonic() + seconds
        acc: list[tuple[float, float, float]] = []
        while True:
            now = time.monotonic()
            write_cue(label, clock - now, seconds)
            if now >= clock:
                break
            ok, frame = cap.read()
            if ok:
                pose = est.process(frame, int(now * 1000))
                if pose.detected:
                    acc.append((pose.yaw, pose.pitch, pose.roll))
        if acc:
            samples[label] = (statistics.median(v[0] for v in acc),
                              statistics.median(v[1] for v in acc),
                              statistics.median(v[2] for v in acc))
            print(f"        median yaw={samples[label][0]:+7.2f} "
                  f"pitch={samples[label][1]:+7.2f} roll={samples[label][2]:+7.2f} "
                  f"(n={len(acc)})", flush=True)
        else:
            missing.append(label)
            print("        no face detected in this step", flush=True)

    print("\nmeasured (median per step):")
    for label, (y, p, r) in samples.items():
        print(f"  {label:11} yaw={y:+7.2f}  pitch={p:+7.2f}  roll={r:+7.2f}")

    base = samples.get("centre")
    if base is None:
        print("\nno neutral reading - rerun with the face clearly visible.")
        return 1

    def delta(label: str, axis: int) -> float | None:
        v = samples.get(label)
        return None if v is None else v[axis] - base[axis]

    print("\nverdict (turning to YOUR left should give yaw+, looking up pitch+):")
    advice = []
    dy = delta("left", 0)
    if dy is None:
        advice.append("yaw: step 'left' lost the face - rerun")
    elif dy < -3.0:
        advice.append(f"yaw  : left gave {dy:+.1f} deg  -> set pose.invert_yaw = True")
    elif dy > 3.0:
        advice.append("yaw  : left gave positive yaw  -> already correct")
    else:
        advice.append(f"yaw  : left only moved {dy:+.1f} deg - move more, or the "
                      "phone is too far / too low")
    dp = delta("up", 1)
    if dp is None:
        advice.append("pitch: step 'up' lost the face - rerun")
    elif dp < -3.0:
        advice.append(f"pitch: up gave {dp:+.1f} deg  -> set pose.invert_pitch = True")
    elif dp > 3.0:
        advice.append("pitch: up gave positive pitch  -> already correct")
    else:
        advice.append(f"pitch: up only moved {dp:+.1f} deg - move more, or the "
                      "phone is too far / too low")
    drl = delta("roll-left", 2)
    if drl is not None:
        advice.append(f"roll : head tilted left gave {drl:+.1f} deg "
                      + ("(positive = roll+ to the left, as documented)"
                         if drl > 0 else "-> set pose.invert_roll = True"))
    drr = delta("roll-right", 2)
    if drl is not None and drr is not None:
        if drl * drr > 0:
            advice.append("roll : left and right tilted the SAME way - "
                          "the roll proxy is unreliable at this camera angle")
    if missing:
        advice.append(f"steps without a face: {', '.join(missing)}")
    for line in advice:
        print(f"  {line}")
    print(f"\ntotal sweep time was {total:.0f}s")
    try:
        CUE_FILE.unlink()
    except OSError:
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="live_axes", description=__doc__)
    p.add_argument("--camera", type=int, default=0, help="camera index")
    p.add_argument("--camera-name", default="", help="select by device name substring")
    p.add_argument("--seconds", type=float, default=20.0, help="how long to watch")
    p.add_argument("--interval", type=float, default=0.25, help="print interval")
    p.add_argument("--script", action="store_true",
                   help="run the timed sweep and report which axes are inverted")
    p.add_argument("--scan", action="store_true",
                   help="scan cameras for one showing a face and exit")
    p.add_argument("--scan-seconds", type=float, default=2.0)
    args = p.parse_args(argv)

    if args.scan:
        scan(4, args.scan_seconds)
        return 0

    if args.script:
        cap, idx = open_camera(args.camera_name, args.camera)
        est = HeadPoseEstimator()
        print(f"camera {idx} - START MOVING when the sweep begins\n", flush=True)
        try:
            return run_script(cap, est)
        finally:
            est.close()
            cap.release()

    cap, idx = open_camera(args.camera_name, args.camera)
    est = HeadPoseEstimator()
    print(f"camera {idx}; turn LEFT / RIGHT / UP / DOWN on cue, watching the signs")
    print(f"running for {args.seconds:g}s - Ctrl+C to stop\n")

    lo = {"yaw": 1e9, "pitch": 1e9, "roll": 1e9}
    hi = {"yaw": -1e9, "pitch": -1e9, "roll": -1e9}
    t0 = t_last = time.monotonic()
    lost = 0
    try:
        while time.monotonic() - t0 < args.seconds:
            ok, frame = cap.read()
            t = time.monotonic()
            if ok:
                pose = est.process(frame, int(t * 1000))
            else:
                pose = None
            if pose is not None and pose.detected:
                for k in ("yaw", "pitch", "roll"):
                    v = getattr(pose, k)
                    lo[k] = min(lo[k], v)
                    hi[k] = max(hi[k], v)
            elif ok:
                lost += 1
            if t - t_last >= args.interval:
                t_last = t
                if pose is not None and pose.detected:
                    print(f"t={t - t0:6.2f}s  yaw={pose.yaw:+7.2f}  "
                          f"pitch={pose.pitch:+7.2f}  roll={pose.roll:+7.2f}")
                else:
                    print(f"t={t - t0:6.2f}s  (no face)")
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        est.close()
        cap.release()

    if lo["yaw"] < 1e9:
        print("\nmin/max per axis (deg):")
        for k in ("yaw", "pitch", "roll"):
            print(f"  {k:6} {lo[k]:+7.2f} .. {hi[k]:+7.2f}")
        print(f"\nframes with no face: {lost}")
        print("expected signs: turning to YOUR left -> yaw+;  looking up -> pitch+")
    else:
        print("\nno face was ever detected - check lighting and that the "
              "phone app is actually streaming")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())