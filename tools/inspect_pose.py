"""Scratch: inspect FaceLandmarker output conventions on sample images.

Prints, for each image: selected landmark coordinates, the facial
transformation matrix and candidate Euler decompositions, plus landmark-based
nose-offset proxies.  Used once to pin down sign conventions.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eyetrack.pose import HeadPoseEstimator  # noqa: E402


def main(paths: list[str]) -> None:
    est = HeadPoseEstimator()
    for p in paths:
        img = cv2.imread(p)
        if img is None:
            print("cannot read", p)
            continue
        out = est.process(img, t_ms=0)
        print("=" * 70)
        print(p, "size", img.shape[1], "x", img.shape[0])
        if not out.detected:
            print("  no face detected")
            continue
        print(f"  matrix-derived yaw/pitch/roll = {out.yaw:.2f} / {out.pitch:.2f} / {out.roll:.2f}")
        print(f"  translation (scaled) x/y/z = {out.tx:.2f} / {out.ty:.2f} / {out.tz:.2f}  face_px={out.face_px:.1f}")
        print(f"  proxies: nose_dx={out.proxy_yaw:.4f} nose_dy={out.proxy_pitch:.4f}")
        if out.matrix is not None:
            np.set_printoptions(precision=4, suppress=True)
            print("  M =\n", out.matrix)
            R = out.matrix[:3, :3]
            # candidate decompositions (rotation order guesses)
            import math

            def d(a):
                return math.degrees(a)

            # XYZ intrinsic (R = Rx @ Ry @ Rz style)
            if abs(R[2, 0]) < 0.99999:
                p_x = math.atan2(R[2, 1], R[2, 2])
                p_y = math.asin(-R[2, 0])
                p_z = math.atan2(R[1, 0], R[0, 0])
                print(f"  euler XYZ: roll={d(p_x):.2f} pitch={d(p_y):.2f} yaw={d(p_z):.2f}")
            # ZYX intrinsic
            if abs(R[0, 2]) < 0.99999:
                p_y2 = math.asin(R[0, 2])
                p_x2 = math.atan2(-R[1, 2], R[2, 2])
                p_z2 = math.atan2(-R[0, 1], R[0, 0])
                print(f"  euler ZYX: roll={d(p_x2):.2f} pitch={d(p_y2):.2f} yaw={d(p_z2):.2f}")
            print("  R diag:", np.diag(R))


if __name__ == "__main__":
    main(sys.argv[1:])
