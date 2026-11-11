"""Can the head get there: every feeder's pick point (each tray's corner cells; not a bulk feeder's, it picks in its container) for each nozzle its parts
use, against the machine's XY travel (neomach.proto: 0 .. 416.24 x 0 .. 400.05 mm, the head's point is the
pick point plus that nozzle's offset). Checked when the job is saved and before it starts, so a feeder
set where one nozzle cannot reach shows up at the feeder set-up, not halfway through a board.

    problems(job, cfg) -> ["feeder 49 (0603 ...): nozzle 4 at (30.00, 407.15) is 7.10 mm past Y 400.05", ...]
"""
from neomach.proto import X_MAX_MM, Y_MAX_MM


def _cells(st):
    if st.type != 1:
        return [(st.x, st.y)]
    xs = [st.x, st.end_x] if st.cols > 1 else [st.x]
    ys = [st.y, st.end_y] if st.rows > 1 else [st.y]
    return [(x, y) for x in xs for y in ys]


def _past(x, y):
    out = []
    if x < 0:
        out.append(f"{-x:.2f} mm past X 0")
    if x > X_MAX_MM:
        out.append(f"{x - X_MAX_MM:.2f} mm past X {X_MAX_MM:.2f}")
    if y < 0:
        out.append(f"{-y:.2f} mm past Y 0")
    if y > Y_MAX_MM:
        out.append(f"{y - Y_MAX_MM:.2f} mm past Y {Y_MAX_MM:.2f}")
    return ", ".join(out)


def problems(job, cfg, only=None):
    """as the job would run (a test run uses the skipped feeders and parts too); only: that part alone"""
    test = bool(getattr(job, "test", False))
    nozzles = {}
    for i, c in enumerate(job.comps):
        if only is not None and i != only:
            continue
        if (not c.skip or test or only is not None) and 1 <= c.nozzle <= 4:
            nozzles.setdefault(c.stack, set()).add(c.nozzle)
    out = []
    for sid in sorted(nozzles):
        st = job.stack(sid)
        if st is None or (st.skip and not test and only is None) or getattr(st, "bulk", 0):
            continue                      # a bulk feeder picks in its container (scanned), not at its X / Y
        for n in sorted(nozzles[sid]):
            o = cfg.nozzle_offset(n)
            for x, y in _cells(st):
                why = _past(x + o[0], y + o[1])
                if why:
                    out.append(f"feeder {sid} ({st.footprint or '?'} {st.value or ''}".rstrip() +
                               f"): nozzle {n} at ({x + o[0]:.2f}, {y + o[1]:.2f}) is {why}")
    return out


def part_height(st, comp, packages):
    """the part's height (mm): the feeder's own (part size H), else the footprint library's; 0 unknown"""
    pk = (packages or {}).get(st.footprint) or (packages or {}).get(comp.footprint)
    return getattr(st, "part_h", 0) or (pk[2] if pk and len(pk) > 2 and pk[2] else 0.0)


def feeder_issues(job, cfg, packages, heights=True, only=None):
    """what a used feeder still needs before the job: {feeder id: ["no part height", "nozzle 1 at ... past Y"]}
    (the feeder list marks them; the job does not start with them)"""
    test = bool(getattr(job, "test", False))
    first = {}
    for i, c in enumerate(job.comps):
        if (i == only) if only is not None else (not c.skip or test):
            first.setdefault(c.stack, c)
    out = {}
    for sid, c in first.items():
        st = job.stack(sid)
        if st is None or (st.skip and not test and only is None):
            continue
        if heights and not getattr(st, "bulk", 0) and not part_height(st, c, packages):
            out.setdefault(sid, []).append("no part height (part size H)")
    for p in problems(job, cfg, only):
        sid = int(p.split(" ", 2)[1])
        out.setdefault(sid, []).append(p.split("): ", 1)[1] if "): " in p else p)
    return out
