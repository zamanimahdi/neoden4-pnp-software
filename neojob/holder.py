"""A bulk-feeder holder: one plate with many pockets, each a bulk feeder. Its drawing is a TEMPLATE
(the pockets and the reference marks in the plate's own mm, origin its lower-left corner); where the
plate lies on the table is found from TWO of its marks seen with the head camera, so the plate may lie
anywhere and turned. Each pocket then becomes the bulk feeder of its number (neojob.bulkfeed).

    templates(data_dir)                 the built-in ones + data_dir/bulk_holders/*.json
    place(tpl, ma, pa, mb, pb, margin)  the plate's place from marks ma / mb seen at pa / pb (head camera mm)
                                        -> {angle, error, pockets: [{n, x0, y0, x1, y1, corners}], marks, outline}

A bulk feeder's area is a rectangle along the machine's axes: of a turned pocket the largest such
rectangle about its centre is taken, less `margin` from each wall (a part against the wall is not seen whole).
"""
import json
import math
import os


def _pp20():
    # "PP feeder" (the user's drawing, 2026-11): plate 300 x 100, two rows of 10 pockets 46 mm high, 25 mm
    # wide at a 27 mm step, the last of each row 37 mm; numbered 1 .. 10 in the upper row, 11 .. 20 below
    ps = []
    for row, y in ((0, 51.0), (1, 3.0)):
        for i in range(10):
            ps.append({"n": row * 10 + i + 1, "x": round(9.8526 + 27.0 * i, 4), "y": y, "w": 37.0 if i == 9 else 25.0, "h": 46.0})
    return {"name": "PP feeder 20", "w": 300.0, "h": 100.0, "pockets": ps, "ref": ["L", "R"],
            "marks": [{"name": "L", "label": "left hole (middle)", "x": 4.755, "y": 50.0, "d": 2.0},
                      {"name": "R", "label": "right hole (middle)", "x": 295.0, "y": 50.0, "d": 2.0},
                      {"name": "L2", "label": "left hole (lower)", "x": 4.755, "y": 20.267, "d": 2.0},
                      # the pockets' own inside corners: seen on every build of the plate (on the one of
                      # 2026-11-19 the plate ends 0.7 mm outside the pockets and has no holes at all)
                      {"name": "P11", "label": "pocket 11: inside lower-left corner", "x": 9.8526, "y": 3.0, "d": 1.5},
                      {"name": "P20", "label": "pocket 20: inside lower-right corner", "x": 289.8526, "y": 3.0, "d": 1.5},
                      {"name": "P1", "label": "pocket 1: inside upper-left corner", "x": 9.8526, "y": 97.0, "d": 1.5},
                      {"name": "P10", "label": "pocket 10: inside upper-right corner", "x": 289.8526, "y": 97.0, "d": 1.5},
                      {"name": "C1", "label": "corner hole lower-left", "x": 5.0, "y": 5.0, "d": 3.0},
                      {"name": "C2", "label": "corner hole lower-right", "x": 295.0, "y": 5.0, "d": 3.0},
                      {"name": "C3", "label": "corner hole upper-left", "x": 5.0, "y": 95.0, "d": 3.0},
                      {"name": "C4", "label": "corner hole upper-right", "x": 295.0, "y": 95.0, "d": 3.0}]}


BUILTIN = [_pp20()]


def check(t):
    """a template read from a file: complete, the pockets inside the plate and apart"""
    for k in ("name", "w", "h", "pockets", "marks"):
        if k not in t:
            raise ValueError(f"holder template: no '{k}'")
    ns = [int(p["n"]) for p in t["pockets"]]
    if len(set(ns)) != len(ns) or not ns:
        raise ValueError("holder template: pocket numbers must differ")
    for p in t["pockets"]:
        if p["w"] <= 0 or p["h"] <= 0 or p["x"] < 0 or p["y"] < 0 or p["x"] + p["w"] > t["w"] or p["y"] + p["h"] > t["h"]:
            raise ValueError(f"holder template: pocket {p['n']} is not inside the plate")
    if len({m["name"] for m in t["marks"]}) < 2:
        raise ValueError("holder template: two marks at least")
    return t


def templates(data_dir=None):
    out = [dict(t) for t in BUILTIN]
    d = os.path.join(data_dir, "bulk_holders") if data_dir else None
    if d and os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith(".json"):
                try:
                    t = check(json.load(open(os.path.join(d, f), encoding="utf-8")))
                except (OSError, ValueError, KeyError, TypeError):
                    continue
                out = [x for x in out if x["name"] != t["name"]] + [t]
    return out


def template(name, data_dir=None):
    t = next((x for x in templates(data_dir) if x["name"] == name), None)
    if not t:
        raise ValueError(f"no holder template '{name}'")
    return t


