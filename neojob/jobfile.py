"""NeoDen4 job file (Proc/*.csv): read, and write back the run state (JOB_SPEC.md section 1).

Only what the runner needs is interpreted; every line is kept, so saving changes only the
stack lines whose tray counters moved and the skip flags of panels.
"""
import os
from dataclasses import dataclass, field

YES = ("yes", "是")
SKIP_WORDS = ("yes", "是")



def _check(v):
    """an after-place check: "" (none), vacuum, photo or both"""
    v = (v or "").strip().lower()
    return v if v in ("vacuum", "photo", "both") else ""

def _f(s, d=0.0):
    try:
        return float(s)
    except (TypeError, ValueError):
        return d


def _i(s, d=0):
    try:
        return int(s)                      # QString::toInt: "1.5" fails -> 0
    except (TypeError, ValueError):
        return d


def _yes(s):
    return str(s).strip().lower() in YES


@dataclass
class Stack:
    id: int
    type: int = 0                          # 0 tape, 1 tray
    nozzles: str = "1"
    x: float = 0.0
    y: float = 0.0
    angle: float = 90.0
    footprint: str = ""
    value: str = ""
    pick_height: float = 0.5
    pick_delay: int = 0
    place_height: float = 0.0
    place_delay: int = 0
    vacuum_detect: bool = False
    threshold: int = -40
    vision: int = 1                        # 0 none, 1 single, 2 four-up, 3 large
    speed: int = 100
    pitch: int = 4
    feed_strength: int = 60
    peel_strength: int = 80
    cols: int = 1
    rows: int = 1
    end_x: float = 0.0
    end_y: float = 0.0
    cur_col: int = 1
    cur_row: int = 1
    skip: bool = False
    size_check: bool = False
    thr: list = field(default_factory=lambda: [-40, -40, -40, -40])
    pocket_check: bool = False             # ours: the head camera finds the pocket centre before each pick
    part_l: float = 0.0                    # ours: part size for the size check (0 = footprint library)
    part_w: float = 0.0
    pad_align: bool = False                # ours: pad alignment with the head camera for all its parts
    second_look: bool = True               # ours: turned to the place angle, photographed again, corrected
                                           # (on unless the feeder says No)
    part_h: float = 0.0                    # ours: part height mm (4-up perspective correction; 0 = pkg.csv)
    bulk: int = 0                          # ours: the bulk feeder (container 1 .. 30) the parts come from
    place_check: str = ""                  # ours: after placing: "" none, vacuum, photo, both
    line_no: int = -1
    tokens: list = field(default_factory=list)

    def next_array_pos(self):
        """CStack::nextArrayPos: tray cell (advances the counter); tape: (X, Y)"""
        if self.type != 1:
            return self.x, self.y
        x = self.x + (self.cur_col - 1) * (self.end_x - self.x) / (self.cols - 1) if self.cols > 1 else self.x
        y = self.y + (self.cur_row - 1) * (self.end_y - self.y) / (self.rows - 1) if self.rows > 1 else self.y
        self.cur_col += 1
        if self.cur_col > self.cols:
            self.cur_col = 1
            if self.cur_row < self.rows:
                self.cur_row += 1
            else:                                   # the last cell was taken: the tray is used up
                self.cur_row = 1
                self.used_up = True
        return x, y


@dataclass
class Mark:
    x: float
    y: float
    dmin: float = 0.8
    dmax: float = 3.0
    led: int = 1
    exposure: int = 0


@dataclass
class Panel:
    x: float
    y: float
    angle: int = 0                         # 0 or 180
    skip: int = 0                          # 0 place, 1 skip, 2 test the bad mark
    line_no: int = -1


@dataclass
class Comp:
    stack: int
    nozzle: int
    name: str
    value: str
    footprint: str
    x: float
    y: float
    rot: float
    skip: bool
    pad_align: object = None               # ours (field 9): True / False, None = as its feeder says
    size_check: object = None              # ours (field 10): True / False, None = as its feeder says
    place_check: object = None             # ours (field 11): "none" / vacuum / photo / both, None = as its feeder says
    key: str = ""                          # the designator, "#2", "#3" ... added for its next copies (a panelized
                                           # file lists every board's parts under the same designators)


