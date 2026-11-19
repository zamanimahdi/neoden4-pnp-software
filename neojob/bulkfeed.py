"""Bulk feeders: loose parts lying in a container on the table, found and checked with the cameras.

Up to 30 containers, defined once on the machine (Proc's data folder, bulk.json): two corners of the
area the parts lie in (head-camera coordinates), the package template the parts are matched with,
whether the pin-1 mark is used, the up-camera exposure for the underside, the depth the nozzle met the
parts at (learnt). A job's special feeder (a tray stack) uses a container by its number (the stack's
"bulk" field).

Templates (bulk_templates/<name>.json): one part taught lying the right way round, from the top (body,
leads, pin 1: neocam.bulk.teach) and from below (the metal pattern: neocam.bulk.teach_bottom).

The flow for each part:
1. the container is scanned (a grid of head pictures, stitched): the dark bodies on its floor; when
   none is left the operator is asked to refill (the job waits);
2. the next part is looked at alone, centred under the head camera: is it there, is it the package,
   which way round (leads / pin 1 / picture), not touching another;
3. it is picked by the vacuum: down in small steps until the tip seals, then a little deeper;
4. from below (neojob.camvision): every lead / pad there (else the trash), the exact angle and centre.

    bf = BulkFeeder(data_dir, vis, mb, cfg, log)
    bf.containers()                    # [{id, name, x0, y0, x1, y1, package, pin1, exp_bottom, z_pick, ...}]
    bf.scan(cid)                       # -> parts found (machine coordinates of the camera)
    bf.next_part(cid)                  # -> (x, y, phi_top, part) or None (empty)
"""
import base64
import json
import math
import os
import time

import cv2
import numpy as np

from neocam import bulk as BK

HEAD, UP = 1, 0
MAX = 30
STEP_MM = 7.0                     # the scan grid when the picture's size is not known
HEAD_PX = 1024                    # the head camera's picture as the job sets it
SCAN_PAD_MM = 1.5                 # the scan's pictures reach this far past the feeder's area (a holder's margin:
                                  # up to the pocket's wall, so a body lying against it is seen whole)
OVERLAP_MM = 0.8                  # two neighbouring scan pictures share this much (the stitching's seam)


def scan_grid(x0, x1, y0, y1, fov):
    """where the scan's pictures are taken: the fewest that cover x0 .. x1, y0 .. y1 with pictures `fov` mm
    wide and high, neighbours sharing OVERLAP_MM; a snake from the top row down, so the moves are short.
    The pictures' middles stand in from the area's edge by half a picture: nothing outside it is taken."""
    step = max(2.0, fov - OVERLAP_MM)

    def axis(a, b):
        n = max(1, int(math.ceil((b - a - fov) / step - 1e-9)) + 1) if b - a > fov else 1
        if n == 1:
            return [(a + b) / 2]
        return [a + fov / 2 + (b - a - fov) * i / (n - 1) for i in range(n)]
    xs, ys = axis(x0, x1), axis(y0, y1)
    return [(x, y) for j, y in enumerate(reversed(ys)) for x in (xs if j % 2 == 0 else xs[::-1])], xs, ys


def wrap(a):
    return (a + 180.0) % 360.0 - 180.0


# ---------------------------------------------------------------- templates on disk
def template_to_json(top, bottom):
    t = dict(top) if top else {}
    if t.get("crop") is not None:
        ok, png = cv2.imencode(".png", t["crop"])
        t["crop"] = base64.b64encode(png.tobytes()).decode()
    if t.get("profile") is not None:
        t["profile"] = [round(float(v), 4) for v in t["profile"]]
    if isinstance(t.get("pin1"), dict) and t["pin1"].get("corner"):
        t["pin1"] = dict(t["pin1"], corner=list(t["pin1"]["corner"]))
    return {"top": t, "bottom": bottom}


def template_from_json(d):
    t = dict(d.get("top") or {})
    if t.get("crop"):
        t["crop"] = cv2.imdecode(np.frombuffer(base64.b64decode(t["crop"]), np.uint8), cv2.IMREAD_GRAYSCALE)
    if t.get("profile") is not None:
        t["profile"] = np.array(t["profile"], np.float32)
    if isinstance(t.get("pin1"), dict) and t["pin1"].get("corner"):
        t["pin1"] = dict(t["pin1"], corner=tuple(t["pin1"]["corner"]))
    return t, d.get("bottom")


