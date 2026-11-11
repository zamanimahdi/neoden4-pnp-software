"""Assign: feeders and nozzles to the parts, and the machine's feeder settings to the job's feeders
(NeoDen4's "Assign Feeder and Nozzle", plus the feeder data). Run at every job start, and from the
job file editor (where the result is saved into the file).

feeder data: the job file comes first. For every tape stack, the feed amount (mm), feed torque and
peel torque the job's stack line gives are used; only a value the line does not have (an old or
short line) is taken from the machine's feeder table (Config.ini Feed/lstFeed<id-1>.nLoad / .nMoment,
Peel/lstPeel<id-1>.nMoment, Settings -> Feeders / Peelers) for the physical feeder / peeler the stack
uses (Stack/lstStack.nFeedId / nPeelId, default = the stack number). These go to the feeder with
every feed command.

parts: each part's feeder is the stack whose footprint / value is the part's ("FP/VALUE" in the stack
value as NeoDen4 writes it, or the two fields; a stack without a footprint matches on the value alone).
A part whose own stack still matches keeps it, even when another stack has the same value (two
feeders of 100N are the job author's plan); only a part whose stack does not match at all moves. Its
nozzle must be one the stack allows (the stack's nozzle digits); otherwise the first allowed one.

    report = assign(job, cfg)          # job: neojob.jobfile.Job, cfg: neocam.config.NeoConfig
"""


def _norm(s):
    return " ".join(str(s or "").split()).lower()


def stack_key(st):
    """(footprint, value) of a stack: NeoDen4 often keeps "FP/VALUE" in the value field"""
    fp, val = _norm(st.footprint), _norm(st.value)
    if not fp and "/" in val:
        fp, val = val.split("/", 1)
    return fp.strip(), val.strip()


def matches(st, comp):
    """a stack without a footprint matches on the value alone (many jobs leave the footprint empty)"""
    fp, val = stack_key(st)
    return val == _norm(comp.value) and (not fp or fp == _norm(comp.footprint))


def feeder_data(job, cfg):
    """the machine feeder table into the job's tape stacks; returns [(stack id, field, old, new)]"""
    out = []
    for sid, st in sorted(job.stacks.items()):
        if st.type != 0:
            continue
        fid = int(float(cfg.get(f"Stack/lstStack{sid - 1}.nFeedId", sid) or sid))
        pid = int(float(cfg.get(f"Stack/lstStack{sid - 1}.nPeelId", sid) or sid))
        tok = list(getattr(st, "tokens", []) or [])
        for ti, field, key in ((16, "pitch", f"Feed/lstFeed{fid - 1}.nLoad"), (17, "feed_strength", f"Feed/lstFeed{fid - 1}.nMoment"),
                               (18, "peel_strength", f"Peel/lstPeel{pid - 1}.nMoment")):
            if len(tok) > ti and str(tok[ti]).strip() != "":
                continue                                          # the job file has it: the job comes first
            v = cfg.get(key, None)
            if v in (None, ""):
                continue
            v = int(float(v))
            if v > 0 and v != getattr(st, field):
                out.append((sid, field, getattr(st, field), v))
                setattr(st, field, v)
    return out


def parts(job):
    """feeder and nozzle of every part; returns ([(index, field, old, new)], [names without a feeder])"""
    changes, missing = [], []
    stacks = [st for _, st in sorted(job.stacks.items())]
    for i, c in enumerate(job.comps):
        if c.skip:
            continue
        cur = job.stacks.get(c.stack)
        if cur is None or not matches(cur, c):
            cand = [st for st in stacks if matches(st, c)]
            pick = next((st for st in cand if not st.skip), cand[0] if cand else None)
            if pick is None:
                if cur is None:
                    missing.append(c.name)
                continue
            changes.append((i, "stack", c.stack, pick.id))
            c.stack, cur = pick.id, pick
        allowed = [int(ch) for ch in str(cur.nozzles) if ch in "1234"]
        if allowed and c.nozzle not in allowed:
            changes.append((i, "nozzle", c.nozzle, allowed[0]))
            c.nozzle = allowed[0]
    return changes, missing


