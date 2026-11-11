"""Read NeoDen4's Config.ini (Qt QSettings INI; floats as @Variant(\\0\\0\\0\\x87 + big-endian float32)).

    cfg = NeoConfig.load(r"D:\\NeoDen4\\Config.ini")
    cfg.sub_mouses        -> [(x, y) px] x4   nozzle centres in the 4-up up-camera image
    cfg.brightness        -> fBrightness  (up camera exposure = trunc(50 * value))
"""
import os
import re
import struct
from dataclasses import dataclass, field


def _unescape(s):
    """Qt INI string escapes -> bytes (\\0, \\xHH... greedy hex like Qt, \\n, \\t, ...)"""
    out = bytearray()
    i = 0
    simple = {"0": 0, "a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92, "'": 39, "?": 63}
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            if n == "x":
                j = i + 2
                while j < len(s) and s[j] in "0123456789abcdefABCDEF":
                    j += 1
                out.append(int(s[i + 2:j] or "0", 16) & 0xFF)
                i = j
                continue
            out.append(simple.get(n, ord(n) & 0xFF))
            i += 2
            continue
        out += c.encode("latin-1", "replace")
        i += 1
    return bytes(out)


def parse_value(v):
    v = v.strip()
    if v.startswith('"') and v.endswith('"'):
        v = v[1:-1]
    m = re.fullmatch(r"@Variant\((.*)\)", v, re.S)
    if m:
        b = _unescape(m.group(1))
        if len(b) >= 8 and b[:4] == b"\0\0\0\x87":            # QMetaType::Float
            return struct.unpack(">f", b[4:8])[0]
        if len(b) >= 12 and b[:4] == b"\0\0\0\x06":           # QMetaType::Double
            return struct.unpack(">d", b[4:12])[0]
        return b
    if v in ("true", "false"):
        return v == "true"
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


def _escape(b):
    """bytes -> Qt INI escapes (QSettingsPrivate::iniEscapedString for a QByteArray in @Variant):
    \\0 for zero, \\xHH for non-printable bytes, and a hex digit right after a \\xHH is escaped too"""
    out, after_hex = [], False
    for c in b:
        ch = chr(c)
        if c == 0:
            out.append("\\0"); after_hex = False
        elif c == 92:
            out.append("\\\\"); after_hex = False
        elif c == 34:
            out.append('\\"'); after_hex = False
        elif c in (7, 8, 9, 10, 11, 12, 13):
            out.append("\\" + "abtnvfr"[c - 7]); after_hex = False
        elif c < 32 or c >= 127 or (after_hex and ch in "0123456789abcdefABCDEF"):
            out.append(f"\\x{c:x}"); after_hex = True
        else:
            out.append(ch); after_hex = False
    return "".join(out)


def _quote(s):
    """Qt quotes a value that contains ';', ',' or '=' (or starts/ends with a space)"""
    return f'"{s}"' if any(c in s for c in ";,=") or s[:1] == " " or s[-1:] == " " else s


def format_value(v):
    """Python value -> INI text, the way NeoDen4 (Qt QSettings) writes it"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return _quote("@Variant(" + _escape(b"\0\0\0\x87" + struct.pack(">f", v)) + ")")
    if isinstance(v, bytes):
        return _quote("@Variant(" + _escape(v) + ")")
    return _quote(str(v))


def write_ini(path, updates):
    """change keys ("Group/key" -> value) in place, keeping every other line, comment and order;
    new keys are appended to their group (a new group goes at the end)"""
    try:
        lines = open(path, encoding="latin-1").read().split("\n")
    except FileNotFoundError:
        lines = []
    todo = dict(updates)
    sec, last = "General", {}
    for i, line in enumerate(lines):
        s = line.rstrip("\r")
        if s.startswith("[") and s.endswith("]"):
            sec = s[1:-1]
            last.setdefault(sec, i)
            continue
        if "=" in s:
            last[sec] = i
            key = f"{sec}/{s.split('=', 1)[0].strip()}"
            if key in todo:
                lines[i] = s.split("=", 1)[0] + "=" + format_value(todo.pop(key)) + ("\r" if line.endswith("\r") else "")
    for key, v in list(todo.items()):
        g, k = key.split("/", 1)
        if g in last:
            lines.insert(last[g] + 1, f"{k}={format_value(v)}")
            for gg in last:
                if last[gg] > last[g]:
                    last[gg] += 1
            last[g] += 1
        else:
            if lines and lines[-1].strip():
                lines.append("")
            lines += [f"[{g}]", f"{k}={format_value(v)}"]
            last[g] = len(lines) - 1
    with open(path, "w", encoding="latin-1", newline="") as f:
        f.write("\n".join(lines))


def read_ini(path):
    sec, data = "General", {}
    for line in open(path, encoding="latin-1"):
        line = line.rstrip("\r\n")
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            sec = line[1:-1]
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            data[f"{sec}/{k.strip()}"] = parse_value(v)
    return data


@dataclass
class NeoConfig:
    raw: dict = field(default_factory=dict)

    path: str = None

    _cache = {}                       # path -> (mtime, size, raw): parsed once per change of the file

    @classmethod
    def load(cls, path):
        """the file's settings; read again only when it changed (every API call loads it: ~20 ms of
        parsing on the machine's Atom). Each load gets its own copy of the values to change"""
        try:
            st = os.stat(path)
            key = (st.st_mtime_ns, st.st_size)
        except OSError:
            key = None
        c = cls._cache.get(path)
        if key is not None and c and c[0] == key:
            return cls(dict(c[1]), path)
        raw = read_ini(path)
        if key is not None:
            cls._cache[path] = (key, raw)
        return cls(dict(raw), path)

    def set(self, key, value):
        self.raw[key] = value
        self._dirty = getattr(self, "_dirty", {})
        self._dirty[key] = value

    def set_pt(self, key, xy):
        self.set(key + ".x", float(xy[0]))
        self.set(key + ".y", float(xy[1]))

    def save(self, path=None):
        """write the changed keys back (NeoDen4 can still read the file)"""
        path = path or self.path
        write_ini(path, getattr(self, "_dirty", {}))
        self._dirty = {}

    def get(self, key, default=None):
        return self.raw.get(key, default)

    def pt(self, key):
        return (float(self.raw.get(key + ".x", 0.0)), float(self.raw.get(key + ".y", 0.0)))

    @property
    def brightness(self):
        return float(self.raw.get("Default/fBrightness", 3.0))

    @property
    def brightness4(self):
        return float(self.raw.get("Default/fBrightness4", 1.0))

    @property
    def cam_angle(self):
        return float(self.raw.get("Default/fAngleCamLookUp", 0.0))

    @property
    def sub_mouses(self):
        return [self.pt(f"Position/dPosSubMouses{i}") for i in range(4)]

    @property
    def nozzle_positions(self):
        """head position that centres nozzle 1..4 over the up camera"""
        return [self.pt(f"Position/posCamLookUpMouse{i}") for i in range(4)]

    @property
    def four_up_position(self):
        return self.pt("Position/posCamLookUpMouseMouseCenter")

    @property
    def head_cam_offset(self):
        """head camera relative to nozzle 1"""
        return self.pt("Position/dPosCamLookDownMouse1")

    def nozzle_offset(self, n):
        """nozzle n (1..4) relative to nozzle 1, from the up-camera calibration"""
        p = self.nozzle_positions
        return (p[n - 1][0] - p[0][0], p[n - 1][1] - p[0][1])

    def up_exposure(self, four_up=False):
        return int(50.0 * (self.brightness4 if four_up else self.brightness))

    # ---- settings of the new software, group [Vision] (NeoDen4.exe ignores unknown groups) ----
    VISION_DEFAULTS = {
        "bSizeCheck": True,          # reject parts whose size does not match the package
        "fSizeMin": 0.8,             # measured / package size limits (NeoDen4: 0.8 .. 1.25 fixed)
        "fSizeMax": 1.25,
        "sSizeOrientation": "stack", # "stack": pair W/H by the stack angle; "auto": whichever fits
        "bVerifyAngle": True,        # closed loop: re-image after the angle correction
        "fVerifyAngleTol": 1.0,      # deg; about the angle resolution for a 2 mm part at 34.5 px/mm
        "fVerifyOffsetTol": 0.15,    # mm
        "nVerifyTries": 3,
        "fUpPxPerMm": 34.5,          # up camera scale (NeoDen4 constant 0x4C22AC)
        "bFourUp": False,            # parts on up to 4 nozzles read from ONE up-camera shot at
                                     # posCamLookUpMouseMouseCenter; the nozzle tips are found
                                     # again at every job start (turned 0/90/180/-90: their axes)
        "nTipExposure": 600,         # up exposure for finding EMPTY nozzle tips
        "fFourUpMaxMm": 8.0,         # bigger parts (or lead vision, modes 2/3) are shot one by one
        "fUpMagPerMm": 0.0087,       # up camera: the picture grows this much per mm nearer the lens
                                     # (measured 2026-11-03 with the tips lowered at the 4-up corners);
                                     # a part's underside is its height below the tip, so in the 4-up
                                     # shot it seems pushed outwards: corrected with pkg.csv H
        "fHeadPxPerMm": 112.0,       # head camera scale (NeoDen4 constant 0x4C2794)
    }

    def vision(self, key):
        return self.raw.get("Vision/" + key, self.VISION_DEFAULTS[key])

    def pxmm(self, cam):
        """camera scale in px/mm (0 up camera, 1 head camera); pass it as k / px_per_mm to neocam.vision"""
        return float(self.vision("fUpPxPerMm" if cam == 0 else "fHeadPxPerMm"))

    # ---- group [Job]: job runner settings of the new software ----
    JOB_DEFAULTS = {
        "bSound": True,              # master switch: no beeps at all when false (neomach mb.sound)
        "bBeepJobEnd": True,         # NeoDen4 frmFilePaist::runJob beeps at the end of the job
        "bBeepError": True,          # mark not found / pick failed / vision reject waits for the user
        "bSettle": True,             # grab until the image stops moving (neocalib.grab_settled)
        "fSettleTolPx": 0.3,         # allowed shift between two frames, px
        "fSettleMaxS": 1.0,          # give up after this, s (NeoDen4: always 0.25 s)
        "fMarkReadMm": 0.3,          # a mark this close to the picture's centre after the slow approach is
                                     # read off the picture (no more centring moves)
        "fMarkMinDiaMm": 1.0,        # a fiducial is a circle at least this big (smaller round things are not marks)
        "fMarkMaxErrMm": 0.3,        # the found marks' distances must match the design within this
        "fMarkMaxErrPct": 0.25,      # ... plus this % of the distance (the machine's X / Y scale is off ~0.2 %)
        "fMarkMaxOffMm": 5.0,        # a mark found further than this from where it should be is not taken
        "sMarkFit": "affine",        # fiducials: neoden (2 marks), affine (3+), bilinear (4+, warped board)
        "fAxisScaleX": 1.0,          # the machine's own axes, true = K x commanded, K = [[sx, 0],
        "fAxisScaleY": 1.0,          # [sx tan(skew), sy]]; used by the 2-mark fit, which cannot see
        "fAxisSkewDeg": 0.0,         # them (tools/xy_from_board.py measures them on a scanned board)
        "bPlanNozzles": True,        # Assign: the nozzles planned so the 4 nozzles pick together (each part an
                                     # allowed nozzle not yet used in its cycle), not one part per cycle
        "nCycleLookahead": 12,       # a cycle is filled from up to this many parts ahead when the next part's
                                     # nozzle is taken (0: strictly in order, as NeoDen4)
        "bCycleMixSpeeds": True,     # feeders of different speeds share a cycle, at the slowest of them
        "nRehomeTries": 2,           # steps lost in a job (after the controller's own correction): XY homed
                                     # again and back to work this many times before the operator is asked
        "nPlaceApproachSpeed": 40,   # the last mm before a place point (one-sided: the backlash); 0 = speed 10
        "bCycleRoute": True,         # the picks and the places of a cycle in nearest-neighbour order
        "bAutoZMap": True,           # a job without a board height map (<job>.zmap.json): measured at its start
        "bZMapDepth": True,          # with a map: each part's place depth from the board there + its height
                                     # (the feeder's place height only for a part of no known height)
        "fPlacePressMm": 0.1,        # ... pressed this much past the part touching the board
        "bSeeTurnSee": True,         # single-part vision: after turning to the place angle the part is looked at
                                     # again and turned until it reads right; nothing turns after the last
                                     # look, its offset is the one placed (the 4-up shot: Second look per stack)
        "nSeeTurnLooks": 5,          # looks at most (then the last one is used)
        "fSeeTurnTolDeg": 0.1,       # turn left that is good enough (the mainboard turns in 0.1 deg steps)
        "fTurnApproachDeg": 1.5,     # every turn ends from the same side (the rotation's backlash): this much
                                     # short of the angle first; 0 = straight there
        "bPickLearn": True,          # each feeder's pick point follows where its parts sit on the nozzle (up camera)
        "nPickLearnSamples": 5,      # ... corrected after this many parts (their mean offset)
        "fPickLearnMaxMm": 1.0,      # ... never more than this from the taught point; a bigger offset is not learnt
        "bStats": True,              # every pick / vision result / placement counted (<data>/stats, Statistics page)
        "fStatsWarnPct": 15.0,       # a feeder / nozzle with this share of failed picks gets a warning
        "bPlaceCheck": True,         # the after-place checks set on feeders / parts (off = none of them)
        "bPlaceCheckFirstOnly": False,   # the photo check on the first panel of a run only (first article)
        "fPlaceCheckMm": 0.15,       # photo check: a part further than this from its place point fails
        "fPlaceCheckDeg": 3.0,       # ... or turned more than this
        "bVisionLog": False,         # save every job image + its result in <neoden_dir>/VisionLog (calibration data)
        "nVisionLogMaxMB": 2000,     # the oldest days are deleted above this
    }

    def job(self, key):
        return self.raw.get("Job/" + key, self.JOB_DEFAULTS[key])

    # ---- group [PadAlign]: local alignment on the board's pads (neojob.padalign) ----
    PADALIGN_DEFAULTS = {
        "bEnabled": False,           # off until it is checked on a test board
        "sMethod": "gerber",         # gerber: each pad found by its own Gerber rectangle (template
                                     # matching, only this part's pads); blobs: any bright blob
        "fMinScore": 0.45,           # gerber method: match score a pad needs (0..1)
        "sFootprints": "",           # footprints that get it, comma separated (0402, 0603, QFP64 ...); "*" = every part
        "sRefs": "",                 # designators that get it
        "sHoleRefs": "",             # parts aligned on the board hole between their pads (reverse-mount
        "sHoleFootprints": "",       # LEDs: LD1..LD8 on the BOARD-A); the pads give only the turn
                                     # (also every part the pads file marks "hole": from the drill file)
        "bAutoHole": True,           # a 2-pad part with its pads >= fAutoHoleGapMm apart: a clear round
        "fAutoHoleGapMm": 1.2,       # hole between them found by the camera centres it (strict test)
        "nLed": 1,                   # head lamp: 1 inner (whole tinned pad bright; checked on the BOARD-A
                                     # board 2026-11-02), 2 outer (only pad edges bright), 3 both
        "nExposure": 0,              # 0 = automatic: solder mask at fPadMedian, pads stay bright
        "fPadMedian": 45,            # grey level the automatic exposure puts the board's median at
        "nThreshold": 0,             # 0 = Otsu; else a fixed grey level
        "bInvert": False,            # pads darker than the board (not usual)
        "fMinPadMm2": 0.05,          # smallest blob that counts as a pad
        "fMaxPadMm2": 25.0,
        "fMatchRadiusMm": 0.6,       # how far a pad may be from where it should be
        "fMinMatched": 0.6,          # share of the visible pads that must be found
        "fMaxRmsMm": 0.08,           # pads must fit the rotation + shift this well
        "fMaxScaleErr": 0.07,        # found pad spacing vs. the Gerber (the check for 2-pad parts) ...
        "fMaxSpacingErrMm": 0.1,     # ... refused only if it is also this far off per pad (an 0603's
                                     # tinned pads 0.08 mm wider apart are 10 %, but its own pads)
        "fMaxShiftMm": 0.5,          # a bigger correction is asked first
        "fMaxTurnDeg": 2.0,
        "fHeadCamAngle": 0.0,        # head camera turned against the machine axes (deg)
    }

    def padalign(self, key):
        return self.raw.get("PadAlign/" + key, self.PADALIGN_DEFAULTS[key])

    # ---- group [LeadVision]: up-camera vision on the leads, matched to the Gerber pads ----
    LEADVISION_DEFAULTS = {
        "bEnabled": False,           # off until it is checked on parts held by the nozzle
        "sFootprints": "",           # footprints measured on their leads ("*" = every part with pads)
        "sRefs": "",
        "nExposure": 0,              # 0 = the job's up-camera exposure (fBrightness)
        "nThreshold": 0,             # 0 = Otsu on (image - empty nozzle)
        "fMinLeadMm2": 0.01,
        "fMaxLeadMm2": 4.0,
        "fMatchRadiusMm": 0.4,
        "fMinMatched": 0.7,          # share of the leads that must be found
        "fMaxRmsMm": 0.05,
        "bBodyFallback": False,      # leads not found: use the body outline (NeoDen) instead of asking
    }

    def leadvision(self, key):
        return self.raw.get("LeadVision/" + key, self.LEADVISION_DEFAULTS[key])

    def size_limits(self):
        return float(self.vision("fSizeMin")), float(self.vision("fSizeMax"))
