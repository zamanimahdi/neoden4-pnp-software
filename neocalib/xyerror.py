"""XY accuracy: repeatability / backlash test and the error map (grid plate) with a
correction table.

Both need measure(): the machine move (mm, machine axes) that would centre the feature
under the head camera, i.e. the feature's position relative to the camera. make_measure()
builds it from the head camera, a calibration (CalResult) and a fiducial finder.

Repeatability: the head approaches one target from +X, -X, +Y, -Y (direct moves, so
backlash shows) and with the one-sided slow approach (NeoDen's moveToSlow), several cycles.
Result: backlash per axis and the spread of each approach.

Error map: a precise grid plate (glass, or a PCB with a fiducial grid) of nx x ny features
at a known pitch. Each node is visited and measured; the apparent feature positions are
fitted with an affine map (machine scale X / Y, skew of the axes); what is left at each
node goes into a correction table, interpolated bilinearly between nodes. ErrorMap.correct
plugs into Mainboard.xy_map so every move is corrected. The plate's own accuracy is the
limit (glass ~0.005 mm, PCB ~0.05 mm).
"""
import json
import math
import time
from dataclasses import asdict, dataclass, field

import numpy as np


# ---- measurement from the head camera ------------------------------------------------

def make_measure(grab, cal, finder):
    """measure() for the tests below. grab() -> head frame (vision frame); cal = head camera
    CalResult; finder(img) -> (x_px, y_px) of the feature or None (e.g. a fiducial)."""
    def measure():
        img = grab()
        f = finder(img)
        if f is None:
            return None
        h, w = img.shape[:2]
        dx, dy = cal.img_to_mm(f[0] - w / 2, f[1] - h / 2)
        # the image shows the feature offset; moving the head by -A^-1 * offset centres it
        return -dx, -dy
    return measure


def mark_finder(dmin_mm=0.8, dmax_mm=2.5, px_per_mm=112.0):
    """finder() using neocam's fiducial search (NeoDen's findMarkBest)."""
    from neocam.vision import find_mark_best

    def finder(img):
        r = find_mark_best(img, dmin_mm, dmax_mm, px_per_mm)
        return (r.x, r.y) if r.precision > 0 else None
    return finder


# ---- repeatability / backlash ----------------------------------------------------------

@dataclass
class RepeatResult:
    target: tuple
    cycles: int
    approach_mm: float
    by_side: dict                   # "+X" etc. -> [(x, y)] feature offsets (mm)
    one_sided: list
    backlash_x: float = 0.0         # mean offset difference, approached from +X vs -X
    backlash_y: float = 0.0
    spread_direct: float = 0.0      # largest distance between any two direct-approach readings
    spread_one_sided: float = 0.0
    sd_one_sided: float = 0.0

    def summary(self):
        return (f"backlash X {self.backlash_x * 1000:.1f} um, Y {self.backlash_y * 1000:.1f} um; "
                f"spread direct {self.spread_direct * 1000:.1f} um, one-sided {self.spread_one_sided * 1000:.1f} um "
                f"(sd {self.sd_one_sided * 1000:.1f} um); {self.cycles} cycles")


def _spread(pts):
    a = np.array(pts)
    if len(a) < 2:
        return 0.0
    d = a[:, None, :] - a[None, :, :]
    return float(np.sqrt((d ** 2).sum(-1)).max())


def repeatability(mb, measure, target=None, cycles=5, approach_mm=5.0, settle_s=0.25, progress=None):
    """Approach `target` (default: the current position) from four sides and one-sided."""
    target = tuple(target or mb.position())
    sides = {"+X": (1, 0), "-X": (-1, 0), "+Y": (0, 1), "-Y": (0, -1)}
    by_side = {k: [] for k in sides}
    one = []
    steps, k = cycles * 5, 0

    def read():
        time.sleep(settle_s)
        m = measure()
        if m is None:
            raise RuntimeError("feature not found under the head camera")
        return m
    for _ in range(cycles):
        for name, (sx, sy) in sides.items():
            mb.move_xy(target[0] + sx * approach_mm, target[1] + sy * approach_mm)
            mb.move_xy(*target)                                  # direct: backlash shows
            by_side[name].append(read())
            k += 1
            if progress:
                progress(k, steps)
        mb.move_xy(target[0] + approach_mm, target[1] + approach_mm)
        mb.move_xy(*target, slow=True)                           # one-sided, from (x-1, y-1)
        one.append(read())
        k += 1
        if progress:
            progress(k, steps)
    mean = {n: np.mean(v, axis=0) for n, v in by_side.items()}
    direct = [p for v in by_side.values() for p in v]
    sd = float(np.sqrt(np.mean(np.sum((np.array(one) - np.mean(one, axis=0)) ** 2, axis=1))))
    return RepeatResult(target, cycles, approach_mm, {n: [tuple(map(float, p)) for p in v] for n, v in by_side.items()},
                        [tuple(map(float, p)) for p in one],
                        float(mean["+X"][0] - mean["-X"][0]), float(mean["+Y"][1] - mean["-Y"][1]),
                        _spread(direct), _spread(one), sd)


