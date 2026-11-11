"""Pads of every part, in the job file's frame, for local (pad) alignment.

    python -m neojob.pads JOB.csv PNP.csv GERBER [-o JOB.csv.pads.json]

GERBER = the top copper file, a folder or a .zip of the Gerber set (read with neosetup's
parse_gerber: every pad flash with its size). PNP = the Altium Pick & Place CSV of the board.
The job's coordinates differ from Altium's (board turned, flipped, moved by the wizard), so the
Altium -> job transform is fitted on the designators both files share (rotation + shift, with or
without a mirror; the scale must come out 1 within 0.5 %). Each pad goes to the nearest part.

Output (JSON): {"fit": {...}, "parts": {designator: {"x", "y", "pads": [[x, y, w, h], ...]}}}
with x, y in the job frame (the comp line's X/Y frame) and w, h along the job's axes.
"""
import argparse
import csv
import io
import json
import math
import os
import sys

from .jobfile import load as load_job


def _num(s):
    s = str(s or "").strip().lower().replace("mm", "").replace("mil", "")
    try:
        return float(s)
    except ValueError:
        return None


def read_pnp(path):
    """Altium P&P CSV -> {designator: (x, y, rot, layer)} (mm)"""
    text = open(path, encoding="utf-8", errors="replace").read()
    i = text.find('"Designator"')
    if i < 0:
        i = text.find("Designator")
    rows = csv.DictReader(io.StringIO(text[i:]))
    out = {}
    for r in rows:
        r = {(k or "").strip(): v for k, v in r.items()}
        x = _num(r.get("Center-X(mm)") or r.get("Mid X") or r.get("Center-X(mil)") or r.get("X"))
        y = _num(r.get("Center-Y(mm)") or r.get("Mid Y") or r.get("Center-Y(mil)") or r.get("Y"))
        if x is None or y is None or not r.get("Designator"):
            continue
        if "Center-X(mil)" in r:
            x, y = x * 0.0254, y * 0.0254
        px, py = _num(r.get("Pad-X(mm)")), _num(r.get("Pad-Y(mm)"))
        out[r["Designator"].strip()] = (x, y, _num(r.get("Rotation")) or 0.0, (r.get("Layer") or "").strip(),
                                        (px, py) if px is not None and py is not None else None)
    return out


def read_pnp_rows(path):
    """as read_pnp, every row kept: [(designator, x, y, rot, layer, pad1)]"""
    text = open(path, encoding="utf-8", errors="replace").read()
    i = text.find('"Designator"')
    if i < 0:
        i = text.find("Designator")
    out = []
    for r in csv.DictReader(io.StringIO(text[i:])):
        r = {(k or "").strip(): v for k, v in r.items()}
        x = _num(r.get("Center-X(mm)") or r.get("Mid X") or r.get("Center-X(mil)") or r.get("X"))
        y = _num(r.get("Center-Y(mm)") or r.get("Mid Y") or r.get("Center-Y(mil)") or r.get("Y"))
        if x is None or y is None or not r.get("Designator"):
            continue
        if "Center-X(mil)" in r:
            x, y = x * 0.0254, y * 0.0254
        px, py = _num(r.get("Pad-X(mm)")), _num(r.get("Pad-Y(mm)"))
        out.append((r["Designator"].strip(), x, y, _num(r.get("Rotation")) or 0.0, (r.get("Layer") or "").strip(),
                    (px, py) if px is not None and py is not None else None))
    return out


