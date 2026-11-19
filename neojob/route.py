"""The order of a job's parts for the least head travel, lower parts first.

    python -m neojob.route JOB.csv CONFIG.ini [--write]        # travel now / sorted; --write: the job file re-ordered

What the head travels in a pick cycle: feeders -> up camera (each nozzle over it) -> place points -> the next
cycle's feeders. The run groups the list's parts into cycles itself (neojob.assign: distinct nozzles, a few
parts ahead), so the list's ORDER decides what travels together. sort() orders it so that
  - the parts go in height tiers, low first (a tier: within TIER_MM of its lowest part, the run's own rule
    for "may be placed in any order"); tall parts (Job/fTallPartMm) stay last (the run does that anyway),
  - inside a tier every cycle is filled (as many nozzles as its feeders allow), with parts whose feeders lie
    on the same side of the table and whose place points lie together.
travel() is the measure (mm; both axes move at once, so a move costs its longer side): the list as the run
would work through it (plan_nozzles + cycles). Only the comp lines change places in the file.
"""
import copy
import itertools
import math

from . import assign as A

TIER_MM = 1.0


def _cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


class Model:
    """where the head (nozzle 1's frame) is for each thing a part needs"""

    def __init__(self, job, cfg):
        self.job, self.cfg = job, cfg
        self.off = {n: cfg.nozzle_offset(n) for n in (1, 2, 3, 4)}
        self.cam = {n: cfg.nozzle_positions[n - 1] for n in (1, 2, 3, 4)}
        P = job.panels[0] if job.panels else None
        C = job.anchor()
        self.shift = (P.x - C[0], P.y - C[1]) if P else (0.0, 0.0)
        self.start = self.cam[1]

    def pick(self, i, n):
        st = self.job.stack(self.job.comps[i].stack)
        return (st.x + self.off[n][0], st.y + self.off[n][1])

    def place(self, i, n):
        c = self.job.comps[i]
        return (c.x + self.shift[0] + self.off[n][0], c.y + self.shift[1] + self.off[n][1])

    def allowed(self, i):
        st = self.job.stack(self.job.comps[i].stack)
        return [int(ch) for ch in str(st.nozzles) if ch in "1234"] or [1]

    def height(self, i):
        c = self.job.comps[i]
        return float(getattr(self.job.stack(c.stack), "part_h", 0) or 0)

    def cycle(self, pos, members):
        """(mm, end position) of one cycle: members = [(comp index, nozzle)], picked nearest first, each
        over the camera in that order, placed nearest first"""
        d, here, left = 0.0, pos, list(members)
        order = []
        while left:
            k = min(left, key=lambda m: _cheb(here, self.pick(*m)))
            left.remove(k)
            order.append(k)
            d += _cheb(here, self.pick(*k))
            here = self.pick(*k)
        for (i, n) in order:
            d += _cheb(here, self.cam[n])
            here = self.cam[n]
        left = list(order)
        while left:
            k = min(left, key=lambda m: _cheb(here, self.place(*m)))
            left.remove(k)
            d += _cheb(here, self.place(*k))
            here = self.place(*k)
        return d, here

    def best(self, pos, idx):
        """the cheapest nozzle choice for these parts together: (mm, end, [(index, nozzle)]) or None"""
        out = None
        for ns in itertools.product(*[self.allowed(i) for i in idx]):
            if len(set(ns)) < len(ns):
                continue
            d, end = self.cycle(pos, list(zip(idx, ns)))
            if out is None or d < out[0]:
                out = (d, end, list(zip(idx, ns)))
        return out


def placeable(job):
    return [i for i, c in enumerate(job.comps) if not c.skip and job.stack(c.stack) is not None
            and not job.stack(c.stack).skip]


def travel(job, cfg, look=None, mix=None):
    """(mm, cycles) of the job as the run works through its list now"""
    j = copy.deepcopy(job)
    A.plan_nozzles(j)
    look = int(cfg.job("nCycleLookahead") or 0) if look is None else look
    mix = bool(cfg.job("bCycleMixSpeeds")) if mix is None else mix
    m = Model(j, cfg)
    tall = float(cfg.job("fTallPartMm") or 0.0)
    cyc = A.cycles(j, look, mix)
    if tall > 0:                                          # the run: tall parts after all the others, each alone
        low = [[i for i in c if m.height(i) < tall] for c in cyc]
        cyc = [c for c in low if c] + [[i] for c in cyc for i in c if m.height(i) >= tall]
    d, pos = 0.0, m.start
    for c in cyc:
        dd, pos = m.cycle(pos, [(i, j.comps[i].nozzle) for i in c])
        d += dd
    return d, len(cyc)


