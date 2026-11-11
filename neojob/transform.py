"""Board transform from the fiducials (CMarkTransform, JOB_SPEC.md section 2.3)."""
import math


def calc_angle(dx, dy):
    """CMarkTransform calcAngle (0x004028a0): only differences of two such angles are used"""
    if dx * dy > 0:
        return math.atan(dy / dx)
    if dx * dy < 0:
        return math.pi + math.atan(dy / dx)
    return -math.pi / 2 if dy < 0 else math.pi / 2 if dy > 0 else 0.0


def farthest_pair(pts):
    """the two marks farthest apart (what findFarthestPoints was meant to do; NeoDen4 always
    ends with marks 0 and 1 because of its start value)"""
    best, pair = -1.0, (0, 1)
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d = math.dist(pts[i], pts[j])
            if d > best:
                best, pair = d, (i, j)
    return pair


class MarkTransform:
    def __init__(self):
        self.design, self.measured = [], []
        self.pcb_angle = 0.0
        self.ref = self.cur = self.comp0 = (0.0, 0.0)
        self.whole, self.panel_angle = True, 0
        # "neoden": rotation + shift from two marks (NeoDen4); "affine": 3+ marks, least squares
        # with scale X/Y and skew (a board that shrank or grew); "bilinear": 4+ marks, also a
        # twist between the corners (a warped board). Fewer marks fall back to the simpler fit.
        self.fit = "neoden"
        self._coef = None
        self.K = None                   # machine axes error (set_axes): true = K @ commanded

    def residuals(self):
        """per mark: distance (mm) between the measured mark and where the fit puts it"""
        out = []
        for d, m in zip(self.design, self.measured):
            x, y, _ = self._map_raw(d[0], d[1])
            out.append(math.hypot(x - m[0], y - m[1]))
        return out

    def _map_raw(self, x, y):
        n = len(self.design)
        if self.fit == "bilinear" and n >= 4:
            return self._poly(x, y, 0.0, True)
        if self.fit in ("affine", "bilinear") and n >= 3:
            return self._poly(x, y, 0.0, False)
        return x, y, 0.0

    def _solve(self, bilinear):
        import numpy as np
        key = (bilinear, tuple(self.design), tuple(self.measured))
        if self._coef and self._coef[0] == key:
            return self._coef[1]
        d = np.array(self.design, float)
        m = np.array(self.measured, float)
        cols = [np.ones(len(d)), d[:, 0], d[:, 1]] + ([d[:, 0] * d[:, 1]] if bilinear else [])
        A = np.stack(cols, 1)
        cx = np.linalg.lstsq(A, m[:, 0], rcond=None)[0]
        cy = np.linalg.lstsq(A, m[:, 1], rcond=None)[0]
        self._coef = (key, (cx, cy))
        return cx, cy

    def _poly(self, x, y, base, bilinear):
        cx, cy = self._solve(bilinear)
        t = [1.0, x, y] + ([x * y] if bilinear else [])
        mx = sum(a * b for a, b in zip(cx, t))
        my = sum(a * b for a, b in zip(cy, t))
        # local derivatives: how the board's X and Y axes are turned at this point
        ax = (cx[1] + (cx[3] * y if bilinear else 0), cy[1] + (cy[3] * y if bilinear else 0))
        ay = (cx[2] + (cx[3] * x if bilinear else 0), cy[2] + (cy[3] * x if bilinear else 0))
        a1 = math.atan2(ax[1], ax[0])
        a2 = math.atan2(-ay[0], ay[1])
        da = math.atan2(math.sin(a2 - a1), math.cos(a2 - a1))
        return mx, my, math.degrees(a1 + da / 2) + base

    def set_axes(self, scale_x=1.0, scale_y=1.0, skew_deg=0.0):
        """the machine's axes: true position = K @ commanded, K = [[sx, 0], [sx tan(skew), sy]].
        A rigid board looks scaled and skewed in commanded coordinates; the 2-mark fit (rotation
        + shift) is then done in true coordinates and the result turned back into a command.
        Fits with 3+ marks take the axes in with the board, so they ignore this."""
        sx, sy, t = float(scale_x), float(scale_y), math.tan(math.radians(float(skew_deg)))
        self.K = None if (sx, sy, t) == (1.0, 1.0, 0.0) else (sx, 0.0, sx * t, sy)

    def _true(self, p):
        a, b, c, d = self.K
        return a * p[0] + b * p[1], c * p[0] + d * p[1]

    def _command(self, p):
        a, b, c, d = self.K
        det = a * d - b * c
        return (d * p[0] - b * p[1]) / det, (a * p[1] - c * p[0]) / det

    def set_points(self, design, measured):
        assert len(design) == len(measured)
        self.design, self.measured = list(design), list(measured)

    def set_params(self, pcb_angle, ref, cur, comp0, whole, panel_angle):
        self.pcb_angle, self.ref, self.cur, self.comp0 = pcb_angle, ref, cur, comp0
        self.whole, self.panel_angle = whole, panel_angle

    def point(self, x, y):
        """design (comp X, Y) -> (machine X, Y of nozzle 1, angle correction deg)"""
        C, P, R = self.comp0, self.cur, self.ref
        if len(self.design) < 2:
            if self.panel_angle == 0:
                return P[0] + (x - C[0]), P[1] + (y - C[1]), 0.0
            # panel turned 180 deg about comp 0. NeoDen4 computes P + 2C - D here, which does
            # not land comp 0 on P; this is P + (C - D) [UNSURE in JOB_SPEC 4.2]
            return P[0] + (C[0] - x), P[1] + (C[1] - y), 180.0
        dx, dy, base = x, y, self.pcb_angle
        if self.whole:
            if self.panel_angle == 180:
                dx, dy, base = 2 * C[0] - dx, 2 * C[1] - dy, base + 180.0
            dx, dy = dx + P[0] - R[0], dy + P[1] - R[1]
        n = len(self.design)
        if self.fit == "bilinear" and n >= 4:
            return self._poly(dx, dy, base, bilinear=True)
        if self.fit in ("affine", "bilinear") and n >= 3:
            return self._poly(dx, dy, base, bilinear=False)
        i, j = farthest_pair(self.design)
        d0, d1, m0, m1 = self.design[i], self.design[j], self.measured[i], self.measured[j]
        if self.K:
            m0, m1 = self._true(m0), self._true(m1)
        delta = calc_angle(m1[0] - m0[0], m1[1] - m0[1]) - calc_angle(d1[0] - d0[0], d1[1] - d0[1])
        cd = ((d0[0] + d1[0]) / 2, (d0[1] + d1[1]) / 2)
        cm = ((m0[0] + m1[0]) / 2, (m0[1] + m1[1]) / 2)
        ux, uy = dx - cd[0], dy - cd[1]
        c, s = math.cos(delta), math.sin(delta)
        x, y = c * ux - s * uy + cm[0], s * ux + c * uy + cm[1]
        if self.K:
            x, y = self._command((x, y))
        return x, y, math.degrees(delta) + base
