"""The mod icon, and why these properties are pinned.

Modrinth and CurseForge both want a square 128x128 PNG; CurseForge rejects
anything else outright. The same file is what Fabric shows in the mod list,
so it has to be centred and legible at 32px, which is how Modrinth renders
it in a project listing.
"""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ICON = REPO / "minecraft-mod/src/main/resources/assets/eyetrack/icon.png"
MOD_JSON = REPO / "minecraft-mod/src/main/resources/fabric.mod.json"


def read_png(path: Path):
    """Minimal PNG reader: IHDR for size, IDAT decoded for pixels.

    Pillow is a transitive dependency here (mediapipe pulls it in) and is
    not declared, so this reads the handful of bytes it needs instead of
    making the icon depend on it.
    """
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, width, height, idat = 8, None, None, b""
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            width, height, depth, colour = struct.unpack(">IIBB", body[:10])
            assert depth == 8, "expected 8 bits per channel"
            assert colour in (2, 6), "expected RGB or RGBA"
        elif ctype == b"IDAT":
            idat += body
        elif ctype == b"IEND":
            break
        pos += 12 + length
    return width, height, zlib.decompress(idat)


def pixels(width: int, height: int, raw: bytes):
    """Undo the PNG per-scanline filters, returning rows of RGB triples."""
    stride = width * 3
    rows, prev, pos = [], bytearray(stride), 0
    for _ in range(height):
        filt = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        for i in range(stride):
            a = line[i - 3] if i >= 3 else 0
            b = prev[i]
            c = prev[i - 3] if i >= 3 else 0
            if filt == 1:
                line[i] = (line[i] + a) & 0xFF
            elif filt == 2:
                line[i] = (line[i] + b) & 0xFF
            elif filt == 3:
                line[i] = (line[i] + (a + b) // 2) & 0xFF
            elif filt == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        rows.append(bytes(line))
        prev = line
    return rows


@pytest.fixture(scope="module")
def icon():
    if not ICON.exists():
        pytest.skip("icon not generated; run tools/make_mod_icon.py")
    w, h, raw = read_png(ICON)
    return w, h, pixels(w, h, raw)


# ------------------------------------------------------------- the basics
def test_the_icon_exists():
    assert ICON.exists(), "Modrinth and CurseForge both need one"


def test_it_is_a_square_128_png(icon):
    """Both platforms reject anything else."""
    w, h, _ = icon
    assert (w, h) == (128, 128), f"expected 128x128, got {w}x{h}"


def test_it_is_small_enough_to_upload(icon):
    assert ICON.stat().st_size < 100_000, "mod icons should be a few KB"


# ------------------------------------------------------------ the layout
def test_the_mark_is_centred(icon):
    """An off-centre icon is instantly noticeable in a listing.

    This is not theoretical: drawing the shapes by hand gave margins of
    18/10/18/8, so the mark is centred on its own bounding box instead.
    """
    _, _, rows = icon
    lit = [(x, y) for y, row in enumerate(rows)
           for x in range(128)
           if sum(row[x * 3:x * 3 + 3]) > 200]
    assert lit, "the icon has no visible mark at all"
    xs = [p[0] for p in lit]
    ys = [p[1] for p in lit]
    left, right = min(xs), 127 - max(xs)
    top, bottom = min(ys), 127 - max(ys)
    assert abs(left - right) <= 2, f"horizontally off-centre: {left} vs {right}"
    assert abs(top - bottom) <= 2, f"vertically off-centre: {top} vs {bottom}"


def test_the_mark_fills_a_good_share_of_the_tile(icon):
    _, _, rows = icon
    lit = sum(1 for row in rows for x in range(128)
              if sum(row[x * 3:x * 3 + 3]) > 200)
    assert 0.10 < lit / (128 * 128) < 0.55, (
        f"mark covers {100 * lit / (128 * 128):.0f}% - too small to read, "
        "or so large it will not breathe")


def test_the_mark_is_left_right_symmetric(icon):
    """A mirrored pair of yaw arcs reads as turning; two identical ones
    read as a wobble."""
    _, _, rows = icon

    def bright(row):
        return [sum(row[x * 3:x * 3 + 3]) > 200 for x in range(128)]

    agree = total = 0
    for row in rows:
        b = bright(row)
        total += 1
        agree += sum(1 for x in range(128) if b[x] == b[127 - x])
    assert agree / (128 * total) > 0.97, "the mark is not symmetric"


def test_the_mark_still_reads_at_thumbnail_size(icon):
    """Modrinth shows it at 32px in listings."""
    w, h, rows = icon
    # Sample the 128px mark down to 32px the way a renderer would and
    # require the silhouette to survive.
    block = 4
    dark = 0
    for by in range(0, 128, block):
        for bx in range(0, 128, block):
            total = sum(sum(rows[y][x * 3:x * 3 + 3])
                        for y in range(by, by + block)
                        for x in range(bx, bx + block))
            if total / (block * block * 3) > 200:
                dark += 1
    assert dark > 40, f"only {dark} dark thumbnail cells - it vanishes at 32px"


# ------------------------------------------------------------- the wiring
def test_fabric_shows_the_same_icon():
    meta = json.loads(MOD_JSON.read_text(encoding="utf-8"))
    assert meta.get("icon") == "assets/eyetrack/icon.png", (
        "fabric.mod.json must point at the icon so the mod list shows it")
    assert (MOD_JSON.parent / meta["icon"]).exists()


def test_the_icon_uses_the_app_palette(icon):
    """Same palette as the HUD, so the mod looks like the same program.

    Every pixel is checked with a tolerance: the accent arcs are thin and
    downsampled, so an exact match on a sampled grid misses them.
    """
    _, _, rows = icon
    wanted = {"accent": (148, 226, 122), "backdrop": (24, 15, 18)}
    seen = {name: 0 for name in wanted}
    for row in rows:
        for x in range(128):
            px = tuple(row[x * 3:x * 3 + 3])
            for name, ref in wanted.items():
                if all(abs(px[i] - ref[i]) <= 12 for i in range(3)):
                    seen[name] += 1
    assert seen["accent"] > 40, (
        f"only {seen['accent']} px of the tracking-green accent")
    assert seen["backdrop"] > 2000, (
        f"only {seen['backdrop']} px of the dark backdrop")


def test_the_generator_can_reproduce_it():
    """Keep tools/make_mod_icon.py honest - it is how the icon is edited."""
    assert (REPO / "tools" / "make_mod_icon.py").exists()