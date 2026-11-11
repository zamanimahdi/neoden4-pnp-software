"""Z height map: the machine's own heights over the table (its axes and table are not flat: nozzle 1 meets a
flat surface at a different depth here and there), and each job's board height on top of it.

The machine map (<data>/zmap.json, machine coordinates, nozzle-1 frame) is a calibration: nozzle 1 touches
a board (the vacuum seals on its surface) at a grid of points; between them the height is linear (the
points cut into triangles, Delaunay, each a plane; outside them the nearest triangle's plane carried on).
Its shape (the height less the points' median) applies to every job. Nozzles 2 .. 4 touch two of the
points too: their difference from nozzle 1 (`nozzles`: {"2": mm, ...}, + = deeper). `odd()` names a point
the plane through the others misses by much.

A job's board height (<job>.zmap.json, `board_z`): where nozzle 1 meets that board at the machine map's
middle level, from a touch or three on it (the machine's shape there taken off). With it a nozzle n
meets the board at (x, y) at board_z + shape(x, y, n), and a part h tall is placed at that less h plus a
press; the board is a floor nothing goes below (less h, plus PRESS). Without the board height the
machine's shape still moves the feeders' place depths.

    hz = load(job_path, data_dir)     # None without either
    z += hz.delta(x, y, n)            # x, y: machine point on the table (not the nozzle's head position)
    z = min(z, hz.floor(x, y, n, part_h))   # floor None without the board height
"""
import itertools
import json
import os

import numpy as np

LIMIT_MM = 1.0
PRESS = 0.3                       # how far past the board's touch a part is pressed at most (the nozzle's spring)


def path_of(job_path):
    return job_path + ".zmap.json"


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _overlap(T, U):
    """two triangles share inner area: an edge of one properly crosses an edge of the other, or one's middle
    lies inside the other"""
    for i in range(3):
        a, b = T[i], T[(i + 1) % 3]
        for j in range(3):
            c, d = U[j], U[(j + 1) % 3]
            d1, d2, d3, d4 = _cross(c, d, a), _cross(c, d, b), _cross(a, b, c), _cross(a, b, d)
            if ((d1 > 1e-9 and d2 < -1e-9) or (d1 < -1e-9 and d2 > 1e-9)) and ((d3 > 1e-9 and d4 < -1e-9) or (d3 < -1e-9 and d4 > 1e-9)):
                return True

    def inside(t, p):
        s = [_cross(t[k], t[(k + 1) % 3], p) for k in range(3)]
        return all(v > 1e-9 for v in s) or all(v < -1e-9 for v in s)
    return inside(U, T.mean(0)) or inside(T, U.mean(0))


def triangles(P):
    """Delaunay triangles of the points P (N x 2, a few dozen): the triples whose circle holds no other
    point (every triple at once, numpy: 36 points in a few ms on the machine's Atom). Four points on one
    circle (an exact grid's cell) give both diagonals: the fatter triangles are kept first and one that
    overlaps a kept one is left out"""
    P = np.asarray(P, float)
    n = len(P)
    if n < 3:
        return []
    T = np.array(list(itertools.combinations(range(n), 3)))
    a, b, c = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    d = 2 * (a[:, 0] * (b[:, 1] - c[:, 1]) + b[:, 0] * (c[:, 1] - a[:, 1]) + c[:, 0] * (a[:, 1] - b[:, 1]))
    ok = np.abs(d) >= 1e-9
    T, a, b, c, d = T[ok], a[ok], b[ok], c[ok], d[ok]
    sa, sb, sc = (a * a).sum(1), (b * b).sum(1), (c * c).sum(1)
    ux = (sa * (b[:, 1] - c[:, 1]) + sb * (c[:, 1] - a[:, 1]) + sc * (a[:, 1] - b[:, 1])) / d
    uy = (sa * (c[:, 0] - b[:, 0]) + sb * (a[:, 0] - c[:, 0]) + sc * (b[:, 0] - a[:, 0])) / d
    r2 = (a[:, 0] - ux) ** 2 + (a[:, 1] - uy) ** 2
    D = (P[None, :, 0] - ux[:, None]) ** 2 + (P[None, :, 1] - uy[:, None]) ** 2      # every point to every circle
    D[np.arange(len(T))[:, None], T] = np.inf                                         # its own three corners
    empty = (D >= r2[:, None] * (1 - 1e-7)).all(1)
    on = (np.abs(D - r2[:, None]) <= r2[:, None] * 1e-7).any(1)                      # a 4th point on its circle
    T, d, on = T[empty], d[empty], on[empty]
    out = [tuple(int(v) for v in t) for t in T[~on]]                                 # plain Delaunay: never overlap
    T, d = T[on], d[on]
    if len(T):
        per2 = sum(((P[T[:, u]] - P[T[:, v]]) ** 2).sum(1) for u, v in ((0, 1), (1, 2), (2, 0)))
        box = lambda t: (P[list(t)].min(0), P[list(t)].max(0))
        kept = [(u, *box(u)) for u in out]
        for k in np.argsort(-(np.abs(d) / 4) / per2):
            t = tuple(int(v) for v in T[k])
            lo, hi = box(t)                          # only the kept ones whose box meets this one's
            if not any((hi > ulo + 1e-9).all() and (uhi > lo + 1e-9).all() and _overlap(P[list(t)], P[list(u)]) for u, ulo, uhi in kept):
                out.append(t)
                kept.append((t, lo, hi))
    return out