# ---- error map -------------------------------------------------------------------------

def rot(deg):
    a = math.radians(deg)
    return np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])


@dataclass
class ErrorMap:
    pitch: float
    nx: int
    ny: int
    nodes: list                     # [(i, j, Cx, Cy, Px, Py)] commanded and apparent feature position
    K: list = None                  # machine linear error: true = K (C - ref) + ref (+ residual)
    ref: list = None                # reference point (grid centre, commanded)
    M: list = None                  # affine plate -> apparent position (2x2) and t
    t: list = None
    scale_x: float = 1.0
    scale_y: float = 1.0
    skew_deg: float = 0.0           # Y axis tilt (true y gains x * tan(skew))
    residual: list = None           # ny x nx x 2 (mm), nonlinear part at the nodes (commanded frame)
    rms_um: float = 0.0
    max_um: float = 0.0
    date: str = ""
    extra: dict = field(default_factory=dict)

    # -- fit --
    def fit(self):
        g = np.array([[n[0] * self.pitch, n[1] * self.pitch] for n in self.nodes])
        C = np.array([[n[2], n[3]] for n in self.nodes])
        Pp = np.array([[n[4], n[5]] for n in self.nodes])
        X = np.hstack([g, np.ones((len(g), 1))])
        sol, *_ = np.linalg.lstsq(X, Pp, rcond=None)
        M, t = sol[:2].T, sol[2]
        N = np.linalg.inv(M)                                    # K = R(phi) N, K[0,1] = 0
        phi = math.atan2(N[0, 1], N[1, 1])
        R = np.array([[math.cos(phi), -math.sin(phi)], [math.sin(phi), math.cos(phi)]])
        K = R @ N
        if K[0, 0] < 0:                                         # keep the axes' sense
            K = -K
        self.M, self.t, self.K = M.tolist(), t.tolist(), K.tolist()
        self.scale_x, self.scale_y = float(K[0, 0]), float(K[1, 1])
        self.skew_deg = float(math.degrees(math.atan2(K[1, 0], K[0, 0])))
        self.ref = C.mean(axis=0).tolist()
        # nonlinear residual: apparent minus affine prediction, as a machine error (true - commanded)
        pred = X @ sol
        res = -(Pp - pred)
        grid = np.zeros((self.ny, self.nx, 2))
        for (n, r) in zip(self.nodes, res):
            grid[n[1], n[0]] = r
        self.residual = grid.tolist()
        e = np.linalg.norm(res, axis=1)
        self.rms_um, self.max_um = float(np.sqrt(np.mean(e ** 2)) * 1000), float(e.max() * 1000)
        self.date = time.strftime("%Y-%m-%d %H:%M:%S")
        self._cache()
        return self

    def _cache(self):
        self._K = np.array(self.K)
        self._Kinv = np.linalg.inv(self._K)
        self._ref = np.array(self.ref)
        self._res = np.array(self.residual)
        C = np.array([[n[2], n[3]] for n in self.nodes])
        # node (i, j) commanded positions in a regular array for interpolation
        self._C = np.zeros((self.ny, self.nx, 2))
        for n in self.nodes:
            self._C[n[1], n[0]] = (n[2], n[3])
        self._origin = self._C[0, 0]
        ex = (self._C[0, -1] - self._C[0, 0]) / max(1, self.nx - 1)
        ey = (self._C[-1, 0] - self._C[0, 0]) / max(1, self.ny - 1)
        self._E = np.column_stack([ex, ey])

    def residual_at(self, x, y):
        """Nonlinear machine error (mm) at commanded (x, y), bilinear between nodes (clamped)."""
        u, v = np.linalg.solve(self._E, np.array([x, y]) - self._origin)
        u = min(max(u, 0.0), self.nx - 1.0)
        v = min(max(v, 0.0), self.ny - 1.0)
        i0, j0 = min(int(u), self.nx - 2), min(int(v), self.ny - 2)
        fu, fv = u - i0, v - j0
        r = self._res
        return ((1 - fu) * (1 - fv) * r[j0, i0] + fu * (1 - fv) * r[j0, i0 + 1]
                + (1 - fu) * fv * r[j0 + 1, i0] + fu * fv * r[j0 + 1, i0 + 1])

    def true_from_commanded(self, x, y):
        c = np.array([x, y])
        return tuple(self._ref + self._K @ (c - self._ref) + self.residual_at(x, y))

    def correct(self, x, y):
        """Command that puts the head truly at (x, y): plug into Mainboard.xy_map."""
        d = np.array([x, y], float)
        c = d.copy()
        for _ in range(3):
            c = self._ref + self._Kinv @ (d - self._ref - self.residual_at(*c))
        return float(c[0]), float(c[1])

    def summary(self):
        return (f"scale X {(self.scale_x - 1) * 100:+.3f} %, Y {(self.scale_y - 1) * 100:+.3f} %, "
                f"axes skew {self.skew_deg:+.4f} deg; nonlinear rest rms {self.rms_um:.1f} um, "
                f"max {self.max_um:.1f} um; {len(self.nodes)} nodes, pitch {self.pitch:g} mm")

    def save(self, path):
        d = asdict(self)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f)

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            em = cls(**json.load(f))
        em._cache()
        return em