@dataclass
class Job:
    """Our own line `origin,x,y,` (NeoDen4 ignores lines it does not know): the board's zero point
    in the comp frame. With it, `mirror` lines give where that point is on the machine, so no
    dummy first part is needed. Without it: the first comp line, as NeoDen4."""
    path: str = ""
    lines: list = field(default_factory=list)
    stacks: dict = field(default_factory=dict)
    mode: str = "Trace"                    # pcb k1: Lock (fixed table) / Trace / TraceSep
    bad_mark: tuple = (10.0, 10.0)
    bad_mark_exposure: int = 200
    mark_whole: bool = True                # mark k0: Whole / Mirror
    mark_manual: bool = False              # mark k1: Manual / Auto
    marks: list = field(default_factory=list)
    test: bool = False                     # hidden dry run
    pcb_angle: float = 0.0                 # mirror_create k8
    panels: list = field(default_factory=list)
    comps: list = field(default_factory=list)
    encoding: str = "utf-8"
    origin: tuple = None                   # `origin,x,y,` line (ours): the board's zero point

    def stack(self, sid):
        return self.stacks.get(sid)

    def anchor(self):
        """the design point the panel (mirror) positions refer to: the `origin` line if there is
        one, else the first part (NeoDen4: comp 0, hence the dummy first part in old jobs)"""
        if self.origin is not None:
            return self.origin
        return (self.comps[0].x, self.comps[0].y) if self.comps else (0.0, 0.0)

    def save(self, path=None):
        """write back with the current tray counters and panel skips (CProcFile::save keeps the rest)"""
        for s in self.stacks.values():
            if s.type == 1 and s.line_no >= 0 and len(s.tokens) > 21:
                t = list(s.tokens)
                t[20], t[21] = str(s.cur_col), str(s.cur_row)
                self.lines[s.line_no] = "stack," + ",".join(t)
        for p in self.panels:
            if p.line_no >= 0:
                t = self.lines[p.line_no].split(",")
                if len(t) > 4:
                    t[4] = ("No", "Yes", "Auto")[p.skip]
                    self.lines[p.line_no] = ",".join(t)
        data = "\r\n".join(self.lines) + "\r\n"
        target = path or self.path
        tmp = target + ".tmp"                       # written whole, then put in place: never half a job file
        with open(tmp, "w", encoding=self.encoding, errors="replace", newline="") as f:
            f.write(data)
        os.replace(tmp, target)


def save_tray_counters(path, counters):
    """the trays' next cells {feeder id: (column, row)} into the job file as it is on disk now (edits
    made while the job ran are kept: the file is read again, only the counters change)"""
    job = load(path)
    changed = False
    for sid, (c, r) in counters.items():
        s = job.stack(int(sid))
        if s is not None and s.type == 1 and (s.cur_col, s.cur_row) != (c, r):
            s.cur_col, s.cur_row = int(c), int(r)
            changed = True
    if changed:
        job.save()
    return changed


def unquote_line(line):
    """a CSV line with quoted fields as the job format has it: no quotes, a comma inside a field becomes a semicolon"""
    import csv
    try:
        f = next(csv.reader([line]))
    except (csv.Error, StopIteration):
        return line
    return ",".join(v.replace(",", ";") for v in f)