LEVELS = (0.55, 0.7, 0.82, 0.9)

def _pieces(d, sub, level, k):
    """the bodies of one dark area: its cores (deeper than level x the deepest point) grown back by what was
    taken off, each pixel to its nearest core -> [(mask, core depth)]"""
    dmax = float(d.max())
    r = level * dmax
    m, pl, ps, _ = cv2.connectedComponentsWithStats((d > r).astype(np.uint8))
    keep = [j for j in range(1, m) if ps[j, 4] >= (0.35 * k) ** 2]
    if not keep:
        return []
    cores = np.isin(pl, keep)
    dist, near = cv2.distanceTransformWithLabels((~cores).astype(np.uint8), cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL)
    # the label image of distanceTransformWithLabels numbers the zero pixels: map them to the cores
    lut = np.zeros(int(near.max()) + 1, np.int32)
    lut[near[cores]] = pl[cores]
    owner = lut[near]
    out = []
    for j in keep:
        body = ((owner == j) & (dist <= r + 1.5) & sub).astype(np.uint8)
        out.append(body)
    return out

def _rect(body, k):
    cs = cv2.findContours(body, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
    if not cs:
        return None
    cc = max(cs, key=cv2.contourArea)
    (cx, cy), (a, b), ang = cv2.minAreaRect(cc)
    if min(a, b) < 1:
        return None
    return {"cx": cx, "cy": cy, "a": a, "b": b, "ang": ang, "L": max(a, b) / k, "W": min(a, b) / k,
            "fill": float(body.sum()) / (a * b), "area": float(body.sum()) / (k * k)}

def find_body_rects(mosaic, k, floor_frac=0.45, size=None):
    """the dark bodies on a container's floor in a picture (k px / mm), also where they lie packed: against
    each other, against the pocket's wall, leads into leads. A dark area is taken apart by depth: its cores
    (deeper inside than a share of its deepest point: a wall or a bridge of shadow is thinner than a body)
    are each grown back to their body. A piece that is not a clean rectangle, or is longer / wider than the
    pocket's own part (`size` = (L, W) mm when taught, else what most clean bodies of this picture measure),
    is taken apart again one level deeper. One still too long with the right width runs on end-to-end into
    a wall or a neighbour: it is cut to the part's length from its free end ("snapped"). The marking on a
    body (bright letters, holes) is filled first. "cut": the body reaches the picture's edge; "odd": its size is not the pocket's own (or none is known).
    Returns [{"cx", "cy" (px), "L", "W" (mm), "angle" (deg, cv2), "box" (4 corners px), "cut", "snapped"}]"""
    floor = float(np.percentile(mosaic, 70))
    g = cv2.GaussianBlur(mosaic, (5, 5), 0)
    dark = (g < floor_frac * floor).astype(np.uint8)
    dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    cs, hier = cv2.findContours(dark, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    for cc, hh in zip(cs, hier[0] if hier is not None else []):
        if hh[3] != -1 and cv2.contourArea(cc) < 6 * k * k:
            cv2.drawContours(dark, [cc], -1, 1, -1)
    H, W = dark.shape
    n, lab, st, _ = cv2.connectedComponentsWithStats(dark)
    cands = []                                   # (rect, body mask, x, y, level index)
    def clean(r, ref):
        if r is None or r["L"] > 15 or r["fill"] < 0.84:
            return False
        return ref is None or (r["L"] <= 1.1 * ref[0] and r["W"] <= 1.12 * ref[1])
    def split(sub, x, y, li, ref, out):
        d = cv2.distanceTransform(cv2.copyMakeBorder(sub.astype(np.uint8), 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0), cv2.DIST_L2, 5)[1:-1, 1:-1]
        for body in _pieces(d, sub, LEVELS[li], k):
            if body.sum() < 0.5 * k * k:
                continue
            r = _rect(body, k)
            if clean(r, ref) or li + 1 >= len(LEVELS):
                if r is not None:
                    out.append((r, x, y, li))
            else:
                split(body.astype(bool), x, y, li + 1, ref, out)
    ref = (max(size), min(size)) if size else None
    for rnd in range(2):
        cands = []
        for i in range(1, n):
            if st[i, 4] < 0.5 * k * k:
                continue
            x, y, w, h = st[i, 0], st[i, 1], st[i, 2], st[i, 3]
            split(lab[y:y + h, x:x + w] == i, x, y, 0, ref, cands)
        good = [(r["L"], r["W"]) for r, x, y, _ in cands if r["fill"] >= 0.84 and r["L"] <= 15 and r["L"] / max(r["W"], 0.1) <= 6
                and min(r["cx"] + x, r["cy"] + y) > max(r["a"], r["b"]) / 2 + 3 and r["cx"] + x < W - max(r["a"], r["b"]) / 2 - 3 and r["cy"] + y < H - max(r["a"], r["b"]) / 2 - 3]
        if ref is not None or len(good) < 3:
            break
        ref = (float(np.median([g[0] for g in good])), float(np.median([g[1] for g in good])))   # the pocket's own part
    out = []
    for r, x, y, li in cands:
        if r["L"] > 15 or r["W"] < 0.5 or r["L"] / max(r["W"], 0.1) > 6:
            continue
        cx, cy = r["cx"] + x, r["cy"] + y
        snapped = False
        if ref and r["L"] > 1.06 * ref[0] and r["W"] <= 1.12 * ref[1]:
            # still too long, its width right: one end runs on into a wall or a neighbour (a butt joint).
            # The end with the floor beyond it is the body's own: the body is its length from there
            a, b = r["a"], r["b"]
            th = math.radians(r["ang"] + (0 if a >= b else 90))
            ux, uy = math.cos(th), math.sin(th)
            half, wid = max(a, b) / 2, min(a, b)
            def beyond(sgn):
                vals = []
                for t in np.linspace(-0.35, 0.35, 9):
                    px = cx + sgn * (half + 0.35 * k) * ux - t * wid * uy
                    py = cy + sgn * (half + 0.35 * k) * uy + t * wid * ux
                    if 0 <= int(py) < H and 0 <= int(px) < W:
                        vals.append(float(g[int(py), int(px)]))
                return float(np.mean(vals)) if vals else 0.0
            e1, e2 = beyond(+1), beyond(-1)
            sgn = 1 if e1 >= e2 else -1
            Lr = ref[0] * k
            cx, cy = cx + sgn * (half - Lr / 2) * ux, cy + sgn * (half - Lr / 2) * uy
            r = dict(r, L=ref[0], a=Lr if a >= b else a, b=b if a >= b else Lr)
            snapped = True
        box = cv2.boxPoints(((cx, cy), (r["a"], r["b"]), r["ang"]))
        edge = bool((box[:, 0].min() < 2) or (box[:, 1].min() < 2) or (box[:, 0].max() > W - 3) or (box[:, 1].max() > H - 3))
        out.append({"cx": float(cx), "cy": float(cy), "L": round(r["L"], 2), "W": round(r["W"], 2), "angle": round(float(r["ang"]), 1),
                    "box": box.tolist(), "cut": edge, "snapped": snapped,
                    "odd": bool(ref is None or abs(r["L"] - ref[0]) > 0.12 * ref[0] or abs(r["W"] - ref[1]) > 0.12 * ref[1]), "fill": round(r["fill"], 2), "level": li})
    return out


def find_bodies(mosaic, k, floor_frac=0.45):
    """the dark bodies on a container's floor (touching ones split): [(cx, cy, L, W, angle)] px / mm / deg"""
    return [(b["cx"], b["cy"], b["L"], b["W"], b["angle"]) for b in find_body_rects(mosaic, k, floor_frac)]


class BulkFeeder:
    def __init__(self, data_dir, vis, mb, cfg, log=print):
        self.dir, self.vis, self.mb, self.cfg, self.log = data_dir, vis, mb, cfg, log
        self.path = os.path.join(data_dir, "bulk.json")
        self.tdir = os.path.join(data_dir, "bulk_templates")
        self.queue = {}                      # container -> parts still to try from the last scan
        self._tcache = {}

    # ---- containers and templates ----
    def containers(self):
        try:
            return json.load(open(self.path, encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def container(self, cid):
        return next((c for c in self.containers() if int(c["id"]) == int(cid)), None)

    def save_container(self, c):
        cs = [x for x in self.containers() if int(x["id"]) != int(c["id"])]
        if not 1 <= int(c["id"]) <= MAX:
            raise ValueError(f"a bulk feeder is 1 .. {MAX}")
        cs.append(c)
        cs.sort(key=lambda x: int(x["id"]))
        json.dump(cs, open(self.path, "w", encoding="utf-8"), indent=1)

    def update(self, cid, **kw):
        c = self.container(cid) or {"id": int(cid)}
        c.update(kw)
        self.save_container(c)
        return c

    def template(self, name):
        if not name:
            return None, None
        p = os.path.join(self.tdir, f"{name}.json")
        if not os.path.exists(p):
            return None, None
        m = os.path.getmtime(p)
        if self._tcache.get(name, (0,))[0] != m:
            self._tcache[name] = (m, template_from_json(json.load(open(p, encoding="utf-8"))))
        return self._tcache[name][1]

    def save_template(self, name, top, bottom):
        os.makedirs(self.tdir, exist_ok=True)
        json.dump(template_to_json(top, bottom), open(os.path.join(self.tdir, f"{name}.json"), "w", encoding="utf-8"))
        self._tcache.pop(name, None)

    def templates(self):
        try:
            return sorted(f[:-5] for f in os.listdir(self.tdir) if f.endswith(".json"))
        except OSError:
            return []

    # ---- the cameras and the head ----
    def head_at(self, x, y, slow=True):
        """the head picture (oriented, full size, settled) with the camera's centre at (x, y). slow: the
        gentle move for the exact look at one part; the scan's many pictures go at the normal speed (the
        table stands still, only the head moves, and the picture is taken once it has settled)"""
        from neocalib import grab_settled
        cam = self.cfg.head_cam_offset
        self.mb.head_led(3)
        self.mb.move_xy(x + cam[0], y + cam[1], timeout=20, slow=slow)
        with self.vis.lock:
            d = self.vis.dev() if callable(self.vis.dev) else self.vis.dev
            raw = grab_settled(lambda: np.asarray(d.grab(HEAD)), tol_px=0.3, stable=2, max_s=1.5, min_s=0.1).frame
        from neocam import vision as V
        img = V.orient_head(raw)
        self.vis.seq += 1
        self.vis.last[HEAD] = (self.vis.seq, img)
        return img

    def look(self, x, y):
        """the part nearest (x, y), centred: (Part, its machine X / Y, the picture). Two tries: the
        first picture's offset recentres the second (the most exact)"""
        k = self.cfg.pxmm(HEAD)
        img = self.head_at(x, y)
        p = BK.analyze(img, k)
        if p.cx == 0:
            return p, (x, y), img
        x, y = x + p.dx_mm, y + p.dy_mm
        if math.hypot(p.dx_mm, p.dy_mm) > 0.15:
            img = self.head_at(x, y)
            p = BK.analyze(img, k)
            if p.cx:
                x, y = x + p.dx_mm, y + p.dy_mm
        return p, (x, y), img

    # ---- the scan ----
    def scan(self, cid, keep=True):
        """the container's area in a grid of head pictures, stitched at 1/4 size; the bodies on it.
        Returns [{"x", "y", "L", "W", "angle"}] (camera coordinates) and saves the mosaic PNG."""
        c = self.container(cid)
        if not c:
            raise ValueError(f"no bulk feeder {cid}")
        x0, x1 = sorted((float(c["x0"]), float(c["x1"])))
        y0, y1 = sorted((float(c["y0"]), float(c["y1"])))
        k = self.cfg.pxmm(HEAD)
        S = 0.25
        t0 = time.time()
        # the grid is laid for what one picture sees (25 x 46 mm pocket: 18 pictures at 8.9 mm, where the
        # fixed 7 mm grid took 28); a camera set to another picture size: laid again from the first picture
        fov = HEAD_PX / k
        cam = self.cfg.head_cam_offset
        from neomach import proto as P
        lim = (-cam[0] + 0.05, -cam[1] + 0.05, P.X_MAX_MM - cam[0] - 0.05, P.Y_MAX_MM - cam[1] - 0.05)   # the camera's centre
        # the pictures reach SCAN_PAD_MM past the area: a body lying against the wall is seen whole, and the
        # wall itself, so it can be told from the body. Never past where the head can go.
        sx0, sx1 = max(x0 - SCAN_PAD_MM, lim[0] - fov / 2), min(x1 + SCAN_PAD_MM, lim[2] + fov / 2)
        sy0, sy1 = max(y0 - SCAN_PAD_MM, lim[1] - fov / 2), min(y1 + SCAN_PAD_MM, lim[3] + fov / 2)
        way, xs, ys = scan_grid(sx0, sx1, sy0, sy1, fov)
        shots, i = [], 0
        while i < len(way):
            x, y = way[i]
            im = self.head_at(x, y, slow=False)
            if not shots and abs(min(im.shape) / k - fov) > 0.05:
                fov = min(im.shape) / k
                way, xs, ys = scan_grid(sx0, sx1, sy0, sy1, fov)
                if way[0] != (x, y):
                    continue
            shots.append((x, y, im))
            i += 1
        h, w = shots[0][2].shape
        mx0, my1 = min(xs) - w / 2 / k, max(ys) + h / 2 / k
        W = int((max(xs) - min(xs)) * k * S + w * S) + 4
        H = int((max(ys) - min(ys)) * k * S + h * S) + 4
        mos = np.zeros((H, W), np.float32)
        cnt = np.zeros((H, W), np.float32)
        for x, y, im in shots:
            sm = cv2.resize(im, None, fx=S, fy=S, interpolation=cv2.INTER_AREA).astype(np.float32)
            u, v = int(round((x - mx0) * k * S - sm.shape[1] / 2)), int(round((my1 - y) * k * S - sm.shape[0] / 2))
            hh, ww = min(sm.shape[0], H - v), min(sm.shape[1], W - u)
            mos[v:v + hh, u:u + ww] += sm[:hh, :ww]
            cnt[v:v + hh, u:u + ww] += 1
        mos = (mos / np.maximum(cnt, 1)).astype(np.uint8)
        found, boxes = [], []
        top, _ = self.template(c.get("package"))
        size = (float(top["L"]), float(top["W"])) if top and top.get("L") and top.get("W") else None
        for b in find_body_rects(mos, k * S, size=size):
            X, Y = mx0 + b["cx"] / (k * S), my1 - b["cy"] / (k * S)
            if b.get("cut") and b.get("odd"):                     # at the pictures' edge and not the part's size:
                continue                                          # not whole, its middle is not known
            if x0 - 0.5 <= X <= x1 + 0.5 and y0 - 0.5 <= Y <= y1 + 0.5:
                found.append({"x": round(X, 3), "y": round(Y, 3), "L": b["L"], "W": b["W"], "angle": b["angle"]})
                boxes.append(b["box"])
        vis = cv2.cvtColor(mos, cv2.COLOR_GRAY2BGR)
        for i, box in enumerate(boxes):                               # each part's rectangle, numbered
            pts = np.array(box, np.int32)
            cv2.polylines(vis, [pts], True, (0, 220, 0), 2)
            cv2.putText(vis, str(i + 1), tuple(int(v) for v in pts.min(axis=0)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 0), 1)
        cv2.imwrite(os.path.join(self.dir, f"bulk_{int(cid)}.png"), vis)
        json.dump({"x0": mx0, "y1": my1, "k": k * S}, open(os.path.join(self.dir, f"bulk_{int(cid)}.json"), "w"))
        if keep:
            self.update(cid, parts=found, scanned=time.strftime("%Y-%m-%d %H:%M:%S"), count=len(found))
            # nearest first along a path: fewer long moves
            self.queue[int(cid)] = sorted(found, key=lambda f: (round(f["y"] / 5), f["x"]))
        self.log(f"bulk feeder {cid}: {len(found)} parts seen ({len(shots)} pictures, {time.time() - t0:.1f} s)")
        return found

    # ---- the next part to pick ----
    def next_part(self, cid, use_pin1=None, max_tries=8):
        """look at the parts of the last scan one by one (centred): the first that is there, is the
        package, lies alone. Returns (x, y, phi, part) with phi the part's turn against the taught one
        (deg), or None when none is left (scan again / refill)."""
        c = self.container(cid)
        top, _ = self.template(c.get("package") if c else None)
        src = (c or {}).get("dir_source") or ("laid" if (c or {}).get("orient") == "fixed" else "top")
        fixed = src == "laid"                                   # laid by hand all the same way
        if not c or (not top and not fixed):
            raise ValueError(f"bulk feeder {cid}: no package taught")
        pin1_on = bool(c.get("pin1", True)) if use_pin1 is None else use_pin1
        q = self.queue.get(int(cid))
        if q is None:
            q = list(c.get("parts") or [])
            self.queue[int(cid)] = q
        tries = 0
        while q and tries < max_tries:
            f = q.pop(0)
            tries += 1
            p, (x, y), img = self.look(f["x"], f["y"])
            if not p.ok or p.cx == 0:
                self.log(f"bulk {cid}: nothing at {f['x']:.2f}, {f['y']:.2f} ({p.why or 'no part'})")
                continue
            if p.touching:
                self.log(f"bulk {cid}: the part at {x:.2f}, {y:.2f} touches another: left")
                continue
            if top:
                t, dist, turn, conf = BK.match(p, [top], use_pin1=pin1_on)
                if dist > 1.3:                                   # one lead miscounted is still the package
                    self.log(f"bulk {cid}: the part at {x:.2f}, {y:.2f} is not {c.get('package')} ({BK.signature(p)}): left")
                    continue
            if fixed:                                            # laid by hand: only the small turn (the Pick angle
                a = wrap(p.angle - float(top.get("angle", 0.0) if top else 0.0))   # says how they lie)
                phi = a - 90.0 * round(a / 90.0)
                p.how = "laid"
                self.log(f"bulk {cid}: at {x:.3f}, {y:.3f}, laid by hand, turned {phi:+.1f} deg")
                return x, y, phi, p
            if src == "bottom":                                  # the underside gives the turn (the up camera)
                phi = wrap(p.angle - turn - float(top.get("angle", 0.0)))
                p.how = "axis (the underside decides)"
                self.log(f"bulk {cid}: {c.get('package')} at {x:.3f}, {y:.3f}, axis {phi:+.1f} deg: the underside decides")
                return x, y, phi, p
            phi = wrap(p.angle - turn - float(top.get("angle", 0.0)))
            self.log(f"bulk {cid}: {c.get('package')} at {x:.3f}, {y:.3f}, turned {phi:+.1f} deg ({getattr(p, 'how', '')}, {conf})")
            return x, y, phi, p
        return None

    # ---- the pick by the vacuum ----
    @staticmethod
    def pick_z(c):
        """the depth (mm) the nozzle tip meets this container's parts: set by hand on the feeder, or
        measured once (Measure); None = not known yet"""
        z = (c or {}).get("z_pick") or (c or {}).get("z_seal")
        return float(z) if z else None

    def pick_at(self, n, x, y, z, extra=1.0, speed=30):
        """nozzle n straight down to z + extra with the suction on, then up; True when the part came up
        with it (the pressure stays low)"""
        mb = self.mb
        mb.raise_all()
        mb.rotate(n, 0.0, wait=True)
        mb.move_xy(x, y, timeout=20, slow=True)
        mb.valve(n, -1)
        time.sleep(0.15)
        base = float(np.median([mb.vacuum(n) for _ in range(5)]))
        mb.set_z(n, min(13.0, z + extra), speed, wait=True)
        time.sleep(0.15)
        mb.set_z(n, 0.0, 40, wait=True)
        time.sleep(0.1)
        held = float(np.median([mb.vacuum(n) for _ in range(3)])) <= base - 8
        if not held:
            mb.valve(n, 0)
        return held

    def pick_by_feel(self, n, x, y, z_hint=None, step=0.1, extra=1.0, zmax=13.0, speed=20):
        """the pick height measured (once per container, Measure): nozzle n over (x, y) (nozzle-1 frame +
        the nozzle's offset applied by the caller), suction on, down in steps from z_hint - 1 until the
        pressure falls (the tip on the part), then `extra` mm more; up. Returns the depth it sealed at,
        or None. The job and the trial pick at that height directly (pick_at)."""
        mb = self.mb
        mb.raise_all()
        mb.rotate(n, 0.0, wait=True)
        mb.move_xy(x, y, timeout=20, slow=True)
        mb.valve(n, -1)
        time.sleep(0.3)
        base = float(np.median([mb.vacuum(n) for _ in range(7)]))
        z = max(0.0, (z_hint - 1.0) if z_hint else 8.0)
        mb.set_z(n, z, 40, wait=True)
        seal = None
        while z <= zmax:
            mb.set_z(n, z, speed, wait=True)
            time.sleep(0.08)
            if float(np.median([mb.vacuum(n) for _ in range(3)])) <= base - 8:
                seal = z
                break
            z += step
        if seal is None:
            mb.set_z(n, 0.0, 40, wait=True)
            mb.valve(n, 0)
            return None
        mb.set_z(n, min(zmax, seal + extra), speed, wait=True)
        time.sleep(0.2)
        mb.set_z(n, 0.0, 30, wait=True)
        return seal

    def put_back(self, n, x, y, z_seal):
        """the part on nozzle n down where it was taken (at the depth the tip met it), released"""
        mb = self.mb
        mb.move_xy(x, y, timeout=20, slow=True)
        mb.set_z(n, max(0.0, z_seal - 0.2), 20, wait=True)
        mb.valve(n, 1)
        time.sleep(0.15)
        mb.set_z(n, max(0.0, z_seal - 0.4), 20, wait=True)
        mb.valve(n, 0)
        mb.set_z(n, 0.0, 30, wait=True)

    def bottom_picture(self, n, exposure):
        """nozzle n over the up camera, the underside's picture at `exposure`"""
        from neocalib import grab_settled
        from neocam import vision as V
        mb = self.mb
        mb.head_led(0)
        mb.up_light(True)
        mb.move_xy(*self.cfg.nozzle_positions[n - 1], timeout=20, slow=True)
        with self.vis.lock:
            d = self.vis.dev() if callable(self.vis.dev) else self.vis.dev
            d.window(UP, 1024, 1024)
            d.exposure(UP, int(exposure))
            raw = grab_settled(lambda: np.asarray(d.grab(UP)), tol_px=0.3, stable=2, max_s=1.5, min_s=0.15).frame
        img = V.orient_up(raw)
        self.vis.seq += 1
        self.vis.last[UP] = (self.vis.seq, img)
        return img


def bottom_result(img, k, bottom_t, phi_top):
    """the underside against the bottom template: (ok, why, dx, dy, dphi) with dx, dy the metal pattern's
    reference point from the nozzle (picture centre) in mm (+X right, +Y up as the up camera's oriented
    picture), dphi the turn against the taught part. For a symmetric underside the turn nearest the top's
    is used."""
    b = BK.analyze_bottom(img, k)
    if not b.ok:
        return False, b.why, 0.0, 0.0, 0.0, b
    m = BK.match_bottom(b, bottom_t)
    if m["missing"]:
        return False, f"{m['missing']} lead(s) / pad(s) missing", 0.0, 0.0, 0.0, b
    phi = wrap(b.angle - m["turn"] - m["dangle"] - float(bottom_t.get("angle", 0.0)))
    if m["sure"] <= 0:                                           # symmetric: 0 / 180 by the top
        alt = wrap(phi + 180.0)
        if abs(wrap(alt - phi_top)) < abs(wrap(phi - phi_top)):
            phi = alt
    # the template's reference point (its pattern's centre) in this part's frame, then in the picture
    sh = np.array([m["dx"], m["dy"]])
    ref = BK._rot(BK._rot((-sh)[None, :], -m["dangle"]), -m["turn"])[0]
    a = math.radians(b.angle)
    H, W = img.shape
    rx = b.cx + (ref[0] * math.cos(a) - ref[1] * math.sin(a)) * k
    ry = b.cy - (ref[0] * math.sin(a) + ref[1] * math.cos(a)) * k
    return True, "", (rx - W / 2) / k, (H / 2 - ry) / k, phi, b