def match_copies(job, rows, tol=0.6):
    """each job part -> its P&P row when designators repeat (a panel of boards): the turn / shift /
    mirror is found from two parts far apart, tried over their copies, and kept when the most parts
    land on a row of the same designator; then each part takes its nearest row. {comp key: row}"""
    by = {}
    for r in rows:
        by.setdefault(r[0], []).append(r)
    cs = [c for c in job.comps if c.name in by]
    if len(cs) < 3:
        return {}
    few = [c for c in cs if len(by[c.name]) <= 6 and sum(1 for q in cs if q.name == c.name) <= 6] or cs
    a = min(few, key=lambda c: (c.x + c.y))
    b = max(few, key=lambda c: math.dist((a.x, a.y), (c.x, c.y)) if c.name != a.name else -1)
    if b.name == a.name:
        return {}
    L = math.dist((a.x, a.y), (b.x, b.y))
    best = (0, None)
    for ra in by[a.name]:
        for rb in by[b.name]:
            if abs(math.dist(ra[1:3], rb[1:3]) - L) > 0.5:
                continue
            for mirror in (False, True):             # two points fit either way: both are tried
                f = fit_rigid([((-ra[1] if mirror else ra[1]), ra[2]), ((-rb[1] if mirror else rb[1]), rb[2])],
                              [(a.x, a.y), (b.x, b.y)], allow_mirror=False)
                f["mirror"] = mirror
                hit = 0
                for c in cs:
                    if any(math.dist(apply_fit(f, r[1], r[2]), (c.x, c.y)) < tol for r in by[c.name]):
                        hit += 1
                if hit > best[0]:
                    best = (hit, f)
    f = best[1]
    if f is None:
        return {}
    out, used = {}, set()
    for c in cs:
        r = min(by[c.name], key=lambda r: math.dist(apply_fit(f, r[1], r[2]), (c.x, c.y)))
        if math.dist(apply_fit(f, r[1], r[2]), (c.x, c.y)) < 3 * tol and id(r) not in used:
            out[c.key] = r
            used.add(id(r))
    return out


def gerber_offset(flashes, pad1s, step=0.05):
    """Gerber origin minus P&P origin: the shift that puts the most pad-1 points on a flash
    (as neosetup's register_gerber), refined by the mean of the matches. (ox, oy, matched)"""
    from collections import Counter
    votes = Counter()
    for px, py in pad1s[:300]:
        for fx, fy, _, _ in flashes:
            dx, dy = fx - px, fy - py
            if abs(dx) < 1000 and abs(dy) < 1000:
                votes[(round(dx / step), round(dy / step))] += 1
    if not votes:
        return 0.0, 0.0, 0
    (bx, by), _ = votes.most_common(1)[0]
    ox, oy = bx * step, by * step
    ds = []
    for px, py in pad1s:
        f = min(flashes, key=lambda f: (f[0] - ox - px) ** 2 + (f[1] - oy - py) ** 2)
        if (f[0] - ox - px) ** 2 + (f[1] - oy - py) ** 2 < 0.1 ** 2:
            ds.append((f[0] - px, f[1] - py))
    if ds:
        ox, oy = sum(d[0] for d in ds) / len(ds), sum(d[1] for d in ds) / len(ds)
    return ox, oy, len(ds)


def _engine():
    from neosetup import Project
    return Project().engine


def _pads_of(g, max_mm2=60.0, shaped=False):
    """flashes plus pad-sized dark regions (Altium writes some pads as regions) -> (x, y, w, h), the
    box around each pad. shaped: (x, y, w, h, angle, tw, th) with the pad's own rectangle too (a
    region's smallest turned rectangle: a pad turned 45 degrees is not its box; a flash: as its box)"""
    out = [tuple(f[:4]) for f in g["flashes"]]
    if shaped:
        out = _flash_shapes(g, out)
    for sh in g.get("shapes", []):
        if sh[0] == "P" and sh[2]:
            xs, ys = [p[0] for p in sh[1]], [p[1] for p in sh[1]]
            w, h = max(xs) - min(xs), max(ys) - min(ys)
            if 0 < w * h <= max_mm2:
                box = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, w, h)
                if shaped:
                    a, tw, th = _turned_rect(sh[1], w, h)
                    box = box + (a, tw, th)
                out.append(box)
    return out