def load(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    job = Job(path=path, encoding=enc)
    job.lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while job.lines and job.lines[-1] == "":
        job.lines.pop()
    for no, line in enumerate(job.lines):
        if '"' in line:                     # a quoted field with a comma in it ("TLP293(GB-TPL,E"): the
            line = job.lines[no] = unquote_line(line)    # format splits on every comma, so it is made a ";"
        t = line.split(",")
        tag, k = t[0].strip(), t[1:]
        if tag == "stack" and len(k) >= 14:
            sid = _i(k[0])
            if not 1 <= sid <= 98:
                continue
            s = Stack(sid, _i(k[1]), k[2], _f(k[3]), _f(k[4]), _f(k[5], 90.0), k[6], k[7], _f(k[8], 0.5),
                      int(_f(k[9])), _f(k[10]), int(_f(k[11])), _yes(k[12]), _i(k[13], -40),
                      _i(k[14], 1) if len(k) > 14 else 1, _i(k[15], 100) if len(k) > 15 else 100,
                      line_no=no, tokens=k)
            if s.type == 1:
                g = (k[16:22] + ["1"] * 6)[:6]
                s.cols, s.rows, s.end_x, s.end_y, s.cur_col, s.cur_row = max(_i(g[0], 1), 1), max(_i(g[1], 1), 1), _f(g[2]), _f(g[3]), max(_i(g[4], 1), 1), max(_i(g[5], 1), 1)
                tail = k[22:]
            else:
                if len(k) > 18:
                    s.pitch, s.feed_strength, s.peel_strength = _i(k[16], 4), _i(k[17], 60), _i(k[18], 80)
                tail = k[19:]
            s.skip = _yes(tail[0]) if len(tail) > 0 else False
            s.size_check = _yes(tail[1]) if len(tail) > 1 else False
            if len(tail) > 5:
                s.thr = [_i(v, -40) for v in tail[2:6]]
            if len(tail) > 6:
                s.pocket_check = _yes(tail[6])
            if len(tail) > 8:
                s.part_l, s.part_w = _f(tail[7]), _f(tail[8])
            if len(tail) > 9:
                s.pad_align = _yes(tail[9])
            if len(tail) > 10:
                s.second_look = tail[10].strip().lower() not in ("no", "0", "false")     # empty: on
            if len(tail) > 11:
                s.part_h = _f(tail[11])
            if len(tail) > 12:
                s.bulk = _i(tail[12], 0)
            if len(tail) > 13:
                s.place_check = _check(tail[13])
            job.stacks[sid] = s
        elif tag == "pcb" and len(k) >= 9:
            if k[1] in ("Lock", "Trace", "TraceSep"):
                job.mode = k[1]
                job.bad_mark = (_f(k[7], 10.0), _f(k[8], 10.0))
                job.bad_mark_exposure = _i(k[9], 200) if len(k) > 9 and k[9] != "" else 200
        elif tag == "mark" and len(k) >= 2:
            if k[0] in ("Whole", "Mirror") and k[1] in ("Manual", "Auto"):
                job.mark_whole, job.mark_manual = k[0] == "Whole", k[1] == "Manual"
                job.marks = []
                v = k[2:]
                for i in range(0, len(v) - 1, 2):
                    try:
                        job.marks.append(Mark(float(v[i]), float(v[i + 1])))
                    except ValueError:
                        break
        elif tag == "markext" and len(k) >= 5:
            i = _i(k[0], -1)
            if 0 <= i < len(job.marks):
                m = job.marks[i]
                m.dmin, m.dmax, m.led, m.exposure = _f(k[1], 0.8), _f(k[2], 3.0), _i(k[3], 1), _i(k[4], 0)
        elif tag == "test" and len(k) >= 1:
            job.test = k[0].strip() == "Yes"
        elif tag == "mirror_create" and len(k) >= 8:
            job.pcb_angle = _f(k[8]) if len(k) > 8 else 0.0
        elif tag == "mirror" and len(k) >= 4:
            job.panels.append(Panel(_f(k[0]), _f(k[1]), _i(k[2]), {"No": 0, "Yes": 1, "Auto": 2}.get(k[3].strip(), 0), no))
        elif tag == "origin" and len(k) >= 2:
            try:
                job.origin = (float(k[0]), float(k[1]))
            except ValueError:
                pass
        elif tag == "comp" and len(k) >= 9:
            own = lambda i: (lambda v: True if v in YES else False if v in ("no", "0", "false") else None)(k[i].strip().lower() if len(k) > i else "")
            pc = _check(k[11]) if len(k) > 11 and k[11].strip() else None
            job.comps.append(Comp(_i(k[0], 1), _i(k[1], 1), k[2], k[3], k[4], _f(k[5]), _f(k[6]), _f(k[7]), k[8].strip().lower() in SKIP_WORDS,
                                  own(9), own(10), "none" if pc == "" else pc))
    seen = {}
    for c in job.comps:
        seen[c.name] = seen.get(c.name, 0) + 1
        c.key = c.name if seen[c.name] == 1 else f"{c.name}#{seen[c.name]}"
    return job