def place(tpl, ma, pa, mb, pb, margin=1.5, max_error=1.0, max_skew=10.0, reach=None, min_side=5.0):
    """the plate on the table from mark `ma` seen at pa and mark `mb` at pb (x, y mm of the head camera).
    reach = (x0, y0, x1, y1): where the head camera's centre can stand. A part is looked at centred before
    it is picked, so a pocket's area ends there: "clipped" = the pockets cut short, "unreachable" = those
    with less than min_side mm left (not made)."""
    marks = {m["name"]: m for m in tpl["marks"]}
    if ma not in marks or mb not in marks or ma == mb:
        raise ValueError("two different marks of the template")
    A, B = marks[ma], marks[mb]
    dt = (B["x"] - A["x"], B["y"] - A["y"])
    dm = (pb[0] - pa[0], pb[1] - pa[1])
    lt, lm = math.hypot(*dt), math.hypot(*dm)
    if lt < 20:
        raise ValueError("these two marks are too near each other for the angle")
    err = lm - lt
    if abs(err) > max_error:
        raise ValueError(f"the two marks are {lm:.2f} mm apart on the table, {lt:.2f} mm on the drawing "
                         f"({err:+.2f} mm): not these marks, or not this holder")
    th = math.atan2(dm[1], dm[0]) - math.atan2(dt[1], dt[0])
    c, s = math.cos(th), math.sin(th)
    # the middle of the two marks goes to the middle of the two points seen (their error shared)
    tm = ((A["x"] + B["x"]) / 2, (A["y"] + B["y"]) / 2)
    pm = ((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2)

    def to(x, y):
        x, y = x - tm[0], y - tm[1]
        return pm[0] + c * x - s * y, pm[1] + s * x + c * y

    deg = math.degrees(th)
    quarter = round(deg / 90.0)
    skew = deg - 90.0 * quarter                       # the turn left after whole quarter turns
    if abs(skew) > max_skew:
        raise ValueError(f"the holder lies {skew:+.1f} deg askew: lay it along the machine's axes (within {max_skew:g} deg)")
    cs, sn = math.cos(math.radians(skew)), abs(math.sin(math.radians(skew)))
    pockets, clipped, unreachable = [], [], []
    for p in sorted(tpl["pockets"], key=lambda p: int(p["n"])):
        w, h = (p["h"], p["w"]) if quarter % 2 else (p["w"], p["h"])
        den = cs * cs - sn * sn
        a = (w / 2 * cs - h / 2 * sn) / den - margin   # half-sizes of the rectangle along the axes inside it
        b = (h / 2 * cs - w / 2 * sn) / den - margin
        if a <= 0.5 or b <= 0.5:
            raise ValueError(f"pocket {p['n']}: nothing left inside with a {margin:g} mm margin at {skew:+.1f} deg")
        cx, cy = to(p["x"] + p["w"] / 2, p["y"] + p["h"] / 2)
        corners = [[round(v, 3) for v in to(x, y)] for x, y in
                   ((p["x"], p["y"]), (p["x"] + p["w"], p["y"]), (p["x"] + p["w"], p["y"] + p["h"]), (p["x"], p["y"] + p["h"]))]
        r = [cx - a, cy - b, cx + a, cy + b]
        if reach:
            full = list(r)
            r = [max(r[0], reach[0]), max(r[1], reach[1]), min(r[2], reach[2]), min(r[3], reach[3])]
            if r[2] - r[0] < min_side or r[3] - r[1] < min_side:
                unreachable.append({"n": int(p["n"]), "corners": corners})
                continue
            if any(abs(u - v) > 1e-9 for u, v in zip(r, full)):
                clipped.append(int(p["n"]))
        pockets.append({"n": int(p["n"]), "x0": round(r[0], 3), "y0": round(r[1], 3), "x1": round(r[2], 3), "y1": round(r[3], 3),
                        "corners": corners})
    return {"template": tpl["name"], "angle": round(deg, 3), "error": round(err, 3), "margin": margin,
            "ref": [{"mark": ma, "x": round(pa[0], 3), "y": round(pa[1], 3)}, {"mark": mb, "x": round(pb[0], 3), "y": round(pb[1], 3)}],
            "pockets": pockets, "clipped": clipped, "unreachable": unreachable,
            "marks": [{"name": m["name"], "x": round(to(m["x"], m["y"])[0], 3), "y": round(to(m["x"], m["y"])[1], 3)} for m in tpl["marks"]],
            "outline": [[round(v, 3) for v in to(x, y)] for x, y in ((0, 0), (tpl["w"], 0), (tpl["w"], tpl["h"]), (0, tpl["h"]))]}