def _flash_shapes(g, flashes):
    """each flash with its own turned rectangle: the parser keeps only the aperture's box, but it also
    draws the flash's outline (a turned rectangle, a rounded one as a polygon and circles); the dark
    shapes lying inside the flash's box are its outline -> (x, y, w, h, angle, tw, th)"""
    import math
    cell = 2.0
    grid = {}
    for sh in g.get("shapes", []):
        if not sh[-1]:
            continue
        if sh[0] == "P":
            pts = sh[1]
        elif sh[0] == "C":
            cx, cy, r = sh[1], sh[2], sh[3]
            pts = [(cx + r * math.cos(k * math.pi / 8), cy + r * math.sin(k * math.pi / 8)) for k in range(16)]
        else:
            continue
        if not pts:
            continue
        mx, my = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        grid.setdefault((int(mx // cell), int(my // cell)), []).append(pts)
    out = []
    for x, y, w, h in flashes:
        mine = []
        gx, gy = int(x // cell), int(y // cell)
        for i in (-1, 0, 1):
            for j in (-1, 0, 1):
                for pts in grid.get((gx + i, gy + j), ()):
                    if all(abs(px - x) <= w / 2 + 1e-3 and abs(py - y) <= h / 2 + 1e-3 for px, py in pts):
                        mine.extend(pts)
        a, tw, th = _turned_rect(mine, w, h) if len(mine) >= 3 else (0.0, w, h)
        out.append((x, y, w, h, a, tw, th))
    return out


def _turned_rect(pts, w, h):
    """(angle deg CCW, w, h) of the smallest rectangle around the polygon; (0, w, h) when that is
    the box itself (an axis-aligned pad)"""
    try:
        import cv2
        import numpy as np
        (cx, cy), (tw, th), a = cv2.minAreaRect(np.array(pts, np.float32))
    except Exception:
        return 0.0, w, h
    if tw * th >= 0.97 * w * h:
        return 0.0, w, h
    a = float(a) % 180.0
    if a > 90.0:
        a -= 180.0
    return round(a, 2), round(float(tw), 4), round(float(th), 4)


LAYER_ORDER = ("pads", "paste", "mask", "copper")      # Pad Master first: every pad, nothing else


def read_copper(path, side="top", shaped=False):
    """pads (x, y, w, h) of one side, mm, Gerber frame; from the first layer that has pads in
    the order Pad Master, Paste, Solder Mask, Copper. Returns (pads, layer file name). shaped: each
    pad with its own turned rectangle as well (see _pads_of)"""
    eng = _engine()
    if os.path.isfile(path) and not path.lower().endswith((".zip", ".rar")):
        name = os.path.basename(path)
        return _pads_of(eng["parse_gerber"](open(path, encoding="latin-1").read(), name), shaped=shaped), name
    files = eng["gerber_files_in"](path)
    by_kind = {}
    for name, text in files:
        ident = eng["identify_gerber_layer"](name, text)
        if ident and ident[2] == side:
            by_kind.setdefault(ident[3], (name, text))
    for kind in LAYER_ORDER:
        if kind in by_kind:
            name, text = by_kind[kind]
            pads = _pads_of(eng["parse_gerber"](text, name), shaped=shaped)
            if pads:
                return pads, name
    raise ValueError(f"no {side} pad / paste / mask / copper layer with pads in {path}")


def _excellon(text):
    """holes (x, y, diameter) mm from an Excellon drill file; None if the text is not one"""
    import re
    if "M48" not in text[:2000] and not re.search(r"^T\d+(?:F\d+)?(?:S\d+)?C[\d.]+", text, re.M):
        return None
    inch = bool(re.search(r"^\s*INCH", text, re.M)) or (";Units=Inch" in text)
    unit = 25.4 if inch else 1.0
    fmt = re.search(r"FILE_FORMAT=(\d):(\d)", text)
    intd, decd = (int(fmt.group(1)), int(fmt.group(2))) if fmt else ((2, 4) if inch else (3, 3))
    lz = "LZ" in text[:3000]                          # leading zeros kept: count from the left
    tools, holes, tool = {}, [], None

    def coord(v):
        if "." in v:
            return float(v) * unit
        neg = v.startswith("-")
        v = v.lstrip("+-")
        if lz:
            v = v.ljust(intd + decd, "0")
        else:
            v = v.rjust(intd + decd, "0")
        x = int(v) / 10 ** decd * unit
        return -x if neg else x
    x = y = 0.0
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"^T(\d+)(?:F\d+)?(?:S\d+)?C([\d.]+)", line)
        if m:
            tools[int(m.group(1))] = float(m.group(2)) * unit
            continue
        m = re.match(r"^T(\d+)$", line)
        if m:
            tool = int(m.group(1))
            continue
        m = re.match(r"^(?:X([+-]?[\d.]+))?(?:Y([+-]?[\d.]+))?$", line)
        if m and (m.group(1) or m.group(2)) and tool in tools:
            if m.group(1):
                x = coord(m.group(1))
            if m.group(2):
                y = coord(m.group(2))
            holes.append((x, y, tools[tool]))
    return holes


def read_drill(path):
    """every hole of the Excellon drill files in a folder or a .zip (Altium NC Drill output); [] if
    there is none (the BOARD-A outputs have only the drill report)"""
    import zipfile
    texts = []
    if os.path.isdir(path):
        for base, _d, files in os.walk(path):
            for fn in files:
                if fn.lower().endswith((".txt", ".drl", ".xln", ".exc", ".tx1", ".tx2", ".tx3")):
                    texts.append(open(os.path.join(base, fn), encoding="latin-1").read())
    elif os.path.isfile(path) and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if info.filename.lower().endswith((".txt", ".drl", ".xln", ".exc", ".tx1", ".tx2", ".tx3")):
                    texts.append(z.read(info).decode("latin-1", "replace"))
    holes = []
    for t in texts:
        h = _excellon(t)
        if h:
            holes += h
    return holes


def hole_between(pads, holes, fit=None):
    """a hole in the middle of a part's pads (a reverse-mount LED shines through it): (x, y, d)
    in the job frame or None. pads: [[x, y, w, h], ...] job frame; holes in the Gerber frame are
    turned by `fit` first."""
    if len(pads) < 2 or not holes:
        return None
    mx, my = sum(p[0] for p in pads) / len(pads), sum(p[1] for p in pads) / len(pads)
    gap = 2 * min(max(0.05, math.dist((p[0], p[1]), (mx, my)) - (p[2] if abs(p[0] - mx) > abs(p[1] - my) else p[3]) / 2)
                  for p in pads)
    best = None
    for hx, hy, d in holes:
        if fit:
            hx, hy = apply_fit(fit, hx, hy)
        off = math.dist((hx, hy), (mx, my))
        if off < 0.35 * gap and 0.4 * gap <= d <= 2.5 * gap and (best is None or off < best[3]):
            best = (hx, hy, d, off)
    return (round(best[0], 4), round(best[1], 4), best[2]) if best else None


def fiducial_candidates(flashes, used, fit, d_range=(0.6, 3.2), clear_mm=1.5):
    """round copper pads of no part with nothing else close: the board's fiducials, in the job
    frame [(x, y, d)]. flashes: (x, y, w, h, ...) Gerber frame (offset removed); used: set of
    their indices that belong to a part"""
    out = []
    for i, f in enumerate(flashes):
        x, y, w, h = f[:4]
        if i in used or not (d_range[0] <= w <= d_range[1]) or abs(w - h) > 0.05 * w:
            continue
        if any(j != i and math.dist((x, y), g[:2]) < clear_mm + max(g[2], g[3]) / 2 for j, g in enumerate(flashes)):
            continue
        jx, jy = apply_fit(fit, x, y)
        out.append((round(jx, 4), round(jy, 4), round(w, 3)))
    return out


def fit_rigid(src, dst, allow_mirror=True):
    """least-squares rotation + shift (and an optional mirror in X) src -> dst.
    Returns dict(theta, tx, ty, mirror, scale, rms, n)."""
    import numpy as np
    best = None
    for mirror in ((False, True) if allow_mirror else (False,)):
        a = np.array([((-x if mirror else x), y) for x, y in src], float)
        b = np.array(dst, float)
        ca, cb = a.mean(0), b.mean(0)
        A, B = a - ca, b - cb
        H = A.T @ B
        theta = math.atan2(H[0, 1] - H[1, 0], H[0, 0] + H[1, 1])
        c, s = math.cos(theta), math.sin(theta)
        R = np.array([[c, -s], [s, c]])
        t = cb - R @ ca
        res = b - (a @ R.T + t)
        rms = float(np.sqrt((res ** 2).sum(1).mean()))
        na, nb = np.sqrt((A ** 2).sum(1).mean()), np.sqrt((B ** 2).sum(1).mean())
        r = dict(theta=math.degrees(theta), tx=float(t[0]), ty=float(t[1]), mirror=mirror,
                 scale=float(nb / na) if na else 1.0, rms=rms, n=len(src))
        if best is None or r["rms"] < best["rms"]:
            best = r
    return best


def apply_fit(f, x, y):
    if f["mirror"]:
        x = -x
    t = math.radians(f["theta"])
    return math.cos(t) * x - math.sin(t) * y + f["tx"], math.sin(t) * x + math.cos(t) * y + f["ty"]


def build(job_path, pnp_path, gerber_path, max_assign_mm=12.0):
    job = load_job(job_path)
    rows = read_pnp_rows(pnp_path)
    names = [r[0] for r in rows]
    if len(set(names)) < len(names) or len({c.name for c in job.comps}) < len(job.comps):
        row = match_copies(job, rows)                  # a panel: the designators repeat
    else:
        by = {r[0]: r for r in rows}
        row = {c.key: by[c.name] for c in job.comps if c.name in by}
    # pnp: comp key -> (x, y, rot, layer, pad1), as read_pnp gives per designator
    pnp = {k: r[1:] for k, r in row.items()}
    pairs = [(pnp[c.key][:2], (c.x, c.y)) for c in job.comps if c.key in pnp]
    total = len(pairs)
    if len(pairs) < 3:
        raise ValueError(f"only {len(pairs)} designators of the job are in the P&P file")
    f = fit_rigid([p[0] for p in pairs], [p[1] for p in pairs])
    for _ in range(4):                        # drop rows that did not move with the board (fiducials)
        res = [math.dist(apply_fit(f, *a), b) for a, b in pairs]
        med = sorted(res)[len(res) // 2]
        keep = [p for p, r in zip(pairs, res) if r <= max(3 * med, 0.2)]
        if len(keep) == len(pairs) or len(keep) < 3:
            break
        pairs = keep
        f = fit_rigid([p[0] for p in pairs], [p[1] for p in pairs])
    f["dropped"] = sum(1 for c in job.comps if c.key in pnp) - len(pairs)
    if abs(f["scale"] - 1) > 0.005 or f["rms"] > 0.5 or len(pairs) < 0.8 * total:
        raise ValueError(f"the P&P file does not match the job (scale {f['scale']:.4f}, rms {f['rms']:.3f} mm)")
    shaped, layer = read_copper(gerber_path, "bottom" if f["mirror"] else "top", shaped=True)
    flashes = [p[:4] for p in shaped]
    side = [r[1:] for r in rows if r[5] and (("bottom" in r[4].lower()) == f["mirror"])]
    ox, oy, nm = gerber_offset(flashes, [v[4] for v in side])
    if nm < max(3, 0.3 * len(side)):
        raise ValueError(f"the Gerber does not line up with the P&P file ({nm} of {len(side)} pad-1 points on a pad)")
    flashes = [(x - ox, y - oy, w, h, a, tw, th) for x, y, w, h, a, tw, th in shaped]
    quarter = round(f["theta"] / 90) % 2 == 1                 # w/h swap when the board is turned 90
    # the same pad drawn twice (a flash and a region, as on the BOARD-A Gerber): keep one
    uniq = []
    for p in sorted(flashes, key=lambda p: -p[2] * p[3]):
        if not any(abs(p[0] - q[0]) < 0.05 and abs(p[1] - q[1]) < 0.05 for q in uniq):
            uniq.append(p)
    flashes = uniq
    # the parts of this job (the side being placed); not the P&P's Layer column: on the BOARD-A
    # board the job's parts say TopLayer while their pads are in the bottom Gerber files
    names = list(pnp)
    centres = [pnp[n][:2] for n in names]
    # a part's pads lie within its pad-1 distance from its centre (pad 1 is an outer pad of almost
    # every footprint); this keeps a neighbour's pads, vias and test points out
    reach = {}
    for n in names:
        p1 = pnp[n][4]
        reach[n] = 1.15 * math.dist(p1, pnp[n][:2]) + 0.3 if p1 else max_assign_mm
    parts = {}
    used = set()
    for k, (x, y, w, h, a, tw, th) in enumerate(flashes):
        best, bd = None, max_assign_mm
        for n, (cx, cy) in zip(names, centres):
            d = math.hypot(x - cx, y - cy)
            if d < bd and d <= max(reach[n], 0.5):
                best, bd = n, d
        if best is None:
            continue
        used.add(k)
        jx, jy = apply_fit(f, x, y)
        part = parts.setdefault(best, {"pads": [], "shapes": []})
        part["pads"].append([round(jx, 4), round(jy, 4)] + ([h, w] if quarter else [w, h]))
        # the pad's own rectangle in the job's frame: its angle turned (and mirrored) like the board
        ja = ((180.0 - a) if f["mirror"] else a) + f["theta"]
        part["shapes"].append([round((ja + 90.0) % 180.0 - 90.0, 2), tw, th])
    holes = [(x - ox, y - oy, d) for x, y, d in read_drill(gerber_path)]
    for n, p in parts.items():
        p["x"], p["y"] = [round(v, 4) for v in apply_fit(f, *pnp[n][:2])]
        hb = hole_between(p["pads"], holes, f)
        if hb:                                          # pad alignment centres it on the hole
            p["hole"] = list(hb)
    return {"job": os.path.basename(job_path), "pnp": os.path.basename(pnp_path), "gerber": layer,
            "gerber_offset": [round(ox, 4), round(oy, 4), nm], "fit": f, "parts": parts,
            "holes": len(holes), "fiducials": [list(c) for c in fiducial_candidates(flashes, used, f)],
            "keepout": keepout_of(gerber_path, "bottom" if f["mirror"] else "top", ox, oy, f)}


def keepout_of(gerber_path, side, ox, oy, f):
    """where a nozzle must not touch the board, in the job frame: every opening of the side's solder mask
    (pads, holes, test points; a hole has a mask opening round it) and every drilled hole -> [[x, y, w, h]]"""
    eng = _engine()
    out = []
    files = ([(os.path.basename(gerber_path), open(gerber_path, encoding="latin-1").read())]
             if os.path.isfile(gerber_path) and not gerber_path.lower().endswith((".zip", ".rar")) else eng["gerber_files_in"](gerber_path))
    for name, text in files:
        ident = eng["identify_gerber_layer"](name, text)
        if ident and ident[2] == side and ident[3] == "mask":
            for x, y, w, h in _pads_of(eng["parse_gerber"](text, name), max_mm2=400.0):
                jx, jy = apply_fit(f, x - ox, y - oy)
                out.append([round(jx, 3), round(jy, 3), round(w, 3), round(h, 3)])
    for x, y, d in read_drill(gerber_path):
        jx, jy = apply_fit(f, x - ox, y - oy)
        out.append([round(jx, 3), round(jy, 3), round(d, 3), round(d, 3)])
    uniq = {}
    for k in out:
        uniq.setdefault((round(k[0], 2), round(k[1], 2)), k)
    return list(uniq.values())


def add_keepout(job_path, gerber_path):
    """the keep-out (keepout_of) added to a pads file built before it had one; its number"""
    d = load_pads(job_path)
    if not d:
        raise KeyError("no pads file for this job")
    ox, oy = d["gerber_offset"][:2]
    d["keepout"] = keepout_of(gerber_path, "bottom" if d["fit"]["mirror"] else "top", ox, oy, d["fit"])
    tmp = pads_path(job_path) + ".tmp"
    json.dump(d, open(tmp, "w", encoding="utf-8"))
    os.replace(tmp, pads_path(job_path))
    return len(d["keepout"])


def pads_path(job_path):
    return job_path + ".pads.json"


def load_pads(job_path):
    p = pads_path(job_path)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("job"); ap.add_argument("pnp"); ap.add_argument("gerber")
    ap.add_argument("-o", "--out")
    a = ap.parse_args(argv)
    d = build(a.job, a.pnp, a.gerber)
    out = a.out or pads_path(a.job)
    json.dump(d, open(out, "w", encoding="utf-8"), indent=1)
    f = d["fit"]
    print(f"{out}: {len(d['parts'])} parts, {sum(len(p['pads']) for p in d['parts'].values())} pads; "
          f"Altium -> job: {f['theta']:.3f} deg{' mirrored' if f['mirror'] else ''}, rms {f['rms']:.3f} mm on {f['n']} parts")


if __name__ == "__main__":
    sys.exit(main())