def measure_grid(mb, measure, origin, pitch, nx, ny, angle_deg=0.0, settle_s=0.25, progress=None):
    """Visit an nx x ny grid plate and fit the error map.

    origin: machine XY with the head camera over node (0, 0) (taught by the user);
    angle_deg: the plate's rotation on the machine (teach a second node, or 0);
    the predicted node position is refined from the nodes measured so far."""
    nodes = []
    R = rot(angle_deg)
    o = np.array(origin, float)
    total = nx * ny
    for j in range(ny):
        row = range(nx) if j % 2 == 0 else range(nx - 1, -1, -1)       # serpentine path
        for i in row:
            g = np.array([i * pitch, j * pitch])
            G = np.array([[n[0] * pitch, n[1] * pitch, 1.0] for n in nodes]) if nodes else None
            if G is not None and np.linalg.matrix_rank(G) == 3:      # nodes span both directions
                Pp = np.array([[n[4], n[5]] for n in nodes])
                sol, *_ = np.linalg.lstsq(G, Pp, rcond=None)
                C = np.array([g[0], g[1], 1.0]) @ sol
            elif len(nodes) >= 2:                                    # first row: line through it
                p0, p1 = np.array(nodes[0][4:6]), np.array(nodes[-1][4:6])
                k = nodes[-1][0] - nodes[0][0]
                ex = (p1 - p0) / k if k else R @ np.array([pitch, 0.0])
                ey = np.array([-ex[1], ex[0]])
                C = p0 + ex * (i - nodes[0][0]) + ey * (j - nodes[0][1])
            else:
                C = o + R @ g
            mb.move_xy(float(C[0]), float(C[1]), slow=True)
            time.sleep(settle_s)
            m = measure()
            if m is None:
                raise RuntimeError(f"grid node ({i}, {j}) not found under the head camera")
            nodes.append((i, j, float(C[0]), float(C[1]), float(C[0] + m[0]), float(C[1] + m[1])))
            if progress:
                progress(len(nodes), total)
    return ErrorMap(pitch, nx, ny, nodes).fit()


# ---- the linear correction from a precise board's fiducials ----------------------------

@dataclass
class XYCorrection:
    """the machine's linear XY error measured on a precise board (its fiducials, centred by the head
    camera): commanded = ref + L (true - ref), L = [[sx, k], [0, sy]] (the X axis of motion is the
    reference direction: the board's rotation is taken out). sx, sy: travel per true mm along X / Y;
    k: the Y axis's lean (non-perpendicular axes). At `ref` (the up camera, nozzle 1) nothing moves.
    Mainboard.xy_map = XYCorrection.correct; <data>/xy_correction.json (neoserver loads it)."""
    L: list
    ref: list
    scale_x: float = 1.0
    scale_y: float = 1.0
    skew_deg: float = 0.0
    rms_um: float = 0.0
    marks: int = 0
    source: str = ""
    date: str = ""
    limits: list = None              # [x_max, y_max] the command is kept in

    @classmethod
    def from_marks(cls, design, commanded, ref, source="", limits=None):
        """design: the marks' true positions (board, mm); commanded: where the machine had to go to
        centre each (machine, mm); >= 3 marks, not on a line"""
        g, m = np.asarray(design, float), np.asarray(commanded, float)
        X = np.hstack([g, np.ones((len(g), 1))])
        sol, *_ = np.linalg.lstsq(X, m, rcond=None)
        A = sol[:2].T
        th = math.atan2(A[1, 0], A[1, 1])                       # the board's own rotation: out (A = L R)
        L = A @ rot(-math.degrees(th))
        L[1, 0] = 0.0
        res = m - X @ sol
        return cls(L=L.tolist(), ref=[float(v) for v in ref], scale_x=float(L[0, 0]), scale_y=float(L[1, 1]),
                   skew_deg=float(math.degrees(math.atan2(L[0, 1], L[1, 1]))),
                   rms_um=float(np.sqrt((res ** 2).sum(1).mean()) * 1000), marks=len(g), source=source,
                   date=time.strftime("%Y-%m-%d %H:%M:%S"), limits=limits)

    def correct(self, x, y):
        """the command that puts the head truly at (x, y)"""
        L, r = self.L, self.ref
        cx = r[0] + L[0][0] * (x - r[0]) + L[0][1] * (y - r[1])
        cy = r[1] + L[1][1] * (y - r[1])
        if self.limits:                                         # the ends of travel (home, park) stay reachable
            cx, cy = min(max(cx, 0.0), self.limits[0]), min(max(cy, 0.0), self.limits[1])
        return cx, cy

    def summary(self):
        return (f"scale X {(self.scale_x - 1) * 1000:+.3f} mm/m, Y {(self.scale_y - 1) * 1000:+.3f} mm/m, "
                f"Y axis lean {self.skew_deg:+.4f} deg; {self.marks} marks, rest rms {self.rms_um:.0f} um ({self.source})")

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=1)

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            return cls(**json.load(f))
