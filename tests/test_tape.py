"""neocam.tape: pockets and sprocket holes of a synthetic paper tape under the head camera"""
import cv2
import numpy as np

from neocam.tape import find_pockets, annotate

K = 115.0


def tape(shift=(0.3, -0.2)):
    img = np.full((800, 800), 190, np.uint8)
    c = (400 + shift[0] * K, 400 - shift[1] * K)
    for dy in (-4, 0, 4):                                  # 0603 pockets, 4 mm apart along Y
        y = c[1] + dy * K
        cv2.rectangle(img, (int(c[0] - 0.5 * K), int(y - 0.9 * K)), (int(c[0] + 0.5 * K), int(y + 0.9 * K)), 30, -1)
    for dy in (-2, 2):                                     # sprocket holes 3.5 mm to the side
        cv2.circle(img, (int(c[0] - 3.0 * K), int(c[1] + dy * K)), int(0.75 * K), 20, -1)
    return img, c


def test_nearest_pocket_first():
    img, c = tape()
    f = find_pockets(img, K)
    p = f[0]
    assert p["kind"] == "pocket" and p["dark"]
    assert abs(p["px"] - c[0]) < 2 and abs(p["py"] - c[1]) < 2
    assert abs(min(p["w"], p["h"]) - 1.0) < 0.05 and abs(max(p["w"], p["h"]) - 1.8) < 0.05
    assert any(q["kind"] == "hole" for q in f)
    assert p["full"] is False                              # a dark empty hole
    assert annotate(img, f, K).shape == (800, 800, 3)


def test_part_in_pocket_is_also_found():
    img, c = tape((0, 0))
    cv2.rectangle(img, (int(c[0] - 0.3 * K), int(c[1] - 0.6 * K)), (int(c[0] + 0.3 * K), int(c[1] + 0.6 * K)), 250, -1)
    f = find_pockets(img, K)
    bright = [q for q in f if q["kind"] == "pocket" and not q["dark"]]
    assert bright and abs(bright[0]["px"] - c[0]) < 2 and abs(bright[0]["py"] - c[1]) < 2
    assert bright[0]["full"]                               # the part in it
    assert not any(q["kind"] == "pocket" and q["dark"] and abs(q["px"] - c[0]) < 2 and abs(q["py"] - c[1]) < 2 for q in f)
