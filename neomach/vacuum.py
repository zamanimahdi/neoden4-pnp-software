"""Vacuum pick detection from pressure edges and time integrals, with errors and an
automatic threshold per nozzle.

NeoDen4.exe decides "part picked" with a fixed threshold (job column, default -40): the
average of 10 pressure reads must be <= it (CFunNodeHead::vacuumBelow). That fails when a
nozzle's open-air level or a part's sealed level is far from the default. Here:

  1. suction is switched on before the nozzle touches the part, so every pick first
     measures this nozzle's open-air level and noise ("open" phase);
  2. touching the part seals the tip: the falling pressure edge is found relative to that
     level and noise ("down" phase); the sealed level and the drop come from it;
  3. after lifting ("up" phase) the decision is an INTEGRAL, not one reading: the
     time-weighted seal fraction  S = mean over time of (open - p) / (open - sealed)
     (1 = fully sealed, 0 = open air). Held if S >= held_fraction (automatic threshold =
     midpoint between this pick's open and sealed levels);
  4. errors, each with its own reason: no pressure drop (missed part / empty pocket),
     weak seal (drop much smaller than this nozzle usually gets: tilted or leaking part),
     lost on the way up, clogged nozzle (open-air level already near the sealed level);
  5. VacuumWatch integrates the leak while the part travels (to the camera, to the board):
     L = integral of max(0, p - sealed) / drop dt. A slipping or falling part pushes L past
     the limit and raises an error.

VacuumLearner keeps per-nozzle open / sealed levels and typical drop from good picks.
Pressure values are reg 0 b0..b3 (signed bytes, more negative = stronger vacuum). All
numbers are guesses until measured on the machine; they are all in VacuumParams.
"""
import json
from dataclasses import asdict, dataclass, field
from statistics import median


@dataclass
class VacuumParams:
    min_drop: float = 8.0           # smallest fall below the open level that counts as a seal
    noise_k: float = 4.0            # ... and it must also exceed noise_k * open-level noise
    hold: int = 3                   # samples the fall must persist (rejects spikes)
    smooth: int = 3                 # moving-average length before edge search
    held_fraction: float = 0.5      # held if the after-lift seal fraction S >= this
    weak_fraction: float = 0.6      # weak seal if drop < this * the nozzle's typical drop
    clog_fraction: float = 0.5      # clogged if the open level is this far toward the sealed level
    leak_limit_s: float = 0.03      # VacuumWatch: allowed leak integral (seconds of open air)
    learn_rate: float = 0.2         # EMA weight of a new good pick in VacuumLearner
    open_ms: int = 80               # suction on, nozzle up: time before the baseline ends
    settle_ms: int = 60             # after the nozzle is back up, sample this long
    integrate_ms: int = 50          # window (end of the "up" phase) for the seal integral


@dataclass
class PickResult:
    held: bool
    reason: str
    nozzle: int = 0
    open_level: float = 0.0
    open_noise: float = 0.0
    sealed_level: float = None
    drop: float = None              # open - sealed
    threshold: float = None         # automatic: open - held_fraction * drop
    seal_down: float = None         # seal fraction integral while down (after the edge)
    seal_up: float = None           # seal fraction integral after lifting (the decision)
    edge_time: float = None         # seconds from the start of the "down" phase
    weak: bool = False
    clogged: bool = False
    samples: list = field(default_factory=list, repr=False)    # (t, value, phase)


def robust_level(values):
    """(median, sigma) with sigma from the median absolute deviation (at least 0.5)."""
    if not values:
        return 0.0, 0.5
    m = median(values)
    mad = median(abs(v - m) for v in values)
    return float(m), max(0.5, 1.4826 * mad)


def _smooth(values, n):
    if n <= 1:
        return list(values)
    out, acc = [], []
    for v in values:
        acc.append(v)
        if len(acc) > n:
            acc.pop(0)
        out.append(sum(acc) / len(acc))
    return out


def find_falling_edge(values, base, sigma, p):
    """Index of the first sample where the smoothed pressure falls below
    base - max(min_drop, noise_k * sigma) and stays there for `hold` samples; else None."""
    thr = base - max(p.min_drop, p.noise_k * sigma)
    sm = _smooth(values, p.smooth)
    run = 0
    for i, v in enumerate(sm):
        run = run + 1 if v <= thr else 0
        if run >= p.hold:
            return i - p.hold + 1
    return None


def seal_fraction(tv, open_level, sealed_level):
    """Time-weighted mean of (open - p) / (open - sealed) over [(t, p)] (trapezoid rule),
    clipped to 0..1 per sample. One sample: its own value. Empty: None."""
    drop = open_level - sealed_level
    if not tv or drop <= 0:
        return None
    f = [min(1.0, max(0.0, (open_level - v) / drop)) for _, v in tv]
    if len(tv) == 1:
        return f[0]
    area = sum((tv[i + 1][0] - tv[i][0]) * (f[i] + f[i + 1]) / 2 for i in range(len(tv) - 1))
    span = tv[-1][0] - tv[0][0]
    return area / span if span > 0 else sum(f) / len(f)


def _window(tv, ms):
    if not tv:
        return tv
    t_end = tv[-1][0]
    w = [(t, v) for t, v in tv if t >= t_end - ms / 1000]
    return w if len(w) >= 2 else tv[-2:]


