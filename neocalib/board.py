"""Board (fiducial) transform: design coordinates -> machine coordinates.

NeoDen4.exe (calcTransformFromTwoPairs 0x00402b30) uses two marks and applies only a
rotation and a shift: it computes the distance between the marks but never uses it, so
any scale difference between the design and the machine stays as a placement error.

BoardTransform:
  1 mark   shift
  2 marks  rotation + shift + scale along the line between the marks
           (two_mark_scale="line"; "uniform" scales both directions; "none" = NeoDen)
  3+ marks full affine fit (scale X / Y, skew, rotation, shift) by least squares, with the
           residual of every mark (a bad / misread mark shows a large residual)

apply(x, y) -> machine (X, Y); angle_at(x, y) -> the local board rotation (deg) to add to
the part angle (NeoDen adds its pcbRot the same way).
"""
import math

import numpy as np


class BoardTransform:
    def __init__(self, design, measured, two_mark_scale="line"):
        d = np.asarray(design, float).reshape(-1, 2)
        m = np.asarray(measured, float).reshape(-1, 2)
        if len(d) != len(m) or len(d) == 0:
            raise ValueError("need the same number (>= 1) of design and measured marks")
        self.n, self.mode = len(d), two_mark_scale
        self.residual = np.zeros(len(d))
        if self.n == 1:
            self.M = np.eye(2)
            self.t = m[0] - d[0]
        elif self.n == 2:
            dd, md = d[1] - d[0], m[1] - m[0]
            a = math.atan2(md[1], md[0]) - math.atan2(dd[1], dd[0])
            R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
            s = np.linalg.norm(md) / np.linalg.norm(dd)
            if two_mark_scale == "none":
                S = np.eye(2)
            elif two_mark_scale == "uniform":
                S = s * np.eye(2)
            else:                                           # along the mark line only
                u = md / np.linalg.norm(md)
                S = np.eye(2) + (s - 1) * np.outer(u, u)
            self.M = S @ R
            self.t = (m[0] + m[1]) / 2 - self.M @ ((d[0] + d[1]) / 2)
            self.scale_line = float(s)
        else:
            X = np.hstack([d, np.ones((self.n, 1))])
            sol, *_ = np.linalg.lstsq(X, m, rcond=None)
            self.M, self.t = sol[:2].T, sol[2]
            self.residual = np.linalg.norm(X @ sol - m, axis=1)

    def apply(self, x, y):
        v = self.M @ np.array([x, y], float) + self.t
        return float(v[0]), float(v[1])

    def angle_at(self, x=0.0, y=0.0):
        """Board rotation (deg): mean turn of the design X and Y directions."""
        ax = math.degrees(math.atan2(self.M[1, 0], self.M[0, 0]))
        ay = math.degrees(math.atan2(-self.M[0, 1], self.M[1, 1]))
        return (ax + ay) / 2

    @property
    def scale(self):
        """(scale along design X, along design Y)."""
        return float(np.linalg.norm(self.M[:, 0])), float(np.linalg.norm(self.M[:, 1]))

    @property
    def skew_deg(self):
        cx, cy = self.M[:, 0], self.M[:, 1]
        return math.degrees(math.acos(np.clip(cx @ cy / (np.linalg.norm(cx) * np.linalg.norm(cy)), -1, 1))) - 90

    def bad_marks(self, tol_mm=0.05):
        """Indices of marks whose fit residual exceeds tol (3+ marks)."""
        return [i for i, r in enumerate(self.residual) if r > tol_mm]

    def summary(self):
        sx, sy = self.scale
        s = f"{self.n} marks: rotation {self.angle_at():+.4f} deg, scale X {(sx - 1) * 100:+.3f} %, Y {(sy - 1) * 100:+.3f} %"
        if self.n >= 3:
            s += f", skew {self.skew_deg:+.4f} deg, mark residual max {self.residual.max() * 1000:.1f} um"
        return s
