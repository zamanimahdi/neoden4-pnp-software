"""Camera settling: wait until the image stops moving instead of a fixed delay.

NeoDen4.exe sleeps a fixed 250 ms after every move before it takes a vision picture. That
is too long when the machine is quiet and too short when the head still rings after a long
fast move. grab_settled() grabs frames until the image no longer moves: the shift between
two consecutive frames (phase correlation on a reduced image, CPU-light) must stay below
tol_px for `stable` frames in a row. It returns the last frame and what it saw.

    from neocalib.settle import grab_settled
    r = grab_settled(grab)              # grab() -> gray frame
    if not r.settled: ...               # still moving after max_s (the frame is returned anyway)
    img = r.frame

method="diff" uses the mean absolute grey difference instead (for scenes with too little
texture for a shift). settle_profile() measures the settle time after moves of several
lengths, to choose tol_px / max_s and to see how the machine rings.
"""
import time
from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class SettleResult:
    frame: object
    settled: bool
    time_s: float                   # from the call to the returned frame
    frames: int
    motion: float                   # last measured motion (px, or grey levels for "diff")
    method: str
    history: list = field(default_factory=list)     # (t_s, motion)


class _Reducer:
    """Grey frame -> small float32 image (+ Hann window), reused between frames."""

    def __init__(self, scale, roi):
        self.scale, self.roi, self.win = max(1, int(scale)), roi, None

    def __call__(self, img):
        h, w = img.shape[:2]
        mh, mw = int(h * (1 - self.roi) / 2), int(w * (1 - self.roi) / 2)
        img = img[mh:h - mh, mw:w - mw]
        if self.scale > 1:
            img = cv2.resize(img, (img.shape[1] // self.scale, img.shape[0] // self.scale),
                             interpolation=cv2.INTER_AREA)
        f = img.astype(np.float32)
        f -= f.mean()
        if self.win is None or self.win.shape != f.shape:
            self.win = cv2.createHanningWindow((f.shape[1], f.shape[0]), cv2.CV_32F)
        return f


def _motion(prev, cur, red, method):
    if method == "diff":
        return float(np.mean(np.abs(cur - prev)))
    (dx, dy), _ = cv2.phaseCorrelate(prev, cur, red.win)
    return float(np.hypot(dx, dy) * red.scale)                     # in full-resolution px


def grab_settled(grab, tol_px=0.3, stable=2, max_s=1.0, min_s=0.0, scale=4, roi=0.8,
                 method="shift", tol_diff=1.5):
    """Grab until the image stops moving. tol_px: allowed shift between frames (full-res px);
    stable: consecutive quiet frame pairs needed; max_s: give up and return the last frame;
    min_s: never return earlier (e.g. a light that needs time); method "shift" or "diff"."""
    red = _Reducer(scale, roi)
    tol = tol_diff if method == "diff" else tol_px
    t0 = time.monotonic()
    frame = grab()
    prev = red(frame)
    n, quiet, hist, m = 1, 0, [], float("inf")
    while True:
        frame = grab()
        n += 1
        cur = red(frame)
        m = _motion(prev, cur, red, method)
        t = time.monotonic() - t0
        hist.append((t, m))
        prev = cur
        quiet = quiet + 1 if m <= tol else 0
        if quiet >= stable and t >= min_s:
            return SettleResult(frame, True, t, n, m, method, hist)
        if t >= max_s:
            return SettleResult(frame, False, t, n, m, method, hist)


def settle_profile(mb, grab, distances=(1.0, 5.0, 20.0, 80.0), axis="x", repeats=2, **kw):
    """Move away and back by each distance (direct moves, no slow approach) and measure how
    long the image takes to settle. Returns [(distance, mean_s, max_s, settled_all)]."""
    start = mb.position()
    if start is None:
        raise RuntimeError("position unknown: home the machine first")
    out = []
    for d in distances:
        times, ok = [], True
        for _ in range(repeats):
            dx, dy = (d, 0.0) if axis == "x" else (0.0, d)
            mb.move_xy(start[0] + dx, start[1] + dy)
            mb.move_xy(*start)
            r = grab_settled(grab, **kw)
            times.append(r.time_s)
            ok = ok and r.settled
        out.append((d, float(np.mean(times)), float(np.max(times)), ok))
    return out