def analyze_pick(samples, p=None, nozzle=0, learner=None):
    """samples: [(t, value, phase)] with phases "open", "down", "up". Returns PickResult."""
    p = p or VacuumParams()
    opn = [v for t, v, ph in samples if ph == "open"]
    down = [(t, v) for t, v, ph in samples if ph == "down"]
    up = [(t, v) for t, v, ph in samples if ph == "up"]
    r = PickResult(False, "", nozzle, samples=samples)
    if len(opn) >= 3:
        r.open_level, r.open_noise = robust_level(opn[len(opn) // 2:])     # settled half
    elif learner and learner.knows(nozzle):
        r.open_level, r.open_noise = learner.open[nozzle], 1.0
    else:
        r.reason = "no open-air baseline (suction was not on before contact, nothing learned yet)"
        return r
    if learner and learner.knows(nozzle):
        r.clogged = learner.is_clogged(nozzle, r.open_level, p)
    if not down:
        r.reason = "no samples while the nozzle was down"
        return r
    i = find_falling_edge([v for _, v in down], r.open_level, r.open_noise, p)
    if i is None:
        r.reason = "no pressure drop: the nozzle did not seal on a part (missed part / empty pocket)"
        if r.clogged:
            r.reason += "; the open-air level is already low: nozzle or filter clogged?"
        return r
    r.edge_time = down[i][0] - down[0][0]
    after_edge = down[i + p.hold:] or down[i:]
    # sealed level from settled samples: the last half after the edge, or after lifting if
    # that is lower (a held part keeps settling); a lost part makes "up" rise, so min() keeps "down"
    settled = [v for _, v in after_edge[len(after_edge) // 2:]] or [v for _, v in after_edge]
    r.sealed_level = float(median(settled))
    if up:
        r.sealed_level = min(r.sealed_level, float(median(v for _, v in _window(up, p.integrate_ms))))
    r.drop = r.open_level - r.sealed_level
    r.threshold = r.open_level - p.held_fraction * r.drop
    r.seal_down = seal_fraction(after_edge, r.open_level, r.sealed_level)
    if learner and learner.knows(nozzle):
        r.weak = r.drop < p.weak_fraction * learner.typical_drop(nozzle)
    if not up:
        r.reason = "no samples after lifting"
        return r
    r.seal_up = seal_fraction(_window(up, p.integrate_ms), r.open_level, r.sealed_level)
    if r.seal_up < p.held_fraction:
        r.reason = (f"lost on the way up: seal integral {r.seal_up:.2f} < {p.held_fraction:.2f} "
                    "(pressure went back toward the open-air level)")
        return r
    if r.weak:
        r.reason = (f"weak seal: drop {r.drop:.0f} is below {p.weak_fraction:.0%} of this nozzle's "
                    f"usual {learner.typical_drop(nozzle):.0f} (part tilted, leaking or wrong nozzle?)")
        return r
    r.held = True
    r.reason = "ok"
    return r


class VacuumWatch:
    """Leak integral while a held part travels. feed((t, p)) per pressure read; ok is False
    once the integral of max(0, p - sealed) / drop dt passes leak_limit_s."""

    def __init__(self, result_or_levels, p=None):
        self.p = p or VacuumParams()
        if isinstance(result_or_levels, PickResult):
            self.open, self.sealed = result_or_levels.open_level, result_or_levels.sealed_level
        else:
            self.open, self.sealed = result_or_levels
        self.drop = max(1e-6, self.open - self.sealed)
        self.leak = 0.0
        self.last = None
        self.worst = 0.0

    def feed(self, t, v):
        f = min(1.0, max(0.0, (v - self.sealed) / self.drop))      # 0 sealed .. 1 open air
        self.worst = max(self.worst, f)
        if self.last is not None:
            t0, f0 = self.last
            self.leak += max(0.0, t - t0) * (f0 + f) / 2
        self.last = (t, f)
        return self.ok

    @property
    def ok(self):
        return self.leak <= self.p.leak_limit_s


class VacuumLearner:
    """Per-nozzle open / sealed levels learned from good picks; JSON save / load."""

    def __init__(self, params=None):
        self.p = params or VacuumParams()
        self.open, self.sealed, self.count = {}, {}, {}

    def knows(self, n):
        return self.count.get(n, 0) > 0

    def update(self, r):
        if not (r.held and r.sealed_level is not None):
            return
        n, a = r.nozzle, self.p.learn_rate
        if not self.knows(n):
            self.open[n], self.sealed[n] = r.open_level, r.sealed_level
        else:
            self.open[n] += a * (r.open_level - self.open[n])
            self.sealed[n] += a * (r.sealed_level - self.sealed[n])
        self.count[n] = self.count.get(n, 0) + 1

    def typical_drop(self, n):
        return self.open[n] - self.sealed[n]

    def threshold(self, n, default=-40):
        """Automatic threshold for a check without a fresh baseline (e.g. before placing)."""
        if not self.knows(n):
            return default
        return self.open[n] - self.p.held_fraction * self.typical_drop(n)

    def is_clogged(self, n, open_now, p=None):
        p = p or self.p
        if not self.knows(n):
            return False
        span = self.typical_drop(n)
        return span > 0 and open_now <= self.open[n] - p.clog_fraction * span

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"params": asdict(self.p),
                       "nozzles": {str(n): {"open": self.open[n], "sealed": self.sealed[n],
                                            "count": self.count[n]} for n in self.count}}, f, indent=1)

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        lr = cls(VacuumParams(**d.get("params", {})))
        for k, v in d.get("nozzles", {}).items():
            n = int(k)
            lr.open[n], lr.sealed[n], lr.count[n] = v["open"], v["sealed"], v["count"]
        return lr