def tiers(m, idx, tall=0.0):
    """the parts in height tiers, low first; the tall ones a tier of their own at the end"""
    low = sorted((i for i in idx if not (tall > 0 and m.height(i) >= tall)), key=lambda i: (m.height(i), i))
    out = []
    for i in low:
        if out and m.height(i) - m.height(out[-1][0]) <= TIER_MM:
            out[-1].append(i)
        else:
            out.append([i])
    high = [i for i in idx if tall > 0 and m.height(i) >= tall]
    return out + ([high] if high else [])


def loop(m, idx):
    """what these parts cost as one cycle, there and back: from its first feeder, over the camera, onto the
    board and back to that feeder (mm); None when their feeders' nozzles do not go together"""
    out = None
    for ns in itertools.product(*[m.allowed(i) for i in idx]):
        if len(set(ns)) < len(ns):
            continue
        mem = list(zip(idx, ns))
        start = m.pick(*mem[0])
        d, end = m.cycle(start, mem)
        d += _cheb(end, start)
        if out is None or d < out:
            out = d
    return out


def sort(job, cfg, size=4, tier_mm=None):
    """the placeable parts' indices in the new order. Inside a height tier the cycles are made by savings:
    every part starts as a cycle of its own, and the two cycles that save the most head travel by going
    together are joined, again and again, while their feeders' nozzles allow it and something is saved.
    The far cycles go first; inside a cycle the lowest part first"""
    global TIER_MM
    keep = TIER_MM
    if tier_mm is not None:
        TIER_MM = tier_mm
    try:
        j = copy.deepcopy(job)
        m = Model(j, cfg)
        tall = float(cfg.job("fTallPartMm") or 0.0)
        order = []
        for tier in tiers(m, placeable(j), tall):
            if tall > 0 and m.height(tier[0]) >= tall:
                order += tier                              # the run picks their order itself (next_tall)
                continue
            groups = [[i] for i in tier]
            cost = {(i,): loop(m, [i]) for i in tier}
            key = lambda g: tuple(sorted(g))
            while True:
                best = None
                for a in range(len(groups)):
                    for b in range(a + 1, len(groups)):
                        g = groups[a] + groups[b]
                        if len(g) > size:
                            continue
                        k = key(g)
                        if k not in cost:
                            cost[k] = loop(m, g)
                        if cost[k] is None:
                            continue
                        sv = cost[key(groups[a])] + cost[key(groups[b])] - cost[k]
                        if sv > 0 and (best is None or sv > best[0]):
                            best = (sv, a, b)
                if best is None:
                    break
                _, a, b = best
                groups[a] = groups[a] + groups[b]
                del groups[b]
            groups.sort(key=lambda g: (-cost[key(g)], min(g)))
            for g in groups:
                order += sorted(g, key=lambda i: (m.height(i), i))
        return order
    finally:
        TIER_MM = keep


def reorder_lines(lines, job, order):
    """the job file's lines with the comp lines in the new order (every other line where it was; the parts
    not in `order` after them, in their old order)"""
    slots = [n for n, l in enumerate(lines) if l.split(",")[0].strip() == "comp"]       # the parts' lines, in the list's order
    if len(slots) != len(job.comps):
        raise ValueError(f"{len(slots)} comp lines in the file, {len(job.comps)} parts read from it")
    rest = [i for i in range(len(job.comps)) if i not in set(order)]
    new = [slots[i] for i in list(order) + rest]
    out = list(lines)
    for dst, src in zip(slots, new):
        out[dst] = lines[src]
    return out


def main(argv=None):
    import argparse
    from neocam.config import NeoConfig
    from .jobfile import load
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("job")
    ap.add_argument("config")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    job, cfg = load(a.job), NeoConfig.load(a.config)
    d0, c0 = travel(job, cfg)
    order = sort(job, cfg)
    raw = open(a.job, encoding="utf-8", newline="").read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    lines = reorder_lines(raw.split(nl), job, order)
    tmp = a.job + ".route.tmp"
    open(tmp, "w", encoding="utf-8", newline="").write(nl.join(lines))
    d1, c1 = travel(load(tmp), cfg)
    print(f"now:    {d0 / 1000:.1f} m of head travel in {c0} cycles")
    print(f"sorted: {d1 / 1000:.1f} m in {c1} cycles ({(1 - d1 / d0) * 100:.0f} % less)")
    import os
    if a.write:
        os.replace(tmp, a.job)
        print("written:", a.job)
    else:
        os.remove(tmp)


if __name__ == "__main__":
    main()