class ZMap:
    def __init__(self, points, on=True, nozzles=None):
        self.points, self.on = points, bool(on)
        self.nozzles = {int(k): float(v) for k, v in (nozzles or {}).items() if v is not None}
        self.P = np.array([(p["x"], p["y"]) for p in points], float)
        self.Zv = np.array([p["z"] for p in points], float)
        self.ref = float(np.median(self.Zv))
        self.tri = triangles(self.P) if len(self.P) >= 3 else []
        # the plane of each triangle: z = a x + b y + c
        self.planes = []
        for t in self.tri:
            A = np.c_[self.P[list(t)], np.ones(3)]
            self.planes.append(np.linalg.solve(A, self.Zv[list(t)]))
        if not self.tri:                                       # all in a line: one plane through them
            A = np.c_[self.P, np.ones(len(self.P))]
            self.planes = [np.linalg.lstsq(A, self.Zv, rcond=None)[0]]
        A = np.c_[self.P, np.ones(len(self.P))]
        plane, *_ = np.linalg.lstsq(A, self.Zv, rcond=None)    # one plane through all: how flat the board is
        self.tilt_rms = float(np.sqrt(((A @ plane - self.Zv) ** 2).mean()))

    def _inside(self, t, x, y):
        a, b, c = self.P[list(t)]
        v0, v1, v2 = c - a, b - a, np.array([x, y]) - a
        d00, d01, d11, d20, d21 = v0 @ v0, v0 @ v1, v1 @ v1, v2 @ v0, v2 @ v1
        den = d00 * d11 - d01 * d01
        if abs(den) < 1e-12:
            return False
        u, v = (d11 * d20 - d01 * d21) / den, (d00 * d21 - d01 * d20) / den
        return u >= -1e-9 and v >= -1e-9 and u + v <= 1 + 1e-9

    def z(self, x, y):
        if self.tri:
            for t, pl in zip(self.tri, self.planes):
                if self._inside(t, x, y):
                    return float(pl @ [x, y, 1.0])
            # outside the points: the triangle whose middle is nearest, its plane carried on
            mids = [self.P[list(t)].mean(0) for t in self.tri]
            i = int(np.argmin([np.hypot(x - m[0], y - m[1]) for m in mids]))
            return float(self.planes[i] @ [x, y, 1.0])
        return float(self.planes[0] @ [x, y, 1.0])

    def off(self, n):
        """nozzle n's touch less nozzle 1's (mm, + deeper)"""
        return self.nozzles.get(int(n), 0.0)

    def touch(self, x, y, n=1):
        """the depth nozzle n's empty tip met the calibration board at (x, y)"""
        return self.z(x, y) + self.off(n)

    def shape(self, x, y, n=1):
        """the machine's own height at (x, y) for nozzle n, less the middle of the points (+-LIMIT_MM)"""
        return max(-LIMIT_MM, min(LIMIT_MM, self.touch(x, y, n) - self.ref))

    def delta(self, x, y, n=1):
        """mm to add to a place depth at (x, y) for nozzle n (0 when the map is switched off)"""
        return self.shape(x, y, n) if self.on else 0.0

    def odd(self, tol=0.08):
        """points far off the others' line: left out, the plane through all the others misses them by more
        than tol mm (a bad touch: a pad, dirt, a part's edge). [(index, mm off)]"""
        out = []
        if len(self.points) < 5:
            return out
        A = np.c_[self.P, np.ones(len(self.P))]
        for i in range(len(self.points)):
            k = np.arange(len(self.P)) != i
            pl, *_ = np.linalg.lstsq(A[k], self.Zv[k], rcond=None)
            e = float(A[i] @ pl - self.Zv[i])
            if abs(e) > tol:
                out.append((i, round(e, 3)))
        return out


class Heights:
    """the machine map (or None) and a job's board height (or None) together"""
    def __init__(self, machine=None, board_z=None):
        self.machine, self.board_z = machine, board_z

    @property
    def on(self):
        return self.machine is None or self.machine.on

    def delta(self, x, y, n=1):
        return self.machine.delta(x, y, n) if self.machine else 0.0

    def touch(self, x, y, n=1):
        """where nozzle n meets this board at (x, y); None without its height"""
        if self.board_z is None:
            return None
        return self.board_z + (self.machine.shape(x, y, n) if self.machine else 0.0)

    def floor(self, x, y, n=1, h=0.0):
        """the deepest nozzle n may go at (x, y) carrying a part h mm tall; None without the board height"""
        t = self.touch(x, y, n)
        return None if t is None else t - max(0.0, h or 0.0) + PRESS


def _read(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save(p, d):
    tmp = p + ".tmp"
    json.dump(d, open(tmp, "w", encoding="utf-8"), indent=1)
    os.replace(tmp, p)


def machine_path(data_dir):
    return os.path.join(data_dir, "zmap.json")


def read_machine(data_dir):
    return _read(machine_path(data_dir)) if data_dir else None


def save_machine(data_dir, d):
    _save(machine_path(data_dir), d)


def load_machine(data_dir):
    d = read_machine(data_dir)
    if not d or len(d.get("points") or []) < 3:
        return None
    return ZMap(d["points"], d.get("on", True), d.get("nozzles"))


def read(job_path):
    """the job's board height record ({"board_z", "points", "time"}) or None"""
    return _read(path_of(job_path)) if job_path else None


def save(job_path, d):
    _save(path_of(job_path), d)


def load(job_path, data_dir):
    """the machine map and the job's board height; None without either"""
    m = load_machine(data_dir)
    b = (read(job_path) or {}).get("board_z")
    if m is None and b is None:
        return None
    return Heights(m, None if b is None else float(b))
