"""Generate the mod icon used by Modrinth, CurseForge and Fabric.

Both platforms want a square 128x128 PNG. Modrinth renders it at 32px in
sits listings, so the mark has to survive being shrunk to a thumbnail:
no text, one silhouette, two flat colours, high contrast.

Drawn at 8x and downsampled, because PIL's curves are aliased at 128px and
the whole point is a clean edge.

Run: python tools/make_mod_icon.py
"""
from __future__ import annotations

import pathlib

from PIL import Image, ImageChops, ImageDraw

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "minecraft-mod" / "src" / "main" / "resources" / "assets" / "eyetrack" / "icon.png"

SIZE = 128
SS = 8                      # supersampling factor
S = SIZE * SS

# The app's own palette (see eyetrack/theme.py) so the icon matches the HUD.
BG_OUTER = (24, 15, 18)    # #180F12
BG_INNER = (39, 25, 30)     # #27191E
ACCENT = (148, 226, 122)    # #94E27A
INK = (250, 245, 243)       # #FAF5F3


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def build() -> Image.Image:
    img = Image.new("RGB", (S, S), BG_OUTER)
    d = ImageDraw.Draw(img)

    # Radial lift toward the centre so the tile is not a flat slab.
    cx = cy = S / 2
    max_r = S * 0.72
    step = 2 * SS
    for y in range(0, S, step):
        for x in range(0, S, step):
            r = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 / max_r
            t = max(0.0, 1.0 - r) ** 1.6
            d.rectangle([x, y, x + step, y + step],
                        fill=_lerp(BG_OUTER, BG_INNER, t))

    # --- the mark, drawn on its own layer ---------------------------
    # Positioning by hand got the margins uneven (18/10/18/8); drawing
    # into a mask and centring on its real bounding box cannot.
    mark = Image.new("L", (S, S), 0)
    m = ImageDraw.Draw(mark)

    # Head: a circle plus a shoulder arc - one silhouette, readable at 32px.
    head_r = 26 * SS
    head_cx, head_cy = S // 2, 46 * SS
    m.ellipse([head_cx - head_r, head_cy - head_r,
               head_cx + head_r, head_cy + head_r], fill=255)
    sh_w, sh_top = 44 * SS, 94 * SS
    m.pieslice([head_cx - sh_w, sh_top, head_cx + sh_w, sh_top + sh_w * 1.2],
               start=180, end=360, fill=255)

    accent = Image.new("L", (S, S), 0)
    a = ImageDraw.Draw(accent)
    # One yaw arc, then mirror it: two identical arcs side by side read as
    # a wobble, a mirrored pair reads as turning.
    pad, span = head_r + 13 * SS, 15 * SS
    a.arc([head_cx - pad - span, head_cy - head_r - 6 * SS,
           head_cx - pad, head_cy + head_r + 8 * SS],
          start=-58, end=58, fill=255, width=3 * SS)
    # Union of the arc and its mirror, not a composite: composite() takes
    # image1 where the mask is white, so masking by the *mirrored* arc
    # keeps only positions where both agree - which is nowhere, and the
    # icon came out empty.
    accent = ImageChops.lighter(accent, accent.transpose(Image.FLIP_LEFT_RIGHT))

    combined = Image.new("RGB", (S, S), (0, 0, 0))
    combined.paste(Image.new("RGB", (S, S), INK), (0, 0), mark)
    combined.paste(Image.new("RGB", (S, S), ACCENT), (0, 0), accent)
    alpha = ImageChops.lighter(mark, accent)

    box = alpha.getbbox()
    if box is None:
        raise RuntimeError("the icon mark came out empty")
    cropped = combined.crop(box)
    cropped_alpha = alpha.crop(box)

    # Fit the mark into 84% of the tile, then centre it exactly.
    # The target is in *supersampled* pixels: cropped is SS times the final
    # size, so scaling against an unsupersampled target shrank the mark to
    # an eighth of the tile.
    fit = int(SIZE * 0.84 * SS)
    scale = min(fit / cropped.width, fit / cropped.height)
    size = (max(1, round(cropped.width * scale)),
            max(1, round(cropped.height * scale)))
    final = cropped.resize(size, Image.LANCZOS)
    final_alpha = cropped_alpha.resize(size, Image.LANCZOS)

    # Paste through the alpha so the background gradient shows through the
    # gaps; pasting the opaque layer would stamp a black rectangle.
    img.paste(final, ((S - final.width) // 2, (S - final.height) // 2),
              final_alpha)
    return img.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img = build()
    img.save(OUT, "PNG", optimize=True)
    print(f"{OUT.relative_to(ROOT)}  {img.size[0]}x{img.size[1]}  "
          f"{OUT.stat().st_size} bytes")


if __name__ == "__main__":
    main()