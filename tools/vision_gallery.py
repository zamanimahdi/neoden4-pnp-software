"""The README's vision pictures, drawn from the sample camera pictures in tests/data/bulk with what the
vision finds in them (python tools/vision_gallery.py -> docs/images/vision_*.png).

The PNGs are written with only their image chunks (IHDR, IDAT, IEND): no date, software, size or any
other metadata."""
import os
import struct
import sys
import zlib

import cv2
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from neocam.bulk import analyze, analyze_bottom  # noqa: E402
from neojob.bulkfeed import find_body_rects  # noqa: E402

D = os.path.join(ROOT, "tests", "data", "bulk")
OUT = os.path.join(ROOT, "docs", "images")
K, KU = 115.2, 32.14                  # head camera / up camera px per mm
GREEN, ORANGE, CYAN, WHITE = (90, 220, 90), (40, 160, 255), (230, 200, 40), (240, 240, 240)


def png(path, bgr):
    """an RGB PNG with nothing but IHDR, IDAT and IEND"""
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    raw = b"".join(b"\0" + rgb[y].tobytes() for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def label(img, lines, x=8, y=22):
    for i, t in enumerate(lines):
        for c, th in ((0, 4), (WHITE, 1)):
            cv2.putText(img, t, (x, y + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.6, c, th, cv2.LINE_AA)


def head_parts():
    """one part centred under the head camera: body, angle, leads per side and pitch"""
    tiles = []
    for i, name in ((15, "SOIC-8"), (2, "TSSOP-20"), (5, "SOT-23-5"), (6, "TO-277")):
        g = cv2.imread(os.path.join(D, f"c{i:02d}.png"), 0)
        p = analyze(g, K)
        img = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
        box = cv2.boxPoints(((p.cx, p.cy), (p.L * K, p.W * K), -p.angle)).astype(np.int32)
        cv2.polylines(img, [box], True, GREEN, 3, cv2.LINE_AA)
        cv2.drawMarker(img, (int(p.cx), int(p.cy)), ORANGE, cv2.MARKER_CROSS, 40, 3)
        img = cv2.resize(img, (420, 420), interpolation=cv2.INTER_AREA)
        label(img, [name, f"body {p.L:.2f} x {p.W:.2f} mm, {p.angle:+.1f} deg"]
              + ([f"leads {p.leads[0]} + {p.leads[1]}, pitch {p.pitch:.2f} mm"] if p.leads[0] + p.leads[1] else []))
        tiles.append(img)
    return np.hstack(tiles)


def up_parts():
    """a part held on the nozzle, seen from below (up camera): every piece of metal on its underside"""
    tiles = []
    for i, name in ((15, "SOIC-8 + pad"), (5, "SOT-23-5"), (6, "TO-277")):
        g = cv2.imread(os.path.join(D, f"b{i:02d}.png"), 0)
        b = analyze_bottom(g, KU)
        img = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
        H, W = g.shape
        cx, cy = getattr(b, "cx", W / 2), getattr(b, "cy", H / 2)
        a = np.radians(-getattr(b, "angle", 0.0))
        for u, v, _ar, w, h in b.blobs:                  # pattern frame, mm -> picture px
            x = cx + (u * np.cos(a) - v * np.sin(a)) * KU
            y = cy + (u * np.sin(a) + v * np.cos(a)) * KU
            box = cv2.boxPoints(((x, y), (max(w, 0.2) * KU, max(h, 0.2) * KU), np.degrees(a))).astype(np.int32)
            cv2.polylines(img, [box], True, CYAN, 2, cv2.LINE_AA)
        img = cv2.resize(img, (420, 420), interpolation=cv2.INTER_AREA)
        label(img, [name, f"{len(b.blobs)} metal pieces found"])
        tiles.append(img)
    return np.hstack(tiles)


def packed_pocket():
    """a bulk pocket packed full (the stitched scan, 1/4 size): every whole body found alone"""
    tiles = []
    for cid, name in ((19, "SOIC-16"), (20, "ULN2803 (SOIC-18)")):
        g = cv2.imread(os.path.join(D, f"pocket_mos{cid}.png"), 0)
        img = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
        bs = [b for b in find_body_rects(g, K * 0.25) if not (b["cut"] and b["odd"])]
        for b in find_body_rects(g, K * 0.25):
            ok = not (b["cut"] and b["odd"])
            cv2.polylines(img, [np.array(b["box"], np.int32)], True, GREEN if ok else ORANGE, 3, cv2.LINE_AA)
        s = 640 / img.shape[0]
        img = cv2.resize(img, (int(img.shape[1] * s), 640), interpolation=cv2.INTER_AREA)
        label(img, [name, f"{len(bs)} whole bodies to pick"])
        tiles.append(img)
    return np.hstack(tiles)


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, img in (("vision_head", head_parts()), ("vision_up", up_parts()), ("vision_pocket", packed_pocket())):
        png(os.path.join(OUT, name + ".png"), img)
        print(name, img.shape[1], "x", img.shape[0])


if __name__ == "__main__":
    main()