def cycles(job, look=0, mix=True):
    """the pick cycles the run makes (its grouping: distinct nozzles, up to 4, from `look` parts ahead)"""
    out, tried, c, n = [], set(), 0, len(job.comps)
    ok = lambda i: (not job.comps[i].skip and job.stack(job.comps[i].stack) is not None
                    and not job.stack(job.comps[i].stack).skip and 1 <= job.comps[i].nozzle <= 4)
    while c < n:
        slots, i, first = [], c, None
        while i < n and len(slots) < 4:
            if i in tried or not ok(i):
                i += 1
                continue
            comp, st = job.comps[i], job.stack(job.comps[i].stack)
            if slots and (comp.nozzle in [s[1] for s in slots] or (not mix and st.speed != job.stack(job.comps[slots[0][0]].stack).speed)):
                first = i if first is None else first
                if not look or i - first >= look:
                    break
                i += 1
                continue
            slots.append((i, comp.nozzle))
            i += 1
        c = first if first is not None else i
        if not slots:
            break
        tried.update(s[0] for s in slots)
        out.append([s[0] for s in slots])
    return out


def plan_nozzles(job, window=8):
    """each part (in placement order) an allowed nozzle not yet used in its cycle, so up to four are picked
    together; among the free ones the nozzle the next parts can least do without. Returns the changes"""
    changes, used = [], set()
    todo = [i for i, c in enumerate(job.comps) if not c.skip and job.stack(c.stack) is not None and not job.stack(c.stack).skip]
    allowed = {i: [int(ch) for ch in str(job.stack(job.comps[i].stack).nozzles) if ch in "1234"] or [1] for i in todo}
    for k, i in enumerate(todo):
        free = [n for n in allowed[i] if n not in used]
        if not free:                                     # this cycle is full for this part: a new one
            used, free = set(), list(allowed[i])
        ahead = todo[k + 1:k + 1 + window]
        need = {n: sum(1.0 / len(allowed[j]) for j in ahead if n in allowed[j]) for n in free}
        n = min(free, key=lambda x: (need.get(x, 0), x))
        c = job.comps[i]
        if c.nozzle != n:
            changes.append((i, "nozzle", c.nozzle, n))
            c.nozzle = n
        used.add(n)
        if len(used) >= 4 or not [m for m in range(1, 5) if m not in used]:
            used = set()
    return changes


def assign(job, cfg):
    fd = feeder_data(job, cfg)
    pc, missing = parts(job)
    look = int(cfg.job("nCycleLookahead") or 0)
    mix = bool(cfg.job("bCycleMixSpeeds"))
    before = len(cycles(job, look, mix))
    if cfg.job("bPlanNozzles"):
        planned = plan_nozzles(job)
        last = {}
        for i, f, old, new in pc + planned:                # one change per part: from the first old value
            last[(i, f)] = (last.get((i, f), (old, new))[0], new)
        pc = [(i, f, o, n) for (i, f), (o, n) in last.items() if o != n]
    after = len(cycles(job, look, mix))
    named = [(job.comps[i].name, field, old, new) for i, field, old, new in pc]
    return {"feeder_data": fd, "parts": named, "no_feeder": missing, "cycles": [before, after]}


def summary(rep):
    n_fd = len({s for s, *_ in rep["feeder_data"]})
    t = [f"assign: feeder data missing in the job taken from the machine's feeder table on {n_fd} feeder(s)"
         if n_fd else "assign: feeder data from the job file"]
    if rep["parts"]:
        t.append(f"assign: {len(rep['parts'])} part feeder / nozzle change(s): " +
                 "; ".join(f"{name} {field} {old} -> {new}" for name, field, old, new in rep["parts"]))
    if rep.get("cycles") and rep["cycles"][0] != rep["cycles"][1]:
        t.append(f"assign: pick cycles {rep['cycles'][0]} -> {rep['cycles'][1]} (the nozzles planned to pick together)")
    if rep["no_feeder"]:
        t.append(f"assign: no feeder for {', '.join(rep['no_feeder'][:10])}" + (" ..." if len(rep["no_feeder"]) > 10 else ""))
    return t
