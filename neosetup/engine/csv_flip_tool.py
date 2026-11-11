import csv
import json
import math
import os
import re
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from collections import Counter

try:
    # Rasterises the Gerber underlay. Tk repaints a canvas item by item, and
    # with ten thousand Gerber shapes that is about a second a frame; one
    # image is a single blit.
    from PIL import Image, ImageDraw, ImageTk
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


# =====================================
# Global Data
# =====================================

rows = []          # Component rows

columns = [
    "Designator",
    "Footprint",
    "Center-X(mm)",
    "Center-Y(mm)",
    "Ref-X(mm)",
    "Ref-Y(mm)",
    "Pad-X(mm)",
    "Pad-Y(mm)",
    "Layer",
    "Rotation",
    "Comment"
]

neoden_headers = [
    "Designator", "Footprint", "Mid X", "Mid Y",
    "Ref X", "Ref Y", "Pad X", "Pad Y",
    "Layer", "Rotation", "Comment"
]

# =====================================
# CSV Handling
# =====================================
def normalize_to_bottom_left():
    XY_COLUMNS = [(2,3), (4,5), (6,7)]
    min_x = None
    min_y = None

    for r in rows:
        for xi, yi in XY_COLUMNS:
            try:
                x = float(r[xi])
                y = float(r[yi])
            except:
                continue

            min_x = x if min_x is None else min(min_x, x)
            min_y = y if min_y is None else min(min_y, y)

    if min_x is None or min_y is None:
        return

    for r in rows:
        for xi, yi in XY_COLUMNS:
            try:
                r[xi] = round(float(r[xi]) - min_x, 3)
                r[yi] = round(float(r[yi]) - min_y, 3)
            except:
                continue

def clean_zero(v, eps=1e-6):
    return 0.0 if abs(v) < eps else v


def tidy_angle(a):
    """
    A component angle in 0..360, to 0.01 degree -- whole numbers stay whole.
    Angles used to be rounded to whole degrees, which threw away the small
    turn an alignment finds (a board sitting 0.4 degrees off left every part
    0.4 degrees off).
    """
    a = round(float(a) % 360.0, 2)
    if a >= 360.0:
        a = 0.0
    return int(a) if a == int(a) else a

def rotate_board_polar(theta_deg, pivot=None):
    theta_rad = math.radians(theta_deg)
    cx, cy = pivot if pivot else (0.0, 0.0)
    if theta_deg:
        xform_then_rotation(theta_deg, cx, cy)
    XY_COLUMNS = [(2,3), (4,5), (6,7)]

    for r in rows:
        for xi, yi in XY_COLUMNS:
            try:
                x = float(r[xi])
                y = float(r[yi])
            except (ValueError, TypeError):
                continue

            dx = x - cx
            dy = y - cy
            r_len = math.hypot(dx, dy)
            t = math.atan2(dy, dx)
            t += theta_rad
            new_x = cx + r_len * math.cos(t)
            new_y = cy + r_len * math.sin(t)

            r[xi] = round(clean_zero(new_x), 3)
            r[yi] = round(clean_zero(new_y), 3)

        try:
            comp_rot = float(r[9])
        except (ValueError, TypeError):
            comp_rot = 0.0

        r[9] = tidy_angle(comp_rot + theta_deg)

    refresh_table()

def validate_board_coordinates():
    if not board_width.get().strip() or not board_height.get().strip():
        return True

    try:
        bw = float(board_width.get())
        bh = float(board_height.get())
    except:
        messagebox.showerror("Error", "Invalid Board Width or Height")
        return False

    errors = []

    for idx, r in enumerate(rows, start=1):
        try:
            x = float(r[2])
            y = float(r[3])
        except:
            continue

        if x < 0 or x > bw or y < 0 or y > bh:
            errors.append(
                f"Row {idx} | {r[0]} | X={x} Y={y}"
            )

    if errors:
        messagebox.showerror(
            "Board Coordinate Error",
            "Coordinates out of board range:\n\n"
            + "\n".join(errors[:15])
            + ("\n\n...more rows omitted" if len(errors) > 15 else "")
        )
        return False

    return True

def safe_float(val):
    try:
        return float(str(val).replace("mm", "").strip())
    except:
        return 0.0

EXPECTED_COLUMNS = [
    "Designator",
    "Footprint",
    "Center-X(mm)",
    "Center-Y(mm)",
    "Ref-X(mm)",
    "Ref-Y(mm)",
    "Pad-X(mm)",
    "Pad-Y(mm)",
    "Layer",
    "Rotation",
    "Comment"
]

def is_fiducial(r):
    """A fiducial is marked by its designator, not by its value."""
    return str(r[0]).strip().upper().startswith("FID")


def placeable_rows():
    return [r for r in rows if not is_fiducial(r)]


def _fmt_num(x):
    text = ("%.3f" % x).rstrip("0").rstrip(".")
    return text


def _ohms_text(ohms):
    if ohms >= 1e6:
        return _fmt_num(ohms / 1e6) + "M"
    if ohms >= 1e3:
        return _fmt_num(ohms / 1e3) + "K"
    return _fmt_num(ohms) + "R"


def _farads_text(pf):
    if pf >= 1e6:
        return _fmt_num(pf / 1e6) + "uF"
    if pf >= 1e3:
        return _fmt_num(pf / 1e3) + "nF"
    return _fmt_num(pf) + "pF"


def _eia_code(code, unit_exp=0):
    """'104' -> 10e4, '1002' -> 100e2, '10R0' -> 10.0, '4R7' -> 4.7."""
    code = code.upper()
    if "R" in code:
        return float(code.replace("R", "."))
    return float(code[:-1]) * 10 ** int(code[-1])


def decode_part_value(name):
    """
    What a part is, as a value: '10K', '220R', '100nF', '1uF' -- from a plain
    value or from the common manufacturer part numbers (Panasonic ERJ and
    Yageo RC resistors; Murata GRM / GRT / GCM and Samsung CL capacitors).
    None if it cannot tell.
    """
    v = upper_part_name(name).replace(" ", "")
    m = re.match(r"^ERJ-?[0-9A-Z]*?[FJDG]([0-9R]{3,4})V?$", v)
    if m:
        return _ohms_text(_eia_code(m.group(1)))
    m = re.match(r"^RC\d{4}[A-Z]{2}-\d{2}([0-9.]+[RKM]?[0-9]*)L$", v)
    if m:
        v = m.group(1)
    m = re.match(r"^G[RCJ][MT]\d{3}[0-9A-Z]{2}[0-9A-Z]{2}([0-9]{2}[0-9R]|[0-9]R[0-9])[A-Z]", v)
    if m:
        return _farads_text(_eia_code(m.group(1)))
    m = re.match(r"^CL\d{2}[A-Z]([0-9]{2}[0-9R]|[0-9]R[0-9])[A-Z]", v)
    if m:
        return _farads_text(_eia_code(m.group(1)))
    m = re.match(r"^(\d+(?:\.\d+)?)([PNU])F?(?:/.*|\d+V)?$", v)
    if m:
        mult = {"P": 1, "N": 1e3, "U": 1e6}[m.group(2)]
        return _farads_text(float(m.group(1)) * mult)
    m = re.match(r"^(\d+)([RKM])(\d+)$", v)                   # 4K7, 1R0
    if m:
        mult = {"R": 1, "K": 1e3, "M": 1e6}[m.group(2)]
        return _ohms_text(float("%s.%s" % (m.group(1), m.group(3))) * mult)
    m = re.match(r"^(\d+(?:\.\d+)?)([RKM]?)(?:OHM)?$", v)
    if m:
        mult = {"": 1, "R": 1, "K": 1e3, "M": 1e6}[m.group(2)]
        return _ohms_text(float(m.group(1)) * mult)
    return None


def upper_part_name(value):
    """
    Upper-cases a part name for the BOM. The micro sign is written U first:
    "µ".upper() is the Greek capital "Μ", which turned 10µF into "10ΜF" --
    not readable as microfarads by anything after it.
    """
    text = str(value).strip().replace("\u00b5", "u").replace("\u03bc", "u")
    # a name that was upper-cased before this was fixed: digit + Greek capital mu
    text = re.sub("(?<=[0-9.])\u039c(?=[FH]|$)", "U", text)
    return text.upper()


def part_name_key(value):
    """
    Collation key for a part name: case and surrounding/among-word spacing do
    not make a different part. "10K", "10k" and " 10 K " are one BOM line --
    and, just as importantly, one feeder on the machine.

    A trailing farad unit is optional in the same way: "100n", "100nF" and
    "100nf" are the same capacitor. Only the unit letter is dropped, so "100n"
    never collides with "100".
    """
    key = "".join(str(value).split()).casefold()
    return re.sub(r"^([\d.]+)([numpuµμ])f$", r"\1\2", key)


def similar_part_groups():
    """
    Part names that collate to the same key, i.e. the same part spelled more
    than one way. Also catches the letter O typed for a zero, which case
    folding alone cannot see ("1OK" vs "10K").
    """
    by_key = {}
    for r in placeable_rows():
        by_key.setdefault(part_name_key(r[10]), Counter())[str(r[10])] += 1

    # second pass: fold a lone letter O onto zero so "1OK" meets "10K"
    merged = {}
    for key, spellings in by_key.items():
        folded = key.replace("o", "0")
        if folded != key and folded in by_key:
            merged.setdefault(folded, Counter()).update(spellings)
        else:
            merged.setdefault(key, Counter()).update(spellings)

    return {k: c for k, c in merged.items() if len(c) > 1}


def update_total_count():
    total_label.config(text="Total Components: %d" % len(placeable_rows()))

def update_totalfootprint_count():
    count = len({str(r[1]).strip() for r in placeable_rows()})
    footprints_label.config(text=f"Different Footprints: {count}")

def update_totalBOM_count():
    count = len({str(r[10]).strip() for r in placeable_rows()})
    groups = similar_part_groups()
    if groups:
        # Each distinct spelling becomes its own feeder when the machine
        # program is built, so say so rather than quietly merging them.
        extra = sum(len(c) for c in groups.values()) - len(groups)
        boms_label.config(
            text="Different BOMs: %d   (%d spelled more than one way)" % (count, extra),
            fg="#ff9900")
    else:
        boms_label.config(text=f"Different BOMs: {count}", fg="#ff6666")

def normalize(s):
    return s.strip().lower().replace(" ", "").replace("-", "")

# Tkinter passes Treeview values through Tcl, which re-types anything that looks
# numeric: a part number "100" comes back as int 100, and a footprint "0603"
# comes back as int 603 with the leading zero gone for good. Values read out of
# a Treeview can therefore never be trusted as identity. Every table that needs
# to map a selection back to real data stores the exact key in the item id.
TV_KEY_PREFIX = "k:"

def tv_text(value):
    """Best-effort display text for a value read back from a Treeview."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)

def tv_key(name):
    """Item id that carries an exact string key (Tkinter reserves '' for the root)."""
    return TV_KEY_PREFIX + str(name)

def tv_selected_key(treeview):
    """Exact key of the selected item, or None when nothing is selected."""
    sel = treeview.selection()
    if not sel:
        return None
    item = sel[0]
    if not item.startswith(TV_KEY_PREFIX):
        return None
    return item[len(TV_KEY_PREFIX):]

def ApplyOffset():
    try:
        target_ox = float(board_offsetX.get() or 0.0)
        target_oy = float(board_offsety.get() or 0.0)

        current_fid_x = None
        current_fid_y = None

        for r in rows:
            if str(r[0]).upper().startswith("FID"):
                current_fid_x = float(r[2])
                current_fid_y = float(r[3])
                break

        if current_fid_x is None:
            messagebox.showerror("Error", "No 'FID' component found to use as origin reference.")
            return

        dx = target_ox - current_fid_x
        dy = target_oy - current_fid_y
        xform_then(1.0, 0.0, 0.0, 1.0, dx, dy)

        XY_COLUMNS = [(2,3), (4,5), (6,7)]
        for r in rows:
            for xi, yi in XY_COLUMNS:
                try:
                    r[xi] = round(float(r[xi]) + dx, 3)
                    r[yi] = round(float(r[yi]) + dy, 3)
                except (ValueError, TypeError):
                    continue

        refresh_table()
        messagebox.showinfo("Success", f"Coordinates recalculated.\nShift applied: X:{dx:.3f}, Y:{dy:.3f}")

    except ValueError:
        messagebox.showerror("Error", "Please enter valid numeric values for Offset.")

def load_csv():
    global rows
    path = filedialog.askopenfilename(filetypes=[("CSV Files", "*.csv")])
    if not path:
        return

    # Parse into a local list first. Every validation below can bail out, and
    # clearing `rows` up front would throw away the loaded board just because
    # the wrong file was picked -- leaving the table showing rows that no longer
    # exist in memory.
    parsed = []

    try:
        with open(path, newline='', encoding="ISO-8859-1") as f:
            data = list(csv.reader(f))
    except OSError as e:
        messagebox.showerror("Error", "Could not read the file:\n\n%s" % e)
        return

    header_row_index = None
    header_map = {}

    for i, row in enumerate(data):
        norm = [normalize(c) for c in row]
        if "designator" in norm and ("centerx(mm)" in norm or "midx" in norm):
            header_row_index = i
            for idx, name in enumerate(row):
                key = normalize(name)
                header_map[key] = idx
            break

    if header_row_index is None:
        messagebox.showerror("Error", "Valid PnP or Neoden header not found.")
        return

    column_synonyms = {
        "Center-X(mm)": ["centerx(mm)", "midx"],
        "Center-Y(mm)": ["centery(mm)", "midy"],
        "Ref-X(mm)": ["refx(mm)", "refx"],
        "Ref-Y(mm)": ["refy(mm)", "refy"],
        "Pad-X(mm)": ["padx(mm)", "padx"],
        "Pad-Y(mm)": ["pady(mm)", "pady"]
    }

    col_index = {}
    for col in EXPECTED_COLUMNS:
        norm_col = normalize(col)

        if norm_col in header_map:
            col_index[col] = header_map[norm_col]
        elif col in column_synonyms:
            found = False
            for syn in column_synonyms[col]:
                if syn in header_map:
                    col_index[col] = header_map[syn]
                    found = True
                    break
            if not found:
                messagebox.showerror("Error", f"Missing column: {col}")
                return
        else:
            messagebox.showerror("Error", f"Missing column: {col}")
            return

    # Height(mm) is optional -- plenty of exports leave it out entirely.
    height_col = None
    for candidate in ("height(mm)", "height"):
        if candidate in header_map:
            height_col = header_map[candidate]
            break
    parsed_heights = {}
    parsed_counts = {}
    renamed_footprints = Counter()

    for r in data[header_row_index + 1:]:
        if not r or not r[col_index["Designator"]].strip():
            continue
        raw_fp = str(r[col_index["Footprint"]]).strip()
        if clean_footprint(raw_fp) != raw_fp:
            renamed_footprints[(raw_fp, clean_footprint(raw_fp))] += 1

        if height_col is not None and height_col < len(r):
            try:
                mm = float(str(r[height_col]).replace("mm", "").strip())
            except ValueError:
                mm = 0.0
            # Keyed per part on its footprint: one part is one reel, so one
            # physical height. Keyed by footprint alone, the clean-up that
            # folds "L0603" and "SMD-0603C" into "0603" would let a ferrite
            # bead's 0.6mm speak for every 0603 capacitor on the board.
            key = (footprint_key(r[col_index["Footprint"]]),
                   part_name_key(r[col_index["Comment"]]))
            seen, with_height = parsed_counts.get(key, (0, 0))
            parsed_counts[key] = (seen + 1, with_height + (1 if mm > 0 else 0))
            if mm > 0:
                # A library that disagrees with itself inside one part is wrong
                # somewhere; the tallest value is the one that cannot hurt.
                parsed_heights[key] = max(parsed_heights.get(key, 0.0), mm)

        try:
            new_row = [
                r[col_index["Designator"]],
                clean_footprint(r[col_index["Footprint"]]),
                safe_float(r[col_index["Center-X(mm)"]]),
                safe_float(r[col_index["Center-Y(mm)"]]),
                safe_float(r[col_index["Ref-X(mm)"]]),
                safe_float(r[col_index["Ref-Y(mm)"]]),
                safe_float(r[col_index["Pad-X(mm)"]]),
                safe_float(r[col_index["Pad-Y(mm)"]]),
                r[col_index["Layer"]],
                r[col_index["Rotation"]],
                # Part names are upper-cased on the way in so "10K" and "10k"
                # cannot become two BOM lines -- and two feeders -- for one
                # part. Footprints are deliberately left alone: upper-casing
                # them merges nothing, and machine files carry footprints with
                # lower-case in them ("WL-SMCW_0603_150060xx8x000"), which the
                # feeder matching would then miss. That matching is
                # case-insensitive instead.
                upper_part_name(r[col_index["Comment"]]),
            ]
            parsed.append(new_row)
        except IndexError:
            continue

    if not parsed:
        messagebox.showerror("Error", "No component rows were found in this file.")
        return

    rows.clear()
    rows.extend(parsed)
    pnp_source["path"] = path

    file_heights.clear()
    file_heights.update(parsed_heights)
    xform_reset()
    angle_flags.clear()
    angle_changed, angle_flagged = normalise_rotations()
    file_height_counts.clear()
    file_height_counts.update(parsed_counts)
    learn_footprint_heights()

    refresh_table()
    refresh_variants()

    summary = []
    if renamed_footprints:
        summary.append(
            "Footprint names cleaned -- a chip size with extra text around it now "
            "reads as the size alone:\n" +
            "\n".join("   %s  ->  %s   (%d)" % (old, new, n)
                      for (old, new), n in sorted(renamed_footprints.items())) +
            "\nFootprints that say more than a size (LEDs, tantalums, ICs) were "
            "left as they are.")
    summary.append(angle_summary(angle_changed, angle_flagged))
    messagebox.showinfo("Import Summary", "\n\n".join(summary))

    suspects = suspect_part_keys()
    flagged = sorted({str(r[10]) for r in placeable_rows()
                      if part_name_key(r[10]) in suspects
                      and part_name_key(r[10]) not in part_heights})

    # parts whose components disagree in the library: some with a height,
    # some without -- one reel cannot be two heights
    name_of = {part_name_key(r[10]): str(r[10]) for r in placeable_rows()}
    mixed = []
    for (fk, pk), (n, with_h) in sorted(parsed_counts.items()):
        if 0 < with_h < n and pk not in part_heights:
            mixed.append("%s on %s: %d of %d carry %.2fmm, the rest none"
                         % (name_of.get(pk, pk), fk, with_h, n, parsed_heights[(fk, pk)]))

    if flagged or mixed:
        text = []
        if flagged:
            text.append("Designated as ICs but drawn on a two-terminal chip footprint, "
                        "so that footprint's height is not trusted -- placed last until "
                        "given a height:\n   " + "\n   ".join(flagged))
        if mixed:
            text.append("Library heights that disagree within one part. The tallest is "
                        "used, which is safe but may place it later than it needs:\n   " +
                        "\n   ".join(mixed))
        text.append("Double-click a part in the Part Name table to set its height.")
        messagebox.showwarning("Check These Heights", "\n\n".join(text))

    # Upper-casing settles case differences. Anything still spelled two ways is
    # a typo or an optional unit -- worth showing, because each spelling would
    # otherwise claim its own feeder.
    if similar_part_groups():
        open_similar_names(opened_automatically=True)

# Define package hierarchy based on physical height (index 0 is shortest)
# Unknown packages will be assigned a high index to be placed last.
# =====================================
# Component body height
# =====================================
#
# Assembly runs shortest part first: once a tall part is on the board, the head
# can strike it while placing anything near it. So the order is by body height,
# ascending.
#
# The Altium export has a Height(mm) column and it is authoritative where the
# library actually fills it in -- but on real boards most of it is zero (61 of
# 80 components on one of this project's boards, 196 of 213 on the other). What
# is left is estimated from the footprint name.
#
# Estimates lean tall. Over-estimating only moves a part later in the order,
# which cannot cause a collision; under-estimating can.

# mm = CAPTURED -> the height is the pattern's first capture group
# mm = None     -> the family is recognised but its height cannot be inferred
CAPTURED = "captured"

PACKAGE_HEIGHT_RULES = [
    # (pattern, mm, what it matched) -- first match wins, so specific first.
    (r"[_\-]?H(\d+(?:\.\d+)?)\b",  CAPTURED, "height encoded in the name"),

    (r"(?<![0-9])0201(?![0-9])",     0.35, "0201 chip"),
    (r"(?<![0-9])0402(?![0-9])",     0.60, "0402 chip"),
    (r"(?<![0-9])0603(?![0-9])",     0.95, "0603 chip"),
    (r"(?<![0-9])0805(?![0-9])",     1.30, "0805 chip"),
    (r"(?<![0-9])1206(?![0-9])",     1.80, "1206 chip"),
    (r"(?<![0-9])1210(?![0-9])",     2.00, "1210 chip"),
    (r"(?<![0-9])1812(?![0-9])",     2.50, "1812 chip"),
    (r"(?<![0-9])(?:2010|2512)(?![0-9])", 0.80, "wide chip resistor"),

    (r"(?<![0-9])3216(?![0-9])|CASE[_\- ]?A\b", 1.80, "tantalum case A"),
    (r"(?<![0-9])3528(?![0-9])|CASE[_\- ]?B\b", 2.10, "tantalum case B"),
    (r"(?<![0-9])6032(?![0-9])|CASE[_\- ]?C\b", 2.80, "tantalum case C"),
    (r"(?<![0-9])7343(?![0-9])|CASE[_\- ]?D\b", 3.10, "tantalum case D"),

    (r"SOD[_\-]?523",                0.80, "SOD-523"),
    (r"SOD[_\-]?323",                1.00, "SOD-323"),
    (r"SOD[_\-]?123",                1.20, "SOD-123"),
    (r"\bSMA\b",                     2.40, "SMA diode"),
    (r"\bSMB\b",                     2.40, "SMB diode"),
    (r"\bSMC\b",                     2.70, "SMC diode"),

    (r"SOT[_\-]?(?:323|363)",        1.10, "SOT-323/363"),
    (r"SOT[_\-]?223",                1.80, "SOT-223"),
    (r"SOT[_\-]?89",                 1.60, "SOT-89"),
    (r"SOT",                         1.20, "SOT-23 family"),

    (r"\bDFN\b|[_\-]DFN",            0.90, "DFN"),
    (r"\bQFN\b|[_\-]QFN",            1.00, "QFN"),
    (r"TQFP",                        1.20, "TQFP"),
    (r"LQFP",                        1.60, "LQFP"),
    (r"\bQFP\b",                     2.00, "QFP"),
    (r"TSSOP",                       1.20, "TSSOP"),
    (r"MSOP",                        1.10, "MSOP"),
    (r"SSOP",                        2.00, "SSOP"),
    (r"SOIC|\bSO[_\-]?\d+\b|\bSO\b", 1.80, "SOIC / SO"),
    (r"MELF",                        2.20, "MELF"),

    # Through-hole. No reliable body height from the name, but naming them
    # keeps "not recognised" meaning what it says.
    (r"\bTO[_\-]?\d",                None, "through-hole TO package"),
    (r"\bAXIAL|\bRADIAL|\bDIP\b",    None, "through-hole"),
]

# footprint key -> {"mm": float|None, "source": str, "note": str, "name": str}
footprint_heights = {}

# Height(mm) from the last loaded Pick & Place file:
#   (footprint key, part key) -> tallest non-zero height among its components
file_heights = {}
#   (footprint key, part key) -> (components, components with a height)
file_height_counts = {}


# A footprint name that is a chip size with some decoration around it -- a
# library prefix, a reference-designator letter, a R/C/L suffix -- is the chip
# size and nothing else: "J1-0603", "SMD-0603C", "SMD0805", "0805R" are all
# just 0603 or 0805. Anything with more to say is left alone, deliberately:
# "WL-SMCW_0603_150060xx8x000" is an LED and "CAP_POL_1206_3216" a tantalum,
# and folding them into a bare chip size would lose that.
CHIP_SIZES = ("0201", "0402", "0603", "0805", "1206", "1210", "1812", "2010", "2512")
_CHIP_FOOTPRINT = re.compile(
    r"^(?:SMD|SMT|CHIP|[RCLJ]\d*)?[-_ ]?(" + "|".join(CHIP_SIZES) + r")"
    r"[-_ ]?(?:R|C|L|N|RES|CAP|IND)?$",
    re.IGNORECASE)


def clean_footprint(name):
    text = str(name).strip()
    m = _CHIP_FOOTPRINT.match(text)
    return m.group(1) if m else text


def footprint_key(value):
    return "".join(clean_footprint(value).split()).casefold()


# --------------------------------------------------------------------------
# Height per part
# --------------------------------------------------------------------------
#
# Height normally comes with the footprint, but a footprint can simply be
# wrong: on BOARD-A an optocoupler (PC817) and a darlington array (ULN2803)
# were drawn on an 0402. Taken at its word that puts a 4mm IC first in the
# order -- exactly the direction that gets parts struck.
#
# So a part can carry its own height, which beats its footprint's. And a part
# that is designated as an IC (U.., IC..) but sits on a two-terminal chip
# footprint is not believed: until it is given a height it counts as unknown,
# and unknown goes last.

# part name key -> mm, typed in by the user
part_heights = {}

_IC_DESIGNATOR = re.compile(r"^(?:U|IC)\d", re.IGNORECASE)


def suspect_part_keys():
    """Parts where at least one component is an IC sitting on a chip footprint."""
    found = set()
    for r in rows:
        if (_IC_DESIGNATOR.match(str(r[0]).strip())
                and clean_footprint(r[1]) in CHIP_SIZES):
            found.add(part_name_key(r[10]))
    for c in machine_model["comps"]:
        if (_IC_DESIGNATOR.match(str(field_at(c, 3)).strip())
                and clean_footprint(field_at(c, 5)) in CHIP_SIZES):
            found.add(part_name_key(field_at(c, 4)))
    return found


# --------------------------------------------------------------------------
# Built-in heights of common packages
# --------------------------------------------------------------------------
# Typical maximum body height (mm) of the everyday packages, from the usual
# manufacturer datasheets (Yageo / Samsung / Murata / TDK, JEDEC). A chip size
# is not one height: a 0603 resistor is ~0.5mm, an 0603 capacitor up to 0.9.
# So passives are looked up by what the part is, read from its value -- 100N,
# 4.7NF, 18PF, 10U are capacitors, 100UH an inductor, GREEN an LED, the rest
# resistors. These beat the file's Height(mm) column, which libraries often
# fill in wrongly (BOARD-A had 2.65 on 0603 resistors); a height typed in by
# hand beats everything.
PASSIVE_HEIGHTS = {
    #        resistor  capacitor  inductor   LED
    "0201": {"R": 0.26, "C": 0.33, "L": 0.33, "LED": 0.40},
    "0402": {"R": 0.35, "C": 0.55, "L": 0.55, "LED": 0.55},
    "0603": {"R": 0.50, "C": 0.90, "L": 0.95, "LED": 0.80},
    "0805": {"R": 0.55, "C": 1.30, "L": 1.30, "LED": 0.85},
    "1206": {"R": 0.60, "C": 1.80, "L": 1.60, "LED": 1.10},
    "1210": {"R": 0.60, "C": 2.50, "L": 2.00, "LED": 1.10},
    "1812": {"R": 0.65, "C": 2.50, "L": 2.20, "LED": 1.10},
    "2010": {"R": 0.65, "C": 2.50, "L": 2.20, "LED": 1.10},
    "2512": {"R": 0.65, "C": 2.50, "L": 2.20, "LED": 1.10},
}
PACKAGE_HEIGHTS = [
    # (footprint pattern, mm, note) -- first match wins
    (r"SOT[_\-]?23[_\-]?[56]\b", 1.10, "SOT-23-5/6"),
    (r"SOT[_\-]?23|SOT23", 1.10, "SOT-23"),
    (r"SOT[_\-]?323|SC[_\-]?70", 1.00, "SOT-323 / SC-70"),
    (r"SOT[_\-]?89", 1.60, "SOT-89"),
    (r"SOT[_\-]?223", 1.80, "SOT-223"),
    (r"SOD[_\-]?123", 1.15, "SOD-123"),
    (r"SOD[_\-]?323", 0.95, "SOD-323"),
    (r"SOD[_\-]?523", 0.70, "SOD-523"),
    (r"\bSM[AB]\b|DO[_\-]?214A[AC]", 2.30, "SMA / SMB diode"),
    (r"SMD[_\- ]?4\b|GULL", 3.65, "SMD-4 gull wing (PC817 type)"),
    (r"\bSO[_\-]?(?:18|20|24|28)\b|SOIC[_\-]?(?:18|20|24|28)", 2.65, "wide SOIC"),
    (r"\bSO[_\-]?(?:8|14|16)\b|SOIC[_\-]?(?:8|14|16)", 1.75, "SOIC"),
]
_LED_WORDS = re.compile(r"LED|GREEN|RED|BLUE|YELLOW|WHITE|ORANGE|AMBER", re.IGNORECASE)


def passive_kind(part_name):
    """R, C, L or LED, from a part's value: 100N / 4.7NF / 10U -> C, 100UH -> L."""
    v = upper_part_name(part_name)
    if _LED_WORDS.search(v):
        return "LED"
    # manufacturer part numbers: Murata / Samsung / Yageo / TDK / Taiyo capacitors,
    # Murata / TDK inductors and beads
    if re.match(r"(?:GRM|GCM|GJM|GRT|CL\d\d|CC\d{4}|C\d{4}[A-Z]|CGA|[EJTL]MK\d)", v):
        return "C"
    if re.match(r"(?:LQ[GHMW]|MLZ|MLF|BLM|VLS|NR\d|FB)", v):
        return "L"
    if re.search(r"\d\s*[PNUM]?H\b|\d\s*[UN]H", v):
        return "L"
    if re.search(r"\d\s*(?:[PNU]F?|F)\b|\d\s*[PNU]F|^\d+(?:\.\d+)?[PNU]\d*$", v):
        return "C"
    return "R"


def database_height(footprint, part_name):
    """(mm, note) from the built-in table, or None if it is not a package it knows."""
    size = clean_footprint(footprint)
    if size in PASSIVE_HEIGHTS:
        kind = passive_kind(part_name)
        names = {"R": "resistor", "C": "capacitor", "L": "inductor", "LED": "LED"}
        return PASSIVE_HEIGHTS[size][kind], "%s %s (built-in)" % (size, names[kind])
    name = str(footprint).upper()
    for pattern, mm, note in PACKAGE_HEIGHTS:
        if re.search(pattern, name):
            return mm, "%s (built-in)" % note
    return None


def component_height(footprint, part_name, suspects=None):
    """
    Height used to order assembly and to work out the place height, in this
    order: typed in for the part; unknown if the footprint cannot be trusted
    for it; typed in for the footprint; the built-in table; the file's
    Height(mm) column; a guess from the footprint name.
    Pass `suspects` when calling in a loop -- it is the same for every row.
    """
    key = part_name_key(part_name)
    if key in part_heights:                       # typed in for this part
        return part_heights[key]
    if suspects is None:
        suspects = suspect_part_keys()
    if key in suspects:                           # footprint not believed
        return float("inf")
    fk = footprint_key(footprint)
    entry = footprint_heights.get(fk)
    if entry and entry["source"] == "manual":     # typed in for the footprint
        return entry["mm"]
    db = database_height(footprint, part_name)
    if db is not None:                            # the built-in table
        return db[0]
    mm = file_heights.get((fk, key))
    if mm:                                        # this part's own Height(mm)
        return mm
    est = entry.get("estimate") if entry else estimate_height(footprint)[0]
    return est if est is not None else float("inf")


# --------------------------------------------------------------------------
# Place height from the part's body height
# --------------------------------------------------------------------------
# The machine's Place Height is where the nozzle tip stops. At BOARD_SURFACE_MM
# the tip touches the bare board; a part of height h held on the tip touches
# the paste at BOARD_SURFACE_MM + h. Stopping there would leave it sitting on
# the paste, so the head goes PRESS_FRACTION of the part's height further down
# to press it in:   place height = surface + h * (1 - press).
# e.g. surface 2.0, a 1.0mm part, 30% -> 2.70.
BOARD_SURFACE_MM = 2.0
PRESS_FRACTION = 0.30


def known_part_height(footprint, part_name, suspects=None):
    """The body height if anything actually says it, else None (not a guess)."""
    h = component_height(footprint, part_name, suspects)
    if h == float("inf"):
        return None
    key = part_name_key(part_name)
    if key in part_heights:
        return h
    fk = footprint_key(footprint)
    entry = footprint_heights.get(fk)
    if entry and entry.get("source") == "unknown" and not file_heights.get((fk, key)):
        return None                       # a placeholder, not a height
    if not entry and estimate_height(footprint)[0] is None:
        return None
    return h


def place_height_for(footprint, part_name, suspects=None):
    """Place Height (mm) for a part, or None when its height is not known."""
    h = known_part_height(footprint, part_name, suspects)
    if h is None:
        return None
    return round(BOARD_SURFACE_MM + h * (1.0 - PRESS_FRACTION), 2)


def apply_place_heights(feeders=None):
    """Sets Place Height on every feeder whose part's height is known."""
    suspects = suspect_part_keys()
    n = 0
    for f in machine_feeders() if feeders is None else feeders:
        part = str(field_at(f, 8)).strip()
        if not part or feeder_is_fiducial(f) or "/" not in part:
            continue
        fp, value = part.split("/", 1)
        ph = place_height_for(fp, value, suspects)
        if ph is not None and str(field_at(f, 11)) != "%.2f" % ph:
            set_field(f, 11, "%.2f" % ph)
            n += 1
    return n


def height_span_label(heights):
    """"0.95", "1.30-2.65", or "?" -- the heights a set of components get."""
    known = [h for h in heights if h != float("inf")]
    if not known:
        return "?"
    lo, hi = min(known), max(known)
    text = "%.2f" % lo if lo == hi else "%.2f-%.2f" % (lo, hi)
    return text + (" ?" if len(known) < len(heights) else "")


def part_height_label(part_name, footprints, suspects):
    key = part_name_key(part_name)
    if key in part_heights:
        return "%.2f *" % part_heights[key]
    if key in suspects:
        return "?"
    return height_span_label([component_height(f, part_name, suspects)
                              for f in footprints])


def ask_part_height(part_name):
    """Small dialog: type a height for one part, or clear it."""
    key = part_name_key(part_name)
    popup = tk.Toplevel(root)
    popup.title("Height: %s" % part_name)
    popup.configure(bg=bg_color)
    popup.transient(root)
    popup.resizable(False, False)

    frame = tk.Frame(popup, bg=bg_color, padx=16, pady=14)
    frame.pack(fill="both", expand=True)

    footprints = sorted({str(r[1]) for r in placeable_rows()
                         if part_name_key(r[10]) == key})
    count = sum(1 for r in placeable_rows() if part_name_key(r[10]) == key)
    tk.Label(frame, text="%s   (%d component%s on %s)"
                         % (part_name, count, "" if count == 1 else "s",
                            ", ".join(footprints) or "-"),
             bg=bg_color, fg=frame_title_color,
             font=("Segoe UI", 10, "bold")).grid(row=0, column=0, columnspan=3,
                                                 sticky="w", pady=(0, 6))

    if key in suspect_part_keys() and key not in part_heights:
        tk.Label(frame,
                 text="Designated as an IC but drawn on a chip footprint, so its "
                      "footprint height is not trusted. It goes last until you set one.",
                 bg=bg_color, fg="#ff9900", font=("Segoe UI", 9), wraplength=380,
                 justify="left").grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 8))

    create_label(frame, "Body height (mm):").grid(row=2, column=0, sticky="e", padx=(0, 6))
    var = tk.StringVar(value=("%.2f" % part_heights[key]) if key in part_heights else "")
    entry = create_entry(frame, width=10, var=var)
    entry.grid(row=2, column=1, sticky="w")
    entry.focus_set()

    def save(event=None):
        text = var.get().strip()
        try:
            mm = float(text)
        except ValueError:
            messagebox.showerror("Height", "'%s' is not a number." % text, parent=popup)
            return
        if mm <= 0:
            messagebox.showerror("Height", "Height must be greater than zero.", parent=popup)
            return
        part_heights[key] = mm
        popup.destroy()
        refresh_table()

    def clear():
        part_heights.pop(key, None)
        popup.destroy()
        refresh_table()

    btns = tk.Frame(frame, bg=bg_color)
    btns.grid(row=3, column=0, columnspan=3, sticky="e", pady=(12, 0))
    if key in part_heights:
        tk.Button(btns, text="Use Footprint Height", command=clear, bg=entry_bg,
                  fg=fg_color, font=("Segoe UI", 9)).pack(side="left", padx=(0, 8))
    tk.Button(btns, text="Set", command=save, bg=btn_bg, fg=btn_fg, width=10,
              font=("Segoe UI", 9, "bold")).pack(side="left")
    entry.bind("<Return>", save)


def on_part_double_click(event=None):
    name = tv_selected_key(variant_tree)
    if name is not None:
        ask_part_height(name)


def estimate_height(footprint):
    """Body height guessed from the footprint name, as (mm or None, reason)."""
    name = str(footprint).upper()
    for pattern, mm, note in PACKAGE_HEIGHT_RULES:
        m = re.search(pattern, name)
        if not m:
            continue
        if mm is CAPTURED:
            try:
                return float(m.group(1)), note
            except (IndexError, TypeError, ValueError):
                continue        # the number did not parse -- keep looking
        return mm, note         # mm may be None: recognised, height unknown
    return None, "not recognised"


def learn_footprint_heights():
    """
    Builds the height table for the loaded board: the file's own Height(mm)
    where it is filled in, an estimate from the footprint name otherwise.
    Heights the user typed in are kept.
    """
    manual = {k: v for k, v in footprint_heights.items() if v["source"] == "manual"}
    footprint_heights.clear()

    from_file = {}
    for (fk, _pk), mm in file_heights.items():
        from_file[fk] = max(from_file.get(fk, 0.0), mm)

    for r in placeable_rows():
        name = str(r[1])
        key = footprint_key(name)
        if key in footprint_heights:
            continue
        est_mm, est_note = estimate_height(name)
        db = database_height(name, r[10])
        if key in manual:
            entry = dict(manual[key])
        elif db is not None:
            entry = {"mm": db[0], "source": "built-in", "note": db[1], "name": name}
        elif from_file.get(key):
            # shown for the footprint as a whole; each part still uses its own
            entry = {"mm": from_file[key], "source": "file",
                     "note": "Height(mm) column", "name": name}
        else:
            entry = {"mm": est_mm,
                     "source": "estimate" if est_mm is not None else "unknown",
                     "note": est_note, "name": name}
        entry["estimate"] = est_mm
        footprint_heights[key] = entry


def footprint_height(footprint):
    """Height used for ordering. Unknown sorts last -- the safe direction."""
    entry = footprint_heights.get(footprint_key(footprint))
    if entry is None:
        # not on the loaded board (an imported machine program, say):
        # the footprint name is still worth reading
        mm, _ = estimate_height(footprint)
        return mm if mm is not None else float("inf")
    if entry["mm"] is not None:
        return entry["mm"]
    return float("inf")


def height_label(footprint):
    entry = footprint_heights.get(footprint_key(footprint))
    if not entry or entry["mm"] is None:
        return "?"
    mark = {"file": "", "estimate": " ~", "manual": " *"}.get(entry["source"], "")
    return "%.2f%s" % (entry["mm"], mark)

def optimize_placement_route(rows):
    # Assuming rows structure: [Designator, Footprint, Mid X, Mid Y, ..., Comment]
    # Adjust the indices below if your CSV structure is different:
    # FOOTPRINT_IDX = 1, X_IDX = 2, Y_IDX = 3, COMMENT_IDX = 10
    FOOTPRINT_IDX = 1
    X_IDX = 2
    Y_IDX = 3
    COMMENT_IDX = 10

    # Step 1: group by body height, then by part within a height
    suspects = suspect_part_keys()
    grouped_parts = {}
    for row in rows:
        height = component_height(row[FOOTPRINT_IDX], row[COMMENT_IDX], suspects)
        comment = str(row[COMMENT_IDX]).strip()

        group_key = (height, comment)
        if group_key not in grouped_parts:
            grouped_parts[group_key] = []
        grouped_parts[group_key].append(row)

    optimized_rows = []

    # Step 2: shortest first. Parts of unknown height sort last, which is the
    # direction that cannot cause the head to strike something already placed.
    sorted_group_keys = sorted(grouped_parts.keys(), key=lambda k: (k[0], k[1]))

    # Step 3: Apply Nearest Neighbor routing within each group
    for key in sorted_group_keys:
        group_items = grouped_parts[key]

        # Start routing from the lowest X (left side of the board)
        # We find the part with the minimum X to be our starting node for this feeder
        current_node = min(group_items, key=lambda r: float(r[X_IDX]))
        group_items.remove(current_node)
        optimized_rows.append(current_node)

        while group_items:
            # Calculate Euclidean distance: d = sqrt((x2-x1)^2 + (y2-y1)^2)
            curr_x, curr_y = float(current_node[X_IDX]), float(current_node[Y_IDX])

            # Find the nearest neighbor
            nearest_node = min(
                group_items,
                key=lambda r: math.hypot(float(r[X_IDX]) - curr_x, float(r[Y_IDX]) - curr_y)
            )

            group_items.remove(nearest_node)
            optimized_rows.append(nearest_node)
            current_node = nearest_node

    return optimized_rows

def refresh_table():
    tree.delete(*tree.get_children())
    model = sort_model.get()

    reverse = "decrease" in model
    if model == "No Sort":
        sorted_rows = rows.copy()
    elif "Auto" in model:
        # Apply Auto route optimization and height sorting
        sorted_rows = optimize_placement_route(rows)
        # If user selected a reverse Auto model (e.g., "Auto decrease")
        if reverse:
            sorted_rows.reverse()
    elif "Pythagoras" in model:
        sorted_rows = sorted(
            rows,
            key=lambda r: xy_pythagoras(float(r[2]) if r[2] else 0, float(r[3]) if r[3] else 0),
            reverse=reverse
        )
    elif "Center-X" in model:
        sorted_rows = sorted(rows, key=lambda r: float(r[2]) if r[2] else 0, reverse=reverse)
    elif "Center-Y" in model:
        sorted_rows = sorted(rows, key=lambda r: float(r[3]) if r[3] else 0, reverse=reverse)
    elif "Designator" in model:
        sorted_rows = sorted(rows, key=lambda r: r[0], reverse=reverse)
    elif "Comment" in model:
        sorted_rows = sorted(rows, key=lambda r: r[10] if r[10] else "", reverse=reverse)
    else:
        sorted_rows = rows.copy()

    # Move fiducials to the top
    fid_rows = [r for r in sorted_rows if str(r[0]).upper().startswith("FID")]
    other_rows = [r for r in sorted_rows if not str(r[0]).upper().startswith("FID")]
    final_rows = fid_rows + other_rows

    # Map each row object to its position in the global `rows` list, so a
    # Treeview item can always be traced back to the exact row it displays
    # regardless of the sort model in use.
    source_index = {id(r): i for i, r in enumerate(rows)}

    for r in final_rows:
        display_row = r.copy()
        for idx in [2, 3, 4, 5, 6, 7]:
            try:
                val = float(display_row[idx])
                display_row[idx] = f"{val:.2f}"
            except:
                pass

        layer_val = str(display_row[8]).strip().lower()
        if layer_val in ("toplayer", "top"):
            display_row[8] = "T"
        elif layer_val in ("bottomlayer", "bottom"):
            display_row[8] = "B"

        tree.insert("", "end", iid=str(source_index[id(r)]), values=display_row)

    update_total_count()
    validate_board_coordinates()
    refresh_footprint()
    refresh_variants()
    update_totalfootprint_count()
    update_totalBOM_count()

    # live link: a program built from this list follows it without a Build
    if sync_machine_from_rows():
        refresh_feeder_table()
        refresh_machine_comp_table()

    render_pcb_preview()

def refresh_variants():
    variant_tree.delete(*variant_tree.get_children())
    counter = Counter(str(r[10]) for r in placeable_rows())
    dupe_keys = similar_part_groups()
    flagged = {s for c in dupe_keys.values() for s in c}
    suspects = suspect_part_keys()
    footprints_of = {}
    for r in placeable_rows():
        footprints_of.setdefault(str(r[10]), set()).add(str(r[1]))
    sorted_parts = sorted(counter.items(), key=lambda x: x[1], reverse=True)
    for part, qty in sorted_parts:
        label = part_height_label(part, footprints_of.get(part, ()), suspects)
        tags = ()
        if part in flagged:
            tags = ("dupe",)
        elif label == "?":
            tags = ("noheight",)
        variant_tree.insert("", "end", iid=tv_key(part),
                            values=(part, qty, label), tags=tags)
    variant_tree.tag_configure("dupe", foreground="#ff9900")
    variant_tree.tag_configure("noheight", foreground="#ff6666")

def refresh_footprint():
    learn_footprint_heights()
    footprint_tree.delete(*footprint_tree.get_children())
    counter = Counter(str(r[1]) for r in placeable_rows())
    suspects = suspect_part_keys()
    spans = {}
    for r in placeable_rows():
        h = component_height(r[1], r[10], suspects)
        lo, hi = spans.get(str(r[1]), (h, h))
        spans[str(r[1])] = (min(lo, h), max(hi, h))

    def label(fp):
        lo, hi = spans[fp]
        fmt = lambda v: "?" if v == float("inf") else "%.2f" % v
        return fmt(lo) if lo == hi else "%s-%s" % (fmt(lo), fmt(hi))

    # shortest first, so the table reads in the order the board is assembled
    ordered = sorted(counter.items(), key=lambda kv: (spans[kv[0]][0], -kv[1], kv[0]))
    for foorprint, qty in ordered:
        footprint_tree.insert(
            "", "end", iid=tv_key(foorprint),
            values=(foorprint, qty, label(foorprint)),
            tags=("noheight",) if spans[foorprint][1] == float("inf") else ())
    footprint_tree.tag_configure("noheight", foreground="#ff9900")

last_rotation = 0.0
# Set while a project is restored: the field must show the saved angle without
# turning a board that was saved already turned.
suspend_board_rotation = False

def on_board_rotation_change(*args):
    global last_rotation
    if suspend_board_rotation:
        return
    try:
        new_angle = float(board_rotation_var.get())
    except:
        return
    delta = new_angle - last_rotation
    last_rotation = new_angle
    rotate_board_polar(delta)

def rotate_board_quarter(degrees):
    """
    Turns the whole board by +90, -90 or 180 about its own centre, so it stays
    where it was on the machine. Every part, its pads and its rotation turn
    with it; fiducial rows do not move -- the operator sets those on the
    machine. A linked machine program follows through the live link.
    Returns the pivot used, or None if there was nothing to turn.
    """
    parts = [r for r in rows if not str(r[0]).strip().upper().startswith("FID")]
    pts = []
    for r in parts:
        try:
            pts.append((float(r[2]), float(r[3])))
        except (TypeError, ValueError):
            continue
    if not pts:
        return None
    cx = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2.0
    cy = (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2.0
    xform_then_rotation(degrees, cx, cy)
    for r in parts:
        for xi, yi in ((2, 3), (4, 5), (6, 7)):
            try:
                x, y = rotate_about(float(r[xi]), float(r[yi]), cx, cy, degrees)
            except (TypeError, ValueError):
                continue
            r[xi] = round(clean_zero(x), 3)
            r[yi] = round(clean_zero(y), 3)
        try:
            r[9] = tidy_angle(float(r[9]) + degrees)
        except (TypeError, ValueError):
            r[9] = int(degrees) % 360
    refresh_table()
    return cx, cy


def xy_pythagoras(x, y):
    return math.sqrt(x**2 + y**2)

def flip_x():
    try:
        bw = float(board_width.get())
    except:
        messagebox.showerror("Error", "Invalid Board Width")
        return
    xform_then(-1.0, 0.0, 0.0, 1.0, bw, 0.0)
    for r in rows:
        r[2] = round(bw - float(r[2]), 3)
        r[4] = round(bw - float(r[4]), 3)
        r[6] = round(bw - float(r[6]), 3)
    # Mirroring moves pad 1 to the other side but used to leave Rotation as it
    # was. The machine angle follows from the mirrored pads.
    normalise_rotations()
    refresh_table()

def flip_y():
    try:
        bh = float(board_height.get())
    except:
        messagebox.showerror("Error", "Invalid Board Height")
        return
    xform_then(1.0, 0.0, 0.0, -1.0, 0.0, bh)
    for r in rows:
        r[3] = abs((0-bh)+float(r[3]))
        r[5] = abs((0-bh)+float(r[5]))
        r[7] = abs((0-bh)+float(r[7]))
    normalise_rotations()
    refresh_table()

def rename_part():
    old_name = tv_selected_key(variant_tree)
    if old_name is None: return
    new_name = rename_entry.get().strip()
    if not new_name: return
    renamed = 0
    for r in rows:
        if str(r[10]) == old_name:
            r[10] = new_name
            renamed += 1
    if renamed == 0:
        messagebox.showwarning("Rename Part", f"No component found with Part Name: {old_name}")
        return
    refresh_table()

def open_similar_names(opened_automatically=False):
    """
    Lists part names that are the same part spelled differently, and merges
    each group onto one spelling. This matters beyond the BOM count: the
    machine program gives every distinct spelling its own feeder.
    """
    groups = similar_part_groups()
    if not groups:
        if not opened_automatically:
            messagebox.showinfo(
                "Similar Part Names",
                "Every part name is spelled consistently.\n\nNothing to merge.")
        return

    popup = tk.Toplevel(root)
    popup.title("Similar Part Names")
    popup.geometry("680x460")
    popup.configure(bg=bg_color)
    popup.transient(root)

    intro = ("Part names were upper-cased on import, but these are still spelled more "
             "than one way -- a letter O typed for a zero, or an optional unit. Each "
             "spelling would get its own feeder, so merge them onto one name."
             if opened_automatically else
             "These names differ only in spacing, a letter O typed for a zero, or an "
             "optional unit. Each spelling would get its own feeder, so merge them onto "
             "one name.")
    tk.Label(popup, text=intro, bg=bg_color, fg=fg_color, font=("Segoe UI", 9),
             wraplength=640, justify="left").pack(anchor="w", padx=15, pady=(14, 10))

    if opened_automatically:
        tk.Label(popup,
                 text="Worth fixing in the schematic too, so the next export is clean.",
                 bg=bg_color, fg="#ff9900", font=("Segoe UI", 9, "italic"),
                 wraplength=640, justify="left").pack(anchor="w", padx=15, pady=(0, 8))

    table = tk.Frame(popup, bg=bg_color)
    table.pack(fill="both", expand=True, padx=15)
    scroll = ttk.Scrollbar(table, orient="vertical")
    scroll.pack(side="right", fill="y")
    cols = ("Spellings found", "Components", "Merge into")
    dupe_tree = ttk.Treeview(table, columns=cols, show="headings", yscrollcommand=scroll.set)
    scroll.config(command=dupe_tree.yview)
    for c, w in zip(cols, (330, 90, 160)):
        dupe_tree.heading(c, text=c)
        dupe_tree.column(c, width=w, anchor="w")
    dupe_tree.pack(side="left", fill="both", expand=True)

    # The spelling used on the most components wins by default.
    targets = {}

    def load():
        dupe_tree.delete(*dupe_tree.get_children())
        for key, spellings in sorted(groups.items(),
                                     key=lambda kv: -sum(kv[1].values())):
            targets.setdefault(key, spellings.most_common(1)[0][0])
            shown = ",  ".join("%s (%d)" % (s, n) for s, n in spellings.most_common())
            dupe_tree.insert("", "end", iid=tv_key(key),
                             values=(shown, sum(spellings.values()), targets[key]))

    load()

    form = tk.Frame(popup, bg=bg_color)
    form.pack(fill="x", padx=15, pady=12)
    create_label(form, "Merge the selected group into:").pack(side="left")
    target_var = tk.StringVar()
    target_combo = ttk.Combobox(form, textvariable=target_var, width=24, state="readonly")
    target_combo.pack(side="left", padx=8)

    def on_select(event=None):
        key = tv_selected_key(dupe_tree)
        if key is None:
            return
        target_combo["values"] = [s for s, _ in groups[key].most_common()]
        target_var.set(targets[key])

    dupe_tree.bind("<<TreeviewSelect>>", on_select)

    def set_target():
        key = tv_selected_key(dupe_tree)
        if key is None or not target_var.get():
            return
        targets[key] = target_var.get()
        load()

    create_btn(form, "Set", set_target).pack(side="left")

    def merge(selected_only):
        keys = [tv_selected_key(dupe_tree)] if selected_only else list(groups)
        keys = [k for k in keys if k]
        if not keys:
            messagebox.showinfo("Similar Part Names", "Select a group first.")
            return

        changed = 0
        for key in keys:
            target = targets[key]
            spellings = set(groups[key])
            for r in rows:
                if str(r[10]) in spellings and str(r[10]) != target:
                    r[10] = target
                    changed += 1

        popup.destroy()
        refresh_table()
        messagebox.showinfo(
            "Similar Part Names",
            "Renamed %d component(s) across %d group(s)." % (changed, len(keys)))

    btns = tk.Frame(popup, bg=bg_color)
    btns.pack(fill="x", padx=15, pady=(0, 14))
    tk.Button(btns, text="Merge All Groups", command=lambda: merge(False),
              bg=btn_bg, fg=btn_fg, width=18,
              font=("Segoe UI", 9, "bold")).pack(side="right")
    tk.Button(btns, text="Merge Selected", command=lambda: merge(True),
              bg=entry_bg, fg=fg_color, width=16,
              font=("Segoe UI", 9)).pack(side="right", padx=8)
    tk.Button(btns, text="Leave As Is", command=popup.destroy,
              bg=entry_bg, fg=fg_color, width=14,
              font=("Segoe UI", 9)).pack(side="left")


def open_height_editor():
    """
    Review the body height used to order assembly, and correct it where the
    library did not say and the footprint name gave nothing away.
    """
    if not rows:
        messagebox.showerror("Component Heights", "Load a Pick & Place file first.")
        return

    learn_footprint_heights()

    popup = tk.Toplevel(root)
    popup.title("Component Heights")
    popup.geometry("880x600")
    popup.configure(bg=bg_color)
    popup.transient(root)

    tk.Label(popup,
             text="Assembly runs shortest part first, so the head never reaches over "
                  "something already placed. Heights come from the file's Height(mm) "
                  "column where the library fills it in, and from the footprint name "
                  "otherwise. A part with no height is placed last.",
             bg=bg_color, fg=fg_color, font=("Segoe UI", 9), wraplength=720,
             justify="left").pack(anchor="w", padx=15, pady=(14, 10))

    table = tk.Frame(popup, bg=bg_color)
    table.pack(fill="both", expand=True, padx=15)
    scroll = ttk.Scrollbar(table, orient="vertical")
    scroll.pack(side="right", fill="y")
    cols = ("Footprint", "Qty", "Height mm", "Place Height", "Source", "Based on")
    h_tree = ttk.Treeview(table, columns=cols, show="headings", yscrollcommand=scroll.set)
    scroll.config(command=h_tree.yview)
    for c, w in zip(cols, (230, 45, 75, 85, 70, 190)):
        h_tree.heading(c, text=c)
        h_tree.column(c, width=w, anchor="w")
    h_tree.pack(side="left", fill="both", expand=True)

    def load():
        h_tree.delete(*h_tree.get_children())
        counts = Counter(str(r[1]) for r in placeable_rows())
        suspects = suspect_part_keys()
        heights = {}
        for r in placeable_rows():
            heights.setdefault(str(r[1]), []).append(
                component_height(r[1], r[10], suspects))
        order = sorted(counts, key=lambda f: (min(heights[f]), f))
        place = {}
        for r in placeable_rows():
            ph = place_height_for(r[1], r[10], suspects)
            place.setdefault(str(r[1]), []).append(ph if ph is not None else float("inf"))
        for fp in order:
            e = footprint_heights.get(footprint_key(fp), {})
            label = height_span_label(heights[fp])
            h_tree.insert("", "end", iid=tv_key(fp),
                          values=(fp, counts[fp], label, height_span_label(place[fp]),
                                  e.get("source", "?"), e.get("note", "")),
                          tags=("unknown",) if "?" in label else ())
        h_tree.tag_configure("unknown", foreground="#ff9900")
        unknown = sum(1 for f in counts if "?" in height_span_label(heights[f]))
        status.config(
            text=("%d footprint(s) have parts with no height; those go last. A wrong "
                  "footprint for one part (an IC drawn on an 0402) is better fixed per "
                  "part: double-click it in the Part Name table." % unknown)
                 if unknown else "Every footprint has a height.",
            fg="#ff9900" if unknown else "#5ce65c")

    form = tk.Frame(popup, bg=bg_color)
    form.pack(fill="x", padx=15, pady=12)
    create_label(form, "Height (mm):").pack(side="left")
    mm_var = tk.StringVar()
    create_entry(form, width=10, var=mm_var).pack(side="left", padx=8)

    def on_select(event=None):
        fp = tv_selected_key(h_tree)
        if fp is None:
            return
        e = footprint_heights.get(footprint_key(fp), {})
        mm_var.set("%.2f" % e["mm"] if e.get("mm") is not None else "")

    h_tree.bind("<<TreeviewSelect>>", on_select)

    def apply_height():
        fp = tv_selected_key(h_tree)
        if fp is None:
            messagebox.showinfo("Component Heights", "Select a footprint first.")
            return
        text = mm_var.get().strip()
        if not text:
            messagebox.showerror("Component Heights", "Enter a height in millimetres.")
            return
        try:
            mm = float(text)
        except ValueError:
            messagebox.showerror("Component Heights", "'%s' is not a number." % text)
            return
        if mm <= 0:
            messagebox.showerror("Component Heights", "Height must be greater than zero.")
            return
        footprint_heights[footprint_key(fp)] = {
            "mm": mm, "source": "manual", "note": "typed in", "name": fp}
        load()
        refresh_table()

    create_btn(form, "Set Height", apply_height).pack(side="left")

    # place height = nozzle-touches-board height + part height, less the press
    pform = tk.Frame(popup, bg=bg_color)
    pform.pack(fill="x", padx=15, pady=(0, 6))
    create_label(pform, "Nozzle touches PCB at (mm):").pack(side="left")
    surface_var = tk.StringVar(value="%.2f" % BOARD_SURFACE_MM)
    create_entry(pform, width=7, var=surface_var).pack(side="left", padx=6)
    create_label(pform, "Press into paste (% of height):").pack(side="left", padx=(10, 0))
    press_var = tk.StringVar(value="%g" % (PRESS_FRACTION * 100))
    create_entry(pform, width=6, var=press_var).pack(side="left", padx=6)

    def read_place_settings():
        global BOARD_SURFACE_MM, PRESS_FRACTION
        try:
            s, p = float(surface_var.get()), float(press_var.get())
        except ValueError:
            messagebox.showerror("Component Heights", "Surface and press must be numbers.")
            return False
        if not 0 <= p < 100:
            messagebox.showerror("Component Heights", "Press must be 0 to 99 %.")
            return False
        BOARD_SURFACE_MM, PRESS_FRACTION = s, p / 100.0
        try:
            save_settings()
        except OSError:
            pass
        return True

    def recalc():
        if read_place_settings():
            load()

    def apply_to_feeders():
        if not read_place_settings():
            return
        load()
        if not machine_feeders():
            messagebox.showinfo("Component Heights", "There is no machine program yet; "
                                "Build uses these place heights.")
            return
        n = apply_place_heights()
        refresh_feeder_table()
        messagebox.showinfo("Component Heights",
                            "Place Height set on %d feeder(s): %.2f + height x %.0f%%. "
                            "Parts with no known height were left as they were."
                            % (n, BOARD_SURFACE_MM, (1 - PRESS_FRACTION) * 100))

    create_btn(pform, "Recalculate", recalc).pack(side="left", padx=(6, 4))
    create_btn(pform, "Apply Place Heights to Feeders", apply_to_feeders).pack(side="left")

    status = tk.Label(popup, text="", bg=bg_color, font=("Segoe UI", 9, "italic"))
    status.pack(anchor="w", padx=15)

    btns = tk.Frame(popup, bg=bg_color)
    btns.pack(fill="x", padx=15, pady=(8, 14))
    tk.Button(btns, text="Close", command=popup.destroy, bg=entry_bg, fg=fg_color,
              width=12, font=("Segoe UI", 9)).pack(side="right")

    load()


def remove_part():
    global rows
    partname = tv_selected_key(variant_tree)
    if partname is None: return
    matches = [r for r in rows if str(r[10]) == partname]
    if not matches:
        messagebox.showwarning("Delete Part", f"No component found with Part Name: {partname}")
        return
    confirm = messagebox.askyesno("Delete Part", f"Are you sure you want to delete this part?\n\nPart Name: {partname}\nComponents: {len(matches)}")
    if not confirm: return
    rows = [r for r in rows if str(r[10]) != partname]
    refresh_table()

def remove_footprint():
    global rows
    footprintname = tv_selected_key(footprint_tree)
    if footprintname is None: return
    matches = [r for r in rows if str(r[1]) == footprintname]
    if not matches:
        messagebox.showwarning("Delete Footprint", f"No component found with Footprint: {footprintname}")
        return
    confirm = messagebox.askyesno("Delete Footprint", f"Are you sure you want to delete this footprint?\n\nFootprint: {footprintname}\nComponents: {len(matches)}")
    if not confirm: return
    rows = [r for r in rows if str(r[1]) != footprintname]
    refresh_table()

def add_FIDUCIAL():
    try:
        x = float(fid_x.get())
        y = float(fid_y.get())
    except:
        messagebox.showerror("Error", "Invalid FIDUCIAL coordinates")
        return

    fid_row = [
        "FID", "FIDUCIAL", x, y, x, y, x, y, "Top", 0, "FIDUCIAL"
    ]
    non_fids = [r for r in rows if r[0] != "FID"]
    rows.clear()
    rows.append(fid_row)
    rows.extend(non_fids)
    refresh_table()
    refresh_variants()

def delete_selected_row():
    sel = tree.selection()
    if not sel: return
    # The item id is the row's index in `rows`, not its visual position,
    # so this stays correct while a sort model is active.
    try:
        index = int(sel[0])
    except ValueError:
        return
    if index < 0 or index >= len(rows): return
    row = rows[index]
    confirm = messagebox.askyesno("Delete Row", f"Are you sure you want to delete this row?\n\nDesignator: {row[0]}")
    if not confirm: return
    rows.pop(index)
    refresh_table()

def export_csv():
    file_path = filedialog.asksaveasfilename(
        defaultextension=".csv", filetypes=[("CSV files", "*.csv")], title="Save CSV"
    )
    if file_path:
        with open(file_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(neoden_headers)
            for row_id in tree.get_children():
                writer.writerow(tree.item(row_id)["values"])
        tk.messagebox.showinfo("Export", f"File saved to:\n{file_path}")

# =========================================================
# Two-point camera alignment
# =========================================================
#
# The board sits on the machine at an unknown position and, usually, a small
# unknown rotation. Jogging the camera onto ONE point can only give a shift, so
# any rotation stays baked in. Two points give the rigid transform outright:
#
#   theta  = angle(measured2 - measured1) - angle(design2 - design1)
#   scale  = |measured2 - measured1| / |design2 - design1|
#   p'     = measured1 + R(theta) * (p - design1)
#
# Scale should come out at 1.000. It is computed as a check: a scale that is off
# means one of the two points was measured on the wrong component, and that is
# worth catching before the whole board is transformed.

ALIGN_MIN_BASELINE_MM = 10.0     # below this, angle error from a 0.05mm sighting blows up
ALIGN_SCALE_TOLERANCE = 0.002    # 0.2%
ALIGN_GOOD_ISOLATION_MM = 8.0    # a same-part neighbour closer than this is a mis-sight risk


def _part_key(r):
    return (str(r[1]).strip(), str(r[10]).strip())


def alignment_identifiability(target_rows):
    """
    For each component, the distance to the nearest OTHER component of the same
    footprint and value -- i.e. how far the camera can be off before you would
    be looking at a part that is indistinguishable from the intended one.
    A component that is the only one of its kind scores infinity.
    """
    groups = {}
    for r in target_rows:
        groups.setdefault(_part_key(r), []).append(r)

    scores = {}
    for key, members in groups.items():
        if len(members) == 1:
            scores[id(members[0])] = float("inf")
            continue
        # Capped so a board with thousands of identical parts stays fast
        sample = members[:400]
        for r in members:
            best = float("inf")
            try:
                rx, ry = float(r[2]), float(r[3])
            except (ValueError, TypeError):
                scores[id(r)] = 0.0
                continue
            for o in sample:
                if o is r:
                    continue
                try:
                    d = math.hypot(float(o[2]) - rx, float(o[3]) - ry)
                except (ValueError, TypeError):
                    continue
                if d < best:
                    best = d
            scores[id(r)] = best
    return scores


def suggest_alignment_pairs(target_rows, how_many=6):
    """
    Ranks candidate sighting pairs. A good pair is far apart (long baseline, so
    the measured angle is precise) and made of components that cannot be
    confused with an identical neighbour under the camera.

    Fiducials are never offered: the fiducial line is a placeholder (often at
    0,0) whose real position is set on the machine, so sighting it would align
    the board to nothing.
    """
    usable = []
    for r in target_rows:
        if is_fiducial(r):
            continue
        try:
            float(r[2]), float(r[3])
        except (ValueError, TypeError):
            continue
        usable.append(r)

    if len(usable) < 2:
        return []

    pool = usable
    scores = alignment_identifiability(pool)

    # Keep the most identifiable candidates, then look for the longest baseline
    # among them. 60 candidates is 1770 pairs -- instant, and plenty of choice.
    ranked = sorted(pool, key=lambda r: -scores.get(id(r), 0.0))
    candidates = ranked[:60]

    pairs = []
    for i in range(len(candidates)):
        a = candidates[i]
        ax, ay = float(a[2]), float(a[3])
        for j in range(i + 1, len(candidates)):
            b = candidates[j]
            baseline = math.hypot(float(b[2]) - ax, float(b[3]) - ay)
            if baseline < ALIGN_MIN_BASELINE_MM:
                continue
            worst_isolation = min(scores.get(id(a), 0.0), scores.get(id(b), 0.0))
            confidence = min(worst_isolation, ALIGN_GOOD_ISOLATION_MM) / ALIGN_GOOD_ISOLATION_MM
            pairs.append((baseline * (0.35 + 0.65 * confidence), baseline, worst_isolation, a, b))

    pairs.sort(key=lambda p: -p[0])

    # Do not offer the same component twice in a row of suggestions
    chosen = []
    for _, baseline, isolation, a, b in pairs:
        if any(a is x or a is y or b is x or b is y for _, _, x, y in chosen):
            continue
        chosen.append((baseline, isolation, a, b))
        if len(chosen) >= how_many:
            break

    if not chosen and pairs:
        _, baseline, isolation, a, b = pairs[0]
        chosen = [(baseline, isolation, a, b)]
    return chosen


def compute_two_point_transform(design_a, measured_a, design_b, measured_b):
    """
    Returns (theta_deg, scale, baseline_design, baseline_measured), or raises
    ValueError if the two design points are too close together to define an angle.
    """
    dxd = design_b[0] - design_a[0]
    dyd = design_b[1] - design_a[1]
    dxm = measured_b[0] - measured_a[0]
    dym = measured_b[1] - measured_a[1]

    len_d = math.hypot(dxd, dyd)
    len_m = math.hypot(dxm, dym)

    if len_d < ALIGN_MIN_BASELINE_MM:
        raise ValueError(
            "The two chosen components are only %.2fmm apart in the design.\n"
            "Pick a pair at least %.0fmm apart -- a short baseline turns a small "
            "sighting error into a large rotation error." % (len_d, ALIGN_MIN_BASELINE_MM))
    if len_m < 1e-6:
        raise ValueError("The two measured points are identical.")

    theta = math.degrees(math.atan2(dym, dxm) - math.atan2(dyd, dxd))
    theta = (theta + 180.0) % 360.0 - 180.0
    return theta, len_m / len_d, len_d, len_m


def apply_two_point_transform(design_a, measured_a, theta_deg, scale):
    """Maps every coordinate column, and every component rotation, into machine space."""
    theta = math.radians(theta_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    ax, ay = design_a
    mx, my = measured_a
    XY_COLUMNS = [(2, 3), (4, 5), (6, 7)]
    A, B = scale * cos_t, -scale * sin_t
    C, D = scale * sin_t, scale * cos_t
    xform_then(A, B, C, D, mx - (A * ax + B * ay), my - (C * ax + D * ay))

    for r in rows:
        for xi, yi in XY_COLUMNS:
            try:
                dx = (float(r[xi]) - ax) * scale
                dy = (float(r[yi]) - ay) * scale
            except (ValueError, TypeError):
                continue
            r[xi] = round(clean_zero(mx + dx * cos_t - dy * sin_t), 3)
            r[yi] = round(clean_zero(my + dx * sin_t + dy * cos_t), 3)

        try:
            # the board's turn, to the hundredth: an alignment turn is often
            # well under a degree and must not round away
            r[9] = tidy_angle(float(r[9]) + theta_deg)
        except (ValueError, TypeError):
            pass

    refresh_table()


def board_side_auto():
    """
    Which side of the board the parts are placed on: ("top" | "bottom", why).
    The Gerber decides when there is one -- the copper the pads sit on is the
    truth, and on BOARD-A it disagreed with the Pick & Place layer column for
    186 parts. Without a Gerber, the layer column is all there is.
    """
    if gerber_state["layers"]:
        ox, oy = gerber_state["offset"]
        flashes = {}
        for L in gerber_state["layers"]:
            if L["kind"] == "copper" and L["side"] in ("top", "bottom"):
                flashes.setdefault(L["side"], []).extend(
                    (fx, fy) for fx, fy, _, _ in L["flashes"])
        found = Counter()
        total = 0
        for r in placeable_rows():
            try:
                px, py = xform_invert(float(r[6]), float(r[7]))
            except (TypeError, ValueError):
                continue
            total += 1
            for side, fl in flashes.items():
                if fl and min((fx - ox - px) ** 2 + (fy - oy - py) ** 2
                              for fx, fy in fl) < 0.2 ** 2:
                    found[side] += 1
                    break
        if found:
            side, n = found.most_common(1)[0]
            return side, "%d of %d parts have their pads on %s copper (Gerber)" % (
                n, total, side)
    listed = Counter("bottom" if "bottom" in str(r[8]).lower() else "top"
                     for r in placeable_rows())
    if not listed:
        return "top", "no parts"
    side, n = listed.most_common(1)[0]
    return side, "%d of %d parts are listed %s in the Pick & Place file" % (
        n, sum(listed.values()), side)


def board_is_mirrored():
    a, b, c, d, _, _ = board_xform
    return a * d - b * c < 0


def mirror_board():
    """
    Turns the board over, as it is turned over to place its bottom side: every
    position mirrored left to right, pads with it, and each angle mirrored too.
    Patterned parts then take their angle from the mirrored pad 1.
    """
    xform_then(-1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    for r in rows:
        for xi in (2, 4, 6):
            try:
                r[xi] = round(clean_zero(-float(r[xi])), 3)
            except (TypeError, ValueError):
                pass
        try:
            r[9] = tidy_angle(180.0 - float(r[9]))
        except (TypeError, ValueError):
            pass
    normalise_rotations()


def _mirror_pt(p):
    return (-p[0], p[1])


def _fit_point(p, design_a, measured_a, theta_deg, scale):
    t = math.radians(theta_deg)
    dx, dy = (p[0] - design_a[0]) * scale, (p[1] - design_a[1]) * scale
    return (measured_a[0] + dx * math.cos(t) - dy * math.sin(t),
            measured_a[1] + dx * math.sin(t) + dy * math.cos(t))


ALIGN_CHECK_TOLERANCE_MM = 0.5
# A check point closer than this to the A-B line cannot tell a flip from a turn.
ALIGN_MIN_CHECK_OFFSET_MM = 5.0


def rigid_fit(design, measured):
    """
    Least-squares rotation and shift taking `design` points onto `measured`
    ones (the 2D form of the Kabsch fit). Returns (theta_deg, scale,
    design_centroid, measured_centroid); scale is reported, not imposed.
    With two points it is the plain two-point fit; with three, each sighting's
    error is shared out instead of all of it landing on one point.
    """
    n = float(len(design))
    pcx = sum(p[0] for p in design) / n
    pcy = sum(p[1] for p in design) / n
    qcx = sum(q[0] for q in measured) / n
    qcy = sum(q[1] for q in measured) / n
    sxx = sxy = spp = 0.0
    for (px, py), (qx, qy) in zip(design, measured):
        dpx, dpy, dqx, dqy = px - pcx, py - pcy, qx - qcx, qy - qcy
        sxx += dpx * dqx + dpy * dqy
        sxy += dpx * dqy - dpy * dqx
        spp += dpx * dpx + dpy * dpy
    theta = math.degrees(math.atan2(sxy, sxx))
    scale = math.hypot(sxx, sxy) / spp if spp else 1.0
    return theta, scale, (pcx, pcy), (qcx, qcy)


def _rigid_apply(p, theta_deg, scale, pc, qc):
    t = math.radians(theta_deg)
    dx, dy = (p[0] - pc[0]) * scale, (p[1] - pc[1]) * scale
    return (qc[0] + dx * math.cos(t) - dy * math.sin(t),
            qc[1] + dx * math.sin(t) + dy * math.cos(t))


def plan_alignment(pa, pb, ma, mb, side="auto", pc=None, mc=None):
    """
    Works out how the board sits on the machine from two sighted parts, and a
    third if given. pa/pb/pc are the parts' current coordinates, ma/mb/mc what
    the camera read.

    Two points fit equally well whether or not the board is turned over -- a
    mirror image of a pair is the same pair -- so which side is up comes from
    the data, or, when a third point is measured, from that measurement, which
    then wins.
    """
    auto_side, why = board_side_auto()
    wanted = auto_side if side == "auto" else side
    mirror_by_data = (wanted == "bottom") != board_is_mirrored()

    three = pc is not None and mc is not None
    off_line = 0.0
    if three:
        ab = math.hypot(pb[0] - pa[0], pb[1] - pa[1]) or 1.0
        off_line = abs((pb[0] - pa[0]) * (pa[1] - pc[1])
                       - (pa[0] - pc[0]) * (pb[1] - pa[1])) / ab

    candidates = {}
    for mirror in (False, True):
        m = _mirror_pt if mirror else (lambda p: p)
        theta, scale, len_d, len_m = compute_two_point_transform(m(pa), ma, m(pb), mb)
        cand = {"theta": theta, "scale": scale, "residual": None,
                "len_d": len_d, "len_m": len_m}
        if three:
            design = [m(pa), m(pb), m(pc)]
            meas = [ma, mb, mc]
            t3, s3, cP, cQ = rigid_fit(design, meas)
            res = [math.hypot(*(a - b for a, b in zip(_rigid_apply(d, t3, 1.0, cP, cQ), q)))
                   for d, q in zip(design, meas)]
            cand.update(theta=t3, scale=s3, residuals=res, residual=res[2],
                        rms=math.sqrt(sum(r * r for r in res) / 3.0),
                        centroids=(cP, cQ))
        candidates[mirror] = cand

    plan = {"auto_side": auto_side, "auto_reason": why, "side_asked": side,
            "candidates": candidates, "fit": "three-point" if three else "two-point",
            "off_line": off_line, "outlier": None}
    if three and off_line < ALIGN_MIN_CHECK_OFFSET_MM:
        # C on the A-B line fits both ways equally: it can still be averaged
        # in, but the side has to come from the data.
        mirror = mirror_by_data
        plan["decided_by"] = ("%s (point C is only %.1fmm off the A-B line, too close "
                              "to tell)" % (why, off_line))
        plan["overrides_data"] = False
        plan["check_failed"] = max(candidates[mirror]["residuals"]) > ALIGN_CHECK_TOLERANCE_MM
    elif three:
        mirror = candidates[True]["rms"] < candidates[False]["rms"]
        plan["decided_by"] = "the third point"
        plan["overrides_data"] = mirror != mirror_by_data
        plan["check_failed"] = max(candidates[mirror]["residuals"]) > ALIGN_CHECK_TOLERANCE_MM
    else:
        mirror = mirror_by_data
        plan["decided_by"] = ("your choice" if side != "auto" else why)
        plan["overrides_data"] = False
        plan["check_failed"] = False
    plan["mirror"] = mirror
    # which side will be up once applied
    plan["side"] = "bottom" if (board_is_mirrored() != mirror) else "top"
    plan.update(candidates[mirror])
    if three:
        # one sighting clearly worse than the other two is probably off its part
        res = sorted(enumerate(plan["residuals"]), key=lambda kv: -kv[1])
        if res[0][1] > 0.08 and res[0][1] > 1.5 * res[1][1]:
            plan["outlier"] = "ABC"[res[0][0]]
    return plan


def apply_alignment(plan, row_a, row_b, ma, mb, use_scale=False, row_c=None, mc=None):
    """
    Turns the board over if the plan says so, then fits it onto the machine --
    through all three points when a third was sighted, so no single sighting
    carries the whole error.
    """
    if plan["mirror"]:
        mirror_board()
    design = [(float(r[2]), float(r[3])) for r in (row_a, row_b)]
    meas = [ma, mb]
    if plan.get("fit") == "three-point" and row_c is not None and mc is not None:
        design.append((float(row_c[2]), float(row_c[3])))
        meas.append(mc)
    theta, scale, pc, qc = rigid_fit(design, meas)
    apply_two_point_transform(pc, qc, theta, scale if use_scale else 1.0)
    return theta, scale


def suggest_check_point(row_a, row_b):
    """
    A third part for the check: as far off the line through A and B as the
    board allows (a point on that line cannot tell a mirror from a turn), and
    easy to recognise under the camera.
    """
    ax, ay = float(row_a[2]), float(row_a[3])
    bx, by = float(row_b[2]), float(row_b[3])
    length = math.hypot(bx - ax, by - ay) or 1.0
    scores = alignment_identifiability(placeable_rows())
    best = None
    for r in placeable_rows():
        if r is row_a or r is row_b:
            continue
        try:
            x, y = float(r[2]), float(r[3])
        except (TypeError, ValueError):
            continue
        off_line = abs((bx - ax) * (ay - y) - (ax - x) * (by - ay)) / length
        ident = min(scores.get(id(r), 0.0), ALIGN_GOOD_ISOLATION_MM) / ALIGN_GOOD_ISOLATION_MM
        score = off_line * (0.35 + 0.65 * ident)
        if best is None or score > best[0]:
            best = (score, off_line, r)
    return (best[2], best[1]) if best else (None, 0.0)


def open_two_point_align():
    """
    Two sighted parts fix where the board is and how it is turned; which side
    is up comes from the Gerber or the layer column, or from an optional third
    sighted part, which settles it by measurement.
    """
    if not rows:
        messagebox.showerror("2-Point Alignment", "Load a Pick & Place file first.")
        return

    suggestions = suggest_alignment_pairs(rows)
    if not suggestions:
        messagebox.showerror(
            "2-Point Alignment",
            "Could not find two components at least %.0fmm apart with valid "
            "coordinates." % ALIGN_MIN_BASELINE_MM)
        return

    state = {"index": 0, "plan": None}

    popup = tk.Toplevel(root)
    popup.title("2-Point Camera Alignment")
    popup.configure(bg=bg_color)
    popup.transient(root)

    outer = tk.Frame(popup, bg=bg_color, padx=15, pady=12)
    outer.pack(fill="both", expand=True)

    tk.Label(outer,
             text="Jog the machine camera onto each part and type in what it reads. Position, "
                  "turn and -- if the board is upside down -- the flip are then applied "
                  "together.",
             bg=bg_color, fg=fg_color, font=("Segoe UI", 9), wraplength=640,
             justify="left").pack(anchor="w", pady=(0, 8))

    # ---- which side is up ----
    side_frame = tk.Frame(outer, bg=bg_color)
    side_frame.pack(fill="x", pady=(0, 6))
    create_label(side_frame, "Side facing up on the machine:").pack(side="left")
    side_var = tk.StringVar(value="Auto")
    ttk.Combobox(side_frame, textvariable=side_var, values=["Auto", "Top", "Bottom"],
                 state="readonly", width=8).pack(side="left", padx=6)
    side_note = tk.Label(side_frame, text="", bg=bg_color, fg="#5ce65c",
                         font=("Segoe UI", 9), anchor="w")
    side_note.pack(side="left", padx=6)

    def refresh_side_note(*_):
        auto, why = board_side_auto()
        if side_var.get() == "Auto":
            side_note.config(text="%s -- %s" % (auto.upper(), why), fg="#5ce65c")
        else:
            side_note.config(text="set by hand (data says %s)" % auto.upper(), fg="#ff9900")
        state["plan"] = None

    side_var.trace_add("write", refresh_side_note)

    panels = {}

    def make_panel(slot, title, optional=False):
        lf = tk.LabelFrame(outer, text=title, bg=bg_color,
                           fg="#c8a2ff" if optional else frame_title_color,
                           font=("Segoe UI", 9, "bold"), padx=10, pady=6)
        lf.pack(fill="x", pady=3)
        who = tk.Label(lf, text="", bg=bg_color, fg="#5ce65c", font=("Segoe UI", 10, "bold"))
        who.grid(row=0, column=0, columnspan=4, sticky="w")
        detail = tk.Label(lf, text="", bg=bg_color, fg=fg_color, font=("Segoe UI", 9))
        detail.grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 4))
        create_label(lf, "Measured X:").grid(row=2, column=0, sticky="e", padx=4)
        mx = tk.StringVar()
        create_entry(lf, width=12, var=mx).grid(row=2, column=1, padx=4)
        create_label(lf, "Measured Y:").grid(row=2, column=2, sticky="e", padx=4)
        my = tk.StringVar()
        create_entry(lf, width=12, var=my).grid(row=2, column=3, padx=4)
        panels[slot] = {"who": who, "detail": detail, "mx": mx, "my": my}

    make_panel("A", " Point A ")
    make_panel("B", " Point B ")
    make_panel("C", " Point C -- optional: settles the side by measurement, and checks the "
                    "other two ", optional=True)

    result_label = tk.Label(outer, text="", bg=bg_color, fg=fg_color,
                            font=("Consolas", 9), justify="left")
    result_label.pack(anchor="w", pady=(8, 0))

    def show_pair():
        baseline, isolation, a, b = suggestions[state["index"]]
        c, off = suggest_check_point(a, b)
        state.update(a=a, b=b, c=c, plan=None)
        for slot, r in (("A", a), ("B", b), ("C", c)):
            p = panels[slot]
            if r is None:
                p["who"].config(text="(no suitable part)")
                p["detail"].config(text="")
                continue
            p["who"].config(text=str(r[0]))
            p["detail"].config(text="%s  |  %s  |  now at X %s  Y %s"
                                    % (r[10] or "-", r[1] or "-", _mm(r[2]), _mm(r[3])))
        note = ("only part of its kind" if isolation == float("inf")
                else "nearest identical part %.1fmm away" % isolation)
        result_label.config(
            text="Pair %d of %d  |  A-B %.1fmm  |  %s  |  C sits %.1fmm off the A-B line"
                 % (state["index"] + 1, len(suggestions), baseline, note, off), fg=fg_color)

    def next_pair():
        state["index"] = (state["index"] + 1) % len(suggestions)
        for slot in "ABC":
            panels[slot]["mx"].set("")
            panels[slot]["my"].set("")
        show_pair()

    def read(slot, required=True):
        mx, my = panels[slot]["mx"].get().strip(), panels[slot]["my"].get().strip()
        if not mx and not my and not required:
            return None
        try:
            return float(mx), float(my)
        except ValueError:
            raise ValueError(slot)

    def compute():
        try:
            ma, mb = read("A"), read("B")
            mc = read("C", required=False)
        except ValueError as e:
            messagebox.showerror("2-Point Alignment",
                                 "Point %s needs numeric X and Y." % e.args[0])
            return None
        a, b, c = state["a"], state["b"], state["c"]
        pa = (float(a[2]), float(a[3]))
        pb = (float(b[2]), float(b[3]))
        pc = (float(c[2]), float(c[3])) if (c is not None and mc is not None) else None
        try:
            plan = plan_alignment(pa, pb, ma, mb, side_var.get().lower(), pc, mc)
        except ValueError as e:
            messagebox.showerror("2-Point Alignment", str(e))
            return None
        state.update(plan=plan, ma=ma, mb=mb, mc=mc)

        lines = [
            "Side up       %s%s" % (plan["side"].upper(),
                                    "   (board will be turned over)" if plan["mirror"] else ""),
            "decided by    %s" % plan["decided_by"],
            "Rotation      %+8.3f deg" % plan["theta"],
            "Scale         %8.5f   (A-B %.3fmm in the design, %.3fmm measured)"
            % (plan["scale"], plan["len_d"], plan["len_m"]),
        ]
        bad = abs(plan["scale"] - 1.0) > ALIGN_SCALE_TOLERANCE
        if pc is not None:
            cand = plan["candidates"]
            lines.insert(2, "Fit           all three points (best fit, average error %.3fmm)"
                         % plan["rms"])
            lines.append("Leftover      A %.3f   B %.3f   C %.3f mm"
                         % tuple(plan["residuals"]))
            lines.append("Other way up  %.3fmm average -- %s"
                         % (cand[not plan["mirror"]]["rms"],
                            "ruled out" if cand[not plan["mirror"]]["rms"] > 1.0
                            else "close; check the side"))
            if plan["outlier"]:
                who = {"A": state["a"], "B": state["b"], "C": state["c"]}[plan["outlier"]]
                lines.append("              -> %s (point %s) is the odd one out; it was "
                             "probably sighted a little off its centre"
                             % (who[0], plan["outlier"]))
            if plan["overrides_data"]:
                lines.append("              -> the measurement disagrees with the data; "
                             "going by the measurement")
            if plan["check_failed"]:
                lines.append("WARNING: neither way puts C within %.1fmm -- one of the three "
                             "was sighted on the wrong part." % ALIGN_CHECK_TOLERANCE_MM)
                bad = True
        if abs(plan["scale"] - 1.0) > ALIGN_SCALE_TOLERANCE:
            lines.append("WARNING: scale off by %.2f%% -- A or B was sighted on the wrong "
                         "part." % (abs(plan["scale"] - 1.0) * 100))
        result_label.config(text="\n".join(lines), fg="#ff6666" if bad else "#5ce65c")
        return plan

    scale_var = tk.BooleanVar(value=False)

    def apply_now():
        plan = state["plan"] or compute()
        if plan is None:
            return
        summary = ("%sRotate by %+.3f deg and place point A at X %.3f, Y %.3f.\n\n"
                   "Apply to all %d components?"
                   % ("Turn the board over (%s side up), then:\n" % plan["side"]
                      if plan["mirror"] else "",
                      plan["theta"], state["ma"][0], state["ma"][1], len(rows)))
        if not messagebox.askyesno("Apply Alignment", summary):
            return
        theta, _ = apply_alignment(plan, state["a"], state["b"], state["ma"], state["mb"],
                                   use_scale=scale_var.get(), row_c=state["c"],
                                   mc=state.get("mc"))
        popup.destroy()
        msg = ["Applied: %s side up, turned %+.3f deg." % (plan["side"], theta)]
        if plan["mirror"]:
            msg.append("The board was turned over: positions, pads and angles are mirrored, "
                       "and patterned parts took their angle from the mirrored pad 1.")
        b = state["b"]
        msg.append("Jog the camera back to %s -- it should read X %.3f, Y %.3f."
                   % (b[0], float(b[2]), float(b[3])))
        messagebox.showinfo("2-Point Alignment", "\n\n".join(msg))

    opts = tk.Frame(outer, bg=bg_color)
    opts.pack(fill="x", pady=(8, 0))
    tk.Checkbutton(opts, text="Also apply scale correction (leave off unless the board is "
                              "genuinely a different size)",
                   variable=scale_var, bg=bg_color, fg=fg_color, selectcolor=tree_bg,
                   activebackground=bg_color, activeforeground=fg_color,
                   font=("Segoe UI", 8)).pack(anchor="w")

    btns = tk.Frame(outer, bg=bg_color)
    btns.pack(fill="x", pady=(10, 0))
    create_btn(btns, "Suggest Another Set", next_pair).pack(side="left")
    tk.Button(btns, text="Apply to Board", command=apply_now, bg=btn_bg, fg=btn_fg,
              width=16, font=("Segoe UI", 9, "bold")).pack(side="right")
    tk.Button(btns, text="Compute", command=compute, bg=entry_bg, fg=fg_color,
              width=12, font=("Segoe UI", 9)).pack(side="right", padx=8)

    refresh_side_note()
    show_pair()


# =====================================
# GUI Optimization
# =====================================

root = tk.Tk()
root.title("KFG TECH Pick & Place tool")
root.geometry("1400x850") # Slightly optimized default size

# ---------- Modern Dark Theme Colors
bg_color = "#1e1e1e"
fg_color = "#d4d4d4"
btn_bg = "#0e639c"
btn_fg = "#ffffff"
entry_bg = "#3c3c3c"
entry_fg = "#ffffff"
tree_bg = "#252526"
tree_fg = "#d4d4d4"
tree_sel_bg = "#094771"
tree_sel_fg = "#ffffff"
frame_title_color = "#4fc1ff"

root.configure(bg=bg_color)
font_default = ("Segoe UI", 10)

# ---------- Style
style = ttk.Style()
style.theme_use("default")
style.configure(
    "Treeview",
    rowheight=26,
    bordercolor="#3e3e42",
    borderwidth=1,
    background=tree_bg,
    foreground=tree_fg,
    fieldbackground=tree_bg,
    font=font_default
)
style.configure(
    "Treeview.Heading",
    background="#333333",
    foreground=fg_color,
    font=("Segoe UI", 10, "bold"),
    relief="flat"
)
style.map('Treeview', background=[('selected', tree_sel_bg)], foreground=[('selected', tree_sel_fg)])

# استایل برای تب‌ها (Notebook)
style.configure(
    "TNotebook",
    background=bg_color,
    borderwidth=0
)
style.configure(
    "TNotebook.Tab",
    background="#333333",
    foreground=fg_color,
    padding=[15, 5],
    font=("Segoe UI", 10, "bold")
)
style.map("TNotebook.Tab", background=[("selected", btn_bg)], foreground=[("selected", btn_fg)])


# Common UI component creators for cleanliness
def create_entry(parent, width=10, var=None):
    return tk.Entry(parent, width=width, bg=entry_bg, fg=entry_fg, insertbackground=fg_color, font=font_default, textvariable=var, relief="solid", bd=1)

def create_btn(parent, text, cmd, width=None):
    return tk.Button(parent, text=text, bg=btn_bg, fg=btn_fg, activebackground=tree_sel_bg, activeforeground=tree_sel_fg, command=cmd, font=font_default, relief="flat", padx=10, pady=2, width=width)

def create_label(parent, text):
    return tk.Label(parent, text=text, bg=bg_color, fg=fg_color, font=font_default)


# =====================================
# Tooltips: hover over any button or option to see what it does
# =====================================
TOOLTIPS = {
    # Pick & Place Tool tab
    "Load CSV": "Open an Altium Pick & Place file. Part names are made upper-case and "
                "footprint names cleaned (e.g. J1-0603 -> 0603).",
    "Export CSV": "Save the Pick & Place list, with every edit, as a CSV file.",
    "Delete Row": "Delete the selected line(s) from the Pick & Place list.",
    "Component Heights": "Height of every part, used to place low parts first. Set a "
                         "height for a part whose footprint is wrong.",
    "Rename Part": "Give the selected part (value) a new name on every line that uses it.",
    "Del Part": "Delete every line of the selected part (value).",
    "Del Footprint": "Delete every line that uses the selected footprint.",
    "Find Similar Names": "Find parts written differently that are the same part "
                          "(e.g. 100nF / 100NF / 0.1uF) and merge them.",
    "Add FIDUCIAL": "Add the fiducial line at the X / Y typed above. It always goes first "
                    "in the machine program, on feeder 49, placement skipped.",
    "1-Point (FID)": "Shift the whole board so the fiducial lands on the machine "
                     "position typed above.",
    "2-Point Align...": "Align the board from two or three parts sighted on the machine: "
                        "works out offset, rotation and which side is up.",
    "Flip X": "Mirror the board left-right (for the bottom side). Angles follow the pads.",
    "Flip Y": "Mirror the board top-bottom. Angles follow the pads.",
    "+90°": "Turn the whole board 90° counter-clockwise about its centre. "
            "Fiducials stay where they are.",
    "-90°": "Turn the whole board 90° clockwise about its centre. "
            "Fiducials stay where they are.",
    "180°": "Turn the whole board 180° about its centre. Fiducials stay where they are.",
    # PCB 2D MAP tab
    "Set": "Apply the value typed next to this button.",
    "-90": "Turn the selected part 90° clockwise.",
    "+90": "Turn the selected part 90° counter-clockwise.",
    "180": "Turn the selected part 180°.",
    "Turn every part like it": "When ticked, turning one part turns every part with "
                               "the same footprint and value the same way.",
    "Fit View": "Zoom and pan so the whole board fits the window.",
    "Load Gerber...": "Load the board's Gerber files and show copper / silkscreen under "
                      "the parts, to check positions and pad 1.",
    "Clear": "Remove the Gerber underlay.",
    # Machine File tab
    "Import Machine File": "Open a machine file (e.g. one the machine wrote) to view, "
                           "edit and export it unchanged apart from your edits.",
    "Export Machine File": "Save the whole machine program as one file. For a job split "
                           "into stages use Export Stages.",
    "Export Stages": "Write one machine file per stage (name-1.csv, name-2.csv ...): each "
                     "stage fits the 8mm feeders you have; stage 1 places the most.",
    "Package Heights": "Edit the height of each package (footprint), used to place low "
                       "parts first.",
    "Build From Pick & Place": "Make a machine program from the Pick & Place list: "
                               "feeders by the layout rules, fiducial first.",
    "Optimize for Speed": "Shorten head travel: re-order placements, suggest which "
                          "feeder each reel goes on, or both.",
    "Edit Feeder": "Edit every setting of the selected feeder.",
    "Add Feeder": "Add a new, empty feeder row.",
    "Group Edit": "Set one setting (place height, delays, speed, nozzles ...) on many "
                  "feeders at once.",
    "Arrange Feeders": "Lay the feeders out by the rules: busiest 8mm parts in the middle "
                       "of the bank, big parts from 20, fiducial on 49. Placement order "
                       "is kept.",
    "Take Layout From Machine File": "Make the project match what is loaded on the "
                                     "machine: feeders, fiducial, board / mark / panel and "
                                     "nozzles from a file the machine wrote.",
    "Auto Sort": "Sort the placements: low parts first.",
    "Top": "Move the selected placements to the top (after the fiducial).",
    "Up": "Move the selected placements up one.",
    "Down": "Move the selected placements down one.",
    "Bottom": "Move the selected placements to the bottom.",
    "Assign Feeder": "Put the selected placements on another feeder.",
    "Toggle Skip": "Skip / un-skip the selected placements on the machine.",
    "Edit Record": "Edit the selected board, mark or panel record.",
    "Export Parts List": "Save this list -- every part, per board and for the boards "
                         "entered, with spare -- as a CSV for picking the reels.",
    "Top side": "Count the parts on the top of the board.",
    "Bottom side": "Count the parts on the bottom of the board.",
    # dialogs
    "Save / Apply": "Save the changes and close.",
    "Save": "Save and close.",
    "Cancel": "Close without saving.",
    "Delete": "Delete this item.",
    "Apply": "Apply to the chosen items.",
    "OK": "Keep these values.",
    "Close": "Close this window.",
    "Edit": "Edit the selected line.",
    "Remove": "Remove the selected line(s).",
    "Use Footprint Height": "Forget this part's own height and use its footprint's.",
    "Set Height": "Give the selected footprint the height typed.",
    "Recalculate": "Work the place heights out again with the surface and press typed.",
    "Apply Place Heights to Feeders": "Set every feeder's Place Height to: nozzle-touches-"
                                      "PCB height + part height, less the press. Parts with "
                                      "no known height are left as they are.",
    "Apply to Package": "Use this height for every part with this package.",
    "Merge All Groups": "Merge every group of look-alike names into one name each.",
    "Merge Selected": "Merge only the selected group.",
    "Leave As Is": "Close without merging.",
    "Suggest Another Set": "Pick a different pair (or three) of parts to sight.",
    "Apply to Board": "Move and turn the board by the alignment worked out.",
    "Compute": "Work the alignment out from the positions typed.",
    "Re-assign Feeders": "Assign feeders again by the layout rules.",
    "Edit Selected": "Edit the selected part's feeder, heights, speed and nozzles.",
    "Build": "Build the machine program and show it in the Machine File tab.",
    "Top layer only": "Leave out parts on the bottom layer.",
    "Keep board / mark / mirror rows from the loaded machine file":
        "Keep the PCB, mark and panel set-up of the machine file loaded now.",
    "Learn from machine file...": "Read each slot's pick X / Y, angle, feeder power and "
                                  "peeler power from a file the machine wrote.",
    "Apply slots to the open program": "Also put the slot values into the program open now.",
    "Compute Plan": "Work out the speed plan without changing anything.",
    "Apply Plan": "Apply the plan in the mode chosen on the left.",
    "Order only (feeders stay)": "Re-order the placements for speed; no reel moves.",
    "Feeders only (order stays)": "Suggest which feeder each reel goes on; the placement "
                                  "order stays.",
    "Both": "Move reels as suggested and re-order the placements.",
    "Use the slots that look untaught": "Let the plan use slots whose positions look "
                                        "copied or never taught.",
    "All 8mm feeders": "Every feeder of the 8mm bank or holding an 8mm part.",
    "All feeders except the fiducial": "Every feeder but the fiducial's.",
}
for _n in "1234":
    TOOLTIPS[_n] = "Nozzle %s: parts on this feeder may be picked with nozzle %s." % (_n, _n)

# same text, different job: (window title starts with, button text) -> tip
TOOLTIPS_IN = {

    ("Optimize for Speed", "Set"): "Set the tape width of the selected slots.",
    ("Component Heights", "Set"): "Give the selected part the height typed.",
    ("KFG", "Set"): "Turn the selected part to the angle typed.",
}
TOOLTIP_PREFIXES = {
    "Selected feeders": "Only the feeders selected in the table.",
    "Also apply scale correction": "Also stretch / shrink the board to fit the sightings. "
                                   "Leave off unless the board really is a different size.",
}
_tip = {"win": None, "job": None}


def tooltip_for(widget):
    try:
        text = str(widget.cget("text")).strip()
        title = widget.winfo_toplevel().title()
    except tk.TclError:
        return None
    for (where, label), tip in TOOLTIPS_IN.items():
        if label == text and title.startswith(where):
            return tip
    if text in TOOLTIPS:
        return TOOLTIPS[text]
    return next((tip for p, tip in TOOLTIP_PREFIXES.items() if text.startswith(p)), None)


def _tip_hide(event=None):
    if _tip["job"] is not None:
        try:
            root.after_cancel(_tip["job"])
        except tk.TclError:
            pass
        _tip["job"] = None
    if _tip["win"] is not None:
        try:
            _tip["win"].destroy()
        except tk.TclError:
            pass
        _tip["win"] = None


def _tip_show(widget):
    _tip["job"] = None
    text = tooltip_for(widget)
    if not text:
        return
    try:
        x, y = widget.winfo_pointerx() + 14, widget.winfo_pointery() + 18
    except tk.TclError:
        return
    win = tk.Toplevel(root)
    win.wm_overrideredirect(True)
    win.wm_attributes("-topmost", True)
    tk.Label(win, text=text, justify="left", wraplength=380, bg="#ffffe0", fg="#000000",
             relief="solid", borderwidth=1, font=("Segoe UI", 9), padx=6, pady=4).pack()
    win.wm_geometry("+%d+%d" % (x, y))
    _tip["win"] = win


def _tip_enter(event):
    _tip_hide()
    _tip["job"] = root.after(450, lambda w=event.widget: _tip_show(w))


# One pop-up at a time: a window that opens takes the input until it closes
# (the one under it gets it back), so a second pop-up cannot be opened
# beside it and nothing behind it can be clicked by mistake.
_dialogs = []


def _is_popup(w):
    try:
        return (isinstance(w, tk.Toplevel) and w is not root
                and not w.wm_overrideredirect())
    except tk.TclError:
        return False


def _popup_opened(event):
    w = event.widget
    if not _is_popup(w) or w in _dialogs:
        return
    _dialogs.append(w)
    try:
        w.transient(_dialogs[-2] if len(_dialogs) > 1 else root)
        w.grab_set()
        w.focus_force()
    except tk.TclError:
        pass


def _popup_closed(event):
    w = event.widget
    if w in _dialogs:
        _dialogs.remove(w)
        if _dialogs:
            try:
                _dialogs[-1].grab_set()
                _dialogs[-1].focus_force()
            except tk.TclError:
                pass


root.bind_class("Toplevel", "<Map>", _popup_opened, add="+")
root.bind_class("Toplevel", "<Destroy>", _popup_closed, add="+")

for _cls in ("Button", "Checkbutton", "Radiobutton"):
    root.bind_class(_cls, "<Enter>", _tip_enter, add="+")
    root.bind_class(_cls, "<Leave>", _tip_hide, add="+")
    root.bind_class(_cls, "<ButtonPress>", _tip_hide, add="+")


# =====================================
# TABS SETUP (Notebook)
# =====================================
notebook = ttk.Notebook(root)
notebook.pack(fill="both", expand=True)

tab1 = tk.Frame(notebook, bg=bg_color)
notebook.add(tab1, text="Pick & Place Tool")

tab2 = tk.Frame(notebook, bg=bg_color)
notebook.add(tab2, text="PCB 2D MAP")

# Initialize UI elements in tab2
map_panel = tk.Frame(tab2, bg=bg_color)
map_panel.pack(side=tk.BOTTOM, fill="x", padx=20, pady=(0, 12))

map_sel_label = tk.Label(map_panel, text="Click a part to select it.", bg=bg_color,
                         fg="#ffcc00", font=("Segoe UI", 10, "bold"), anchor="w")
map_sel_label.pack(side="left", padx=(0, 18))

create_label(map_panel, "Angle:").pack(side="left")
map_angle_var = tk.StringVar()
map_angle_entry = create_entry(map_panel, width=7, var=map_angle_var)
map_angle_entry.pack(side="left", padx=(4, 4))
map_angle_entry.bind("<Return>", lambda e: _map_apply_typed())
create_btn(map_panel, "Set", lambda: _map_apply_typed()).pack(side="left", padx=(0, 12))
create_btn(map_panel, "-90", lambda: _map_rotate(-90), width=4).pack(side="left", padx=2)
create_btn(map_panel, "+90", lambda: _map_rotate(90), width=4).pack(side="left", padx=2)
create_btn(map_panel, "180", lambda: _map_rotate(180), width=4).pack(side="left", padx=2)

map_all_var = tk.BooleanVar(value=False)
map_all_check = tk.Checkbutton(map_panel, text="Turn every part like it",
                               variable=map_all_var, bg=bg_color, fg=fg_color,
                               selectcolor=tree_bg, activebackground=bg_color,
                               activeforeground=fg_color, font=("Segoe UI", 9))
map_all_check.pack(side="left", padx=(14, 0))
create_btn(map_panel, "Fit View", lambda: _map_reset_view()).pack(side="right")

map_status = tk.Label(tab2, text="", bg=bg_color, fg=fg_color,
                      font=("Segoe UI", 9, "italic"), anchor="w")
map_status.pack(side=tk.BOTTOM, fill="x", padx=20)

gerber_panel = tk.Frame(tab2, bg=bg_color, width=210)
gerber_panel.pack(side=tk.RIGHT, fill="y", padx=(0, 16), pady=(16, 6))
tk.Label(gerber_panel, text="Gerber", bg=bg_color, fg=frame_title_color,
         font=("Segoe UI", 10, "bold"), anchor="w").pack(fill="x")
gerber_btns = tk.Frame(gerber_panel, bg=bg_color)
gerber_btns.pack(fill="x", pady=(4, 6))
create_btn(gerber_btns, "Load Gerber...", lambda: load_gerber_dialog()).pack(side="left")
create_btn(gerber_btns, "Clear", lambda: clear_gerber()).pack(side="left", padx=(6, 0))
gerber_info = tk.Label(gerber_panel, text="No Gerber loaded.", bg=bg_color, fg=fg_color,
                       font=("Segoe UI", 9), anchor="w", justify="left", wraplength=200)
gerber_info.pack(fill="x", pady=(0, 6))
gerber_layer_box = tk.Frame(gerber_panel, bg=bg_color)
gerber_layer_box.pack(fill="both", expand=True)

board_canvas = tk.Canvas(tab2, bg="#0f1418", highlightthickness=0)
board_canvas.pack(fill=tk.BOTH, expand=True, padx=(20, 10), pady=(16, 6))
board_canvas.bind("<MouseWheel>", lambda e: _map_zoom(e))
board_canvas.bind("<Button-4>", lambda e: _map_zoom(e))
board_canvas.bind("<Button-5>", lambda e: _map_zoom(e))
board_canvas.bind("<ButtonPress-3>", lambda e: _map_pan_start(e))
board_canvas.bind("<B3-Motion>", lambda e: _map_pan_move(e))
board_canvas.bind("<ButtonRelease-3>", lambda e: _map_pan_end(e))
board_canvas.tag_bind("comp", "<Enter>", lambda e: _map_on_enter(e))
board_canvas.tag_bind("comp", "<Leave>", lambda e: _map_unhover(e))
board_canvas.tag_bind("comp", "<Button-1>", lambda e: _map_on_click(e))
board_canvas.tag_bind("comp", "<Double-1>", lambda e: _map_on_double(e))
board_canvas.bind("<KeyPress-r>", lambda e: _map_rotate(90))
board_canvas.bind("<Delete>", lambda e: _map_delete_selected(e))
board_canvas.bind("<KeyPress-R>", lambda e: _map_rotate(-90))
board_canvas.bind("<Configure>", lambda e: render_pcb_preview() if rows else None)

# ---------- TOP SECTION (Controls) ----------
# توجه: parent این فریم‌ها به tab1 تغییر کرده است
top_frame = tk.Frame(tab1, bg=bg_color)
top_frame.pack(fill="x", pady=10, padx=10)

# 1. Board Settings Group
board_lf = tk.LabelFrame(top_frame, text=" Board Dimensions & Origin ", bg=bg_color, fg=frame_title_color, font=("Segoe UI", 10, "bold"), padx=10, pady=5)
board_lf.pack(side="left", fill="y", padx=5)

create_label(board_lf, "Width (mm):").grid(row=0, column=0, padx=5, pady=5, sticky="e")
board_width = create_entry(board_lf)
board_width.grid(row=0, column=1, padx=5, pady=5)

create_label(board_lf, "Height (mm):").grid(row=0, column=2, padx=5, pady=5, sticky="e")
board_height = create_entry(board_lf)
board_height.grid(row=0, column=3, padx=5, pady=5)

create_label(board_lf, "KeepOut (mm):").grid(row=1, column=0, padx=5, pady=5, sticky="e")
board_KeepOutwidth = create_entry(board_lf)
board_KeepOutwidth.grid(row=1, column=1, padx=5, pady=5)

create_label(board_lf, "Rotation:").grid(row=1, column=2, padx=5, pady=5, sticky="e")
board_rotation_var = tk.StringVar(value="0")
board_rotation = create_entry(board_lf, var=board_rotation_var)
board_rotation.grid(row=1, column=3, padx=5, pady=5)
board_rotation_var.trace_add("write", on_board_rotation_change)

# 2. Offset & Operations Group
ops_lf = tk.LabelFrame(top_frame, text=" Alignment & Mirror ", bg=bg_color, fg=frame_title_color, font=("Segoe UI", 10, "bold"), padx=10, pady=5)
ops_lf.pack(side="left", fill="y", padx=5)

create_label(ops_lf, "Offset X:").grid(row=0, column=0, padx=5, pady=5, sticky="e")
board_offsetX = create_entry(ops_lf, width=8)
board_offsetX.grid(row=0, column=1, padx=5, pady=5)

create_label(ops_lf, "Offset Y:").grid(row=0, column=2, padx=5, pady=5, sticky="e")
board_offsety = create_entry(ops_lf, width=8)
board_offsety.grid(row=0, column=3, padx=5, pady=5)

create_btn(ops_lf, "1-Point (FID)", ApplyOffset).grid(row=0, column=4, padx=10, pady=5)
create_btn(ops_lf, "2-Point Align...", open_two_point_align).grid(row=1, column=4, padx=10, pady=5)

create_btn(ops_lf, "Flip X", flip_x).grid(row=1, column=0, columnspan=2, pady=5, sticky="ew", padx=5)
create_btn(ops_lf, "Flip Y", flip_y).grid(row=1, column=2, columnspan=2, pady=5, sticky="ew", padx=5)
_rot_row = tk.Frame(ops_lf, bg=bg_color)
_rot_row.grid(row=2, column=0, columnspan=5, sticky="ew", pady=(0, 5))
tk.Label(_rot_row, text="Turn board (fiducials stay):", bg=bg_color, fg=fg_color,
         font=("Segoe UI", 9)).pack(side="left", padx=5)
for _label, _deg in (("+90°", 90), ("-90°", -90), ("180°", 180)):
    create_btn(_rot_row, _label, lambda d=_deg: rotate_board_quarter(d)).pack(side="left", padx=4)

# 3. File & Display Group
file_lf = tk.LabelFrame(top_frame, text=" File & Table Actions ", bg=bg_color, fg=frame_title_color, font=("Segoe UI", 10, "bold"), padx=10, pady=5)
file_lf.pack(side="left", fill="y", padx=5, expand=True)

# Top row of File frame
f_top = tk.Frame(file_lf, bg=bg_color)
f_top.pack(fill="x", pady=2)
create_btn(f_top, "Load CSV", load_csv, width=12).pack(side="left", padx=5)
create_btn(f_top, "Export CSV", export_csv, width=12).pack(side="left", padx=5)
create_btn(f_top, "Delete Row", delete_selected_row, width=12).pack(side="right", padx=5)

# Bottom row of File frame
f_bot = tk.Frame(file_lf, bg=bg_color)
f_bot.pack(fill="x", pady=2, side="bottom")
create_label(f_bot, "Sort By:").pack(side="left", padx=5)
sort_model = tk.StringVar(value="No Sort")
sort_selector = ttk.Combobox(
    f_bot, textvariable=sort_model, state="readonly", width=35,
    values=[
        "No Sort","Auto (shortest parts first)","Pythagoras (X,Y) incremental", "Center-X incremental",
        "Center-Y incremental", "Designator incremental", "Comment incremental",
        "Pythagoras (X,Y) decrease", "Center-X decrease", "Center-Y decrease",
        "Designator decrease", "Comment decrease"
    ]
)
sort_selector.pack(side="left", padx=5)
sort_selector.bind("<<ComboboxSelected>>", lambda e: refresh_table())
create_btn(f_bot, "Component Heights", open_height_editor).pack(side="left", padx=8)

# ---------- MIDDLE SECTION (Main Table with Scrollbars) ----------
# توجه: parent این فریم به tab1 تغییر کرده است
table_frame = tk.Frame(tab1, bg=bg_color)
table_frame.pack(fill="both", expand=True, padx=10, pady=5)

tree_scroll_y = ttk.Scrollbar(table_frame, orient="vertical")
tree_scroll_y.pack(side="right", fill="y")
tree_scroll_x = ttk.Scrollbar(table_frame, orient="horizontal")
tree_scroll_x.pack(side="bottom", fill="x")

tree = ttk.Treeview(table_frame, columns=columns, show="headings",
                    yscrollcommand=tree_scroll_y.set, xscrollcommand=tree_scroll_x.set)
tree_scroll_y.config(command=tree.yview)
tree_scroll_x.config(command=tree.xview)

for col in columns:
    tree.heading(col, text=col)
    tree.column(col, width=110, anchor="w")
tree.pack(fill="both", expand=True)

# ---------- BOTTOM SECTION ----------
# توجه: parent این فریم به tab1 تغییر کرده است
bottom_frame = tk.Frame(tab1, bg=bg_color)
bottom_frame.pack(fill="x", pady=10, padx=10)

# Variant & Footprint Tables
def create_sub_tree(parent, cols, widths):
    f = tk.Frame(parent, bg=bg_color)
    scroll = ttk.Scrollbar(f, orient="vertical")
    scroll.pack(side="right", fill="y")
    tv = ttk.Treeview(f, columns=cols, show="headings", height=6, yscrollcommand=scroll.set)
    scroll.config(command=tv.yview)
    for c, w in zip(cols, widths):
        tv.heading(c, text=c)
        tv.column(c, width=w, anchor="w")
    tv.pack(side="left", fill="both", expand=True)
    return f, tv

# =========================================================
# Gerber (RS-274X) reading, for the 2D map underlay
# =========================================================
#
# Everything is turned into two kinds of filled shape in millimetres, top
# view -- circles and polygons -- because those are what a Tk canvas can
# stretch when zooming without losing their proportions. A stroked track
# becomes a capsule polygon; an arc becomes a band; an aperture flash becomes
# its outline at the flash point.
#
# Supported: FS/MO, apertures C R O P and macros (primitives 1, 4, 5, 20, 21,
# 22, 7), D01/D02/D03, G01/G02/G03, G74/G75, regions (G36/G37), LP polarity,
# SR step-and-repeat, absolute and incremental coordinates. Clear polarity is
# drawn in the background colour -- right for cut-outs drawn over their own
# layer, an approximation where layers overlap.

import zipfile

GERBER_LAYER_TYPES = [
    # (extension, key, label, side, kind)
    ("GTL", "top_copper", "Top Copper", "top", "copper"),
    ("GBL", "bottom_copper", "Bottom Copper", "bottom", "copper"),
    ("GTO", "top_silk", "Top Silkscreen", "top", "silk"),
    ("GBO", "bottom_silk", "Bottom Silkscreen", "bottom", "silk"),
    ("GTP", "top_paste", "Top Paste", "top", "paste"),
    ("GBP", "bottom_paste", "Bottom Paste", "bottom", "paste"),
    ("GTS", "top_mask", "Top Solder Mask", "top", "mask"),
    ("GBS", "bottom_mask", "Bottom Solder Mask", "bottom", "mask"),
    ("GPT", "top_padmaster", "Top Pad Master", "top", "pads"),
    ("GPB", "bottom_padmaster", "Bottom Pad Master", "bottom", "pads"),
    ("GKO", "keepout", "Keep-Out", "both", "outline"),
]

GERBER_COLORS = {
    "top_copper": "#c8792e",
    "bottom_copper": "#4f8fd6",
    "top_silk": "#f2f2f2",
    "bottom_silk": "#e6d48a",
    "top_paste": "#9aa3ab",
    "bottom_paste": "#9aa3ab",
    "top_mask": "#2f7a4a",
    "bottom_mask": "#2f7a4a",
    "top_padmaster": "#b07d4f",
    "bottom_padmaster": "#5b86b8",
    "keepout": "#e0c050",
    "mechanical": "#9a7fd1",
    "other": "#8d99a6",
}

_IN_TO_MM = 25.4


def identify_gerber_layer(filename, text=""):
    """(key, label, side, kind) for a Gerber file, from its name or attributes."""
    ext = os.path.splitext(filename)[1].lstrip(".").upper()
    for e, key, label, side, kind in GERBER_LAYER_TYPES:
        if ext == e:
            return key, label, side, kind
    m = re.match(r"GM(\d+)$", ext)
    if m or ext == "GM":
        n = m.group(1) if m else ""
        return "mech%s" % n, "Mechanical %s" % n if n else "Mechanical", "both", "mechanical"
    m = re.match(r"G(\d+)$", ext)
    if m:
        return "inner%s" % m.group(1), "Inner Copper %s" % m.group(1), "inner", "copper"
    # X2 attribute, for files without Protel-style extensions
    f = re.search(r"%TF\.FileFunction,([^*]+)\*%", text)
    if f:
        parts = [p.strip().lower() for p in f.group(1).split(",")]
        side = "top" if "top" in parts else ("bottom" if "bot" in parts or "bottom" in parts else "both")
        kind = {"copper": "copper", "legend": "silk", "soldermask": "mask",
                "paste": "paste", "profile": "outline"}.get(parts[0], "other")
        return ("%s_%s" % (side, kind), "%s %s" % (side.title(), parts[0].title()), side, kind)
    return None


def _is_gerber_text(text):
    return "%FS" in text[:4000] or ("%MO" in text[:4000] and "D0" in text)


class GerberError(Exception):
    pass


def _eval_macro_expr(expr, variables):
    """A macro parameter: numbers, $n, + - x / and brackets."""
    s = expr.strip().replace("X", "*").replace("x", "*")
    s = re.sub(r"\$(\d+)", lambda m: repr(variables.get(int(m.group(1)), 0.0)), s)
    if not re.fullmatch(r"[0-9.+\-*/() eE]*", s):
        raise GerberError("unsupported macro expression: %s" % expr)
    try:
        return float(eval(s, {"__builtins__": {}}, {})) if s.strip() else 0.0
    except Exception:
        raise GerberError("bad macro expression: %s" % expr)


def _rot(x, y, deg):
    if not deg:
        return x, y
    t = math.radians(deg)
    return x * math.cos(t) - y * math.sin(t), x * math.sin(t) + y * math.cos(t)


def _circle_poly(cx, cy, r, n=16):
    return [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n))
            for k in range(n)]


def _capsule(x1, y1, x2, y2, r, n=6):
    """Outline of a round-ended stroke from (x1,y1) to (x2,y2), half-width r."""
    a = math.atan2(y2 - y1, x2 - x1)
    pts = []
    for k in range(n + 1):
        t = a + math.pi / 2 + math.pi * k / n
        pts.append((x2 + r * math.cos(t - math.pi), y2 + r * math.sin(t - math.pi)))
    for k in range(n + 1):
        t = a - math.pi / 2 + math.pi * k / n
        pts.append((x1 + r * math.cos(t + math.pi), y1 + r * math.sin(t + math.pi)))
    return pts


def _convex_hull(points):
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def parse_gerber(text, name=""):
    """
    Parses one Gerber file. Returns {"shapes": [...], "bounds": (x0,y0,x1,y1),
    "flashes": [(x, y, w, h)], "warnings": [...]} with every number in mm.
    A shape is ("C", x, y, r, dark) or ("P", [(x, y), ...], dark).
    """
    shapes = []
    flashes = []
    warnings = []

    unit = _IN_TO_MM            # Gerber defaults to inches if MO is absent
    int_d, dec_d = 2, 6
    zero_omit = "L"
    incremental = False

    apertures = {}              # code -> list of local shapes (mm)
    aperture_size = {}          # code -> (w, h) for stroking / reporting
    aperture_round = {}         # code -> radius if a plain circle, else None
    macros = {}

    cur = None
    x = y = 0.0
    interp = 1                  # 1 linear, 2 cw, 3 ccw
    multi_quadrant = True
    dark = True
    region = False
    contour = []
    region_polys = []
    last_d = 2
    sr = None                   # (nx, ny, dx, dy, start index)

    def coord(raw):
        neg = raw.startswith("-")
        digits = raw.lstrip("+-")
        if "." in digits:
            val = float(digits)
        else:
            if zero_omit == "T":
                digits = digits.ljust(int_d + dec_d, "0")
            val = int(digits or "0") / (10.0 ** dec_d)
        return (-val if neg else val) * unit

    def build_macro(body, params):
        variables = {i + 1: v for i, v in enumerate(params)}
        local = []
        for stmt in body:
            stmt = stmt.strip()
            if not stmt or stmt.startswith("0 ") or stmt == "0":
                continue
            if stmt.startswith("$") and "=" in stmt:
                k, v = stmt.split("=", 1)
                variables[int(k[1:])] = _eval_macro_expr(v, variables)
                continue
            f = [_eval_macro_expr(p, variables) for p in stmt.split(",")]
            code = int(f[0])
            on = f[1] != 0 if len(f) > 1 else True
            if code == 1:                            # circle
                d, cx, cy = f[2], f[3], f[4]
                rot = f[5] if len(f) > 5 else 0
                cx, cy = _rot(cx, cy, rot)
                local.append(("C", cx * unit, cy * unit, d / 2 * unit, on))
            elif code == 4:                          # outline
                n = int(f[2])
                pts = [(f[3 + 2 * i], f[4 + 2 * i]) for i in range(n + 1)]
                rot = f[3 + 2 * (n + 1)] if len(f) > 3 + 2 * (n + 1) else 0
                local.append(("P", [(px * unit, py * unit) for px, py in
                                    (_rot(a, b, rot) for a, b in pts)], on))
            elif code == 5:                          # regular polygon
                n, cx, cy, d = int(f[2]), f[3], f[4], f[5]
                rot = f[6] if len(f) > 6 else 0
                pts = [_rot(cx + d / 2 * math.cos(2 * math.pi * k / n),
                            cy + d / 2 * math.sin(2 * math.pi * k / n), rot) for k in range(n)]
                local.append(("P", [(a * unit, b * unit) for a, b in pts], on))
            elif code in (2, 20):                    # vector line
                w, x1, y1, x2, y2 = f[2], f[3], f[4], f[5], f[6]
                rot = f[7] if len(f) > 7 else 0
                a = math.atan2(y2 - y1, x2 - x1)
                ox, oy = -math.sin(a) * w / 2, math.cos(a) * w / 2
                pts = [(x1 + ox, y1 + oy), (x2 + ox, y2 + oy), (x2 - ox, y2 - oy), (x1 - ox, y1 - oy)]
                local.append(("P", [(p * unit, q * unit) for p, q in
                                    (_rot(a_, b_, rot) for a_, b_ in pts)], on))
            elif code == 21:                         # centre line (rectangle)
                w, h, cx, cy = f[2], f[3], f[4], f[5]
                rot = f[6] if len(f) > 6 else 0
                pts = [(cx - w / 2, cy - h / 2), (cx + w / 2, cy - h / 2),
                       (cx + w / 2, cy + h / 2), (cx - w / 2, cy + h / 2)]
                local.append(("P", [(p * unit, q * unit) for p, q in
                                    (_rot(a_, b_, rot) for a_, b_ in pts)], on))
            elif code == 22:                         # lower-left line
                w, h, lx, ly = f[2], f[3], f[4], f[5]
                rot = f[6] if len(f) > 6 else 0
                pts = [(lx, ly), (lx + w, ly), (lx + w, ly + h), (lx, ly + h)]
                local.append(("P", [(p * unit, q * unit) for p, q in
                                    (_rot(a_, b_, rot) for a_, b_ in pts)], on))
            elif code == 7:                          # thermal -- drawn as its outer ring
                cx, cy, od, idd = f[1], f[2], f[3], f[4]
                local.append(("C", cx * unit, cy * unit, od / 2 * unit, True))
                local.append(("C", cx * unit, cy * unit, idd / 2 * unit, False))
            else:
                warnings.append("macro primitive %d ignored" % code)
        return local

    def define_aperture(code, kind, params):
        p = params
        if kind == "C":
            r = p[0] / 2 * unit
            apertures[code] = [("C", 0.0, 0.0, r, True)]
            aperture_size[code] = (p[0] * unit, p[0] * unit)
            aperture_round[code] = r
        elif kind == "R":
            w, h = p[0] * unit, p[1] * unit
            apertures[code] = [("P", [(-w / 2, -h / 2), (w / 2, -h / 2),
                                      (w / 2, h / 2), (-w / 2, h / 2)], True)]
            aperture_size[code] = (w, h)
            aperture_round[code] = None
        elif kind == "O":
            w, h = p[0] * unit, p[1] * unit
            if abs(w - h) < 1e-9:
                apertures[code] = [("C", 0.0, 0.0, w / 2, True)]
            elif w > h:
                apertures[code] = [("P", _capsule(-(w - h) / 2, 0, (w - h) / 2, 0, h / 2), True)]
            else:
                apertures[code] = [("P", _capsule(0, -(h - w) / 2, 0, (h - w) / 2, w / 2), True)]
            aperture_size[code] = (w, h)
            aperture_round[code] = None
        elif kind == "P":
            d, n = p[0] * unit, int(p[1])
            rot = p[2] if len(p) > 2 else 0
            apertures[code] = [("P", [_rot(d / 2 * math.cos(2 * math.pi * k / n),
                                           d / 2 * math.sin(2 * math.pi * k / n), rot)
                                      for k in range(n)], True)]
            aperture_size[code] = (d, d)
            aperture_round[code] = None
        elif kind in macros:
            local = build_macro(macros[kind], p)
            apertures[code] = local
            xs = [q[0] for s in local if s[0] == "P" for q in s[1]] + \
                 [s[1] + s[3] for s in local if s[0] == "C"] + [s[1] - s[3] for s in local if s[0] == "C"]
            ys = [q[1] for s in local if s[0] == "P" for q in s[1]] + \
                 [s[2] + s[3] for s in local if s[0] == "C"] + [s[2] - s[3] for s in local if s[0] == "C"]
            aperture_size[code] = ((max(xs) - min(xs)) if xs else 0, (max(ys) - min(ys)) if ys else 0)
            aperture_round[code] = None
        else:
            raise GerberError("aperture D%d uses unknown template %s" % (code, kind))

    def emit(shape):
        shapes.append(shape)

    def flash_at(px, py):
        if cur not in apertures:
            raise GerberError("flash with undefined aperture D%s" % cur)
        for s in apertures[cur]:
            on = s[-1] if dark else (not s[-1])
            if s[0] == "C":
                emit(("C", px + s[1], py + s[2], s[3], on))
            else:
                emit(("P", [(px + a, py + b) for a, b in s[1]], on))
        w, h = aperture_size.get(cur, (0, 0))
        flashes.append((px, py, w, h))

    def stroke(x1, y1, x2, y2):
        if cur not in apertures:
            raise GerberError("draw with undefined aperture D%s" % cur)
        r = aperture_round.get(cur)
        if r is not None:
            if abs(x1 - x2) < 1e-9 and abs(y1 - y2) < 1e-9:
                emit(("C", x1, y1, r, dark))
            else:
                emit(("P", _capsule(x1, y1, x2, y2, r), dark))
            return
        # non-round aperture: sweep its outline along the segment
        pts = []
        for s in apertures[cur]:
            local = s[1] if s[0] == "P" else _circle_poly(s[1], s[2], s[3], 8)
            for a, b in local:
                pts += [(x1 + a, y1 + b), (x2 + a, y2 + b)]
        emit(("P", _convex_hull(pts), dark))

    def arc_points(x1, y1, x2, y2, i, j, mode):
        if multi_quadrant:
            cx, cy = x1 + i, y1 + j
        else:
            # single-quadrant: I/J are unsigned; pick the centre giving <=90 deg
            best = None
            for sx in (1, -1):
                for sy in (1, -1):
                    cx_, cy_ = x1 + sx * abs(i), y1 + sy * abs(j)
                    a1 = math.atan2(y1 - cy_, x1 - cx_)
                    a2 = math.atan2(y2 - cy_, x2 - cx_)
                    sweep = (a2 - a1) if mode == 3 else (a1 - a2)
                    sweep %= 2 * math.pi
                    err = abs(math.hypot(x2 - cx_, y2 - cy_) - math.hypot(x1 - cx_, y1 - cy_))
                    if sweep <= math.pi / 2 + 1e-6 and (best is None or err < best[0]):
                        best = (err, cx_, cy_)
            cx, cy = (best[1], best[2]) if best else (x1 + i, y1 + j)
        r = math.hypot(x1 - cx, y1 - cy)
        a1 = math.atan2(y1 - cy, x1 - cx)
        a2 = math.atan2(y2 - cy, x2 - cx)
        if mode == 3:                       # counter-clockwise
            sweep = (a2 - a1) % (2 * math.pi)
        else:
            sweep = -((a1 - a2) % (2 * math.pi))
        if abs(sweep) < 1e-9 and multi_quadrant:
            sweep = 2 * math.pi if mode == 3 else -2 * math.pi
        n = max(4, int(abs(sweep) / (math.pi / 18)) + 1)
        return [(cx + r * math.cos(a1 + sweep * k / n), cy + r * math.sin(a1 + sweep * k / n))
                for k in range(n + 1)], (cx, cy, r)

    # ---- tokenise: %...% parameter blocks and *-terminated word blocks ----
    pos, n_text = 0, len(text)
    while pos < n_text:
        ch = text[pos]
        if ch in " \r\n\t":
            pos += 1
            continue
        if ch == "%":
            end = text.find("%", pos + 1)
            if end < 0:
                break
            block = text[pos + 1:end]
            pos = end + 1
            stmts = [s for s in block.replace("\r", "").replace("\n", "").split("*")]
            head = stmts[0]
            if head.startswith("FS"):
                m = re.match(r"FS([LT]?)([AI]?)X(\d)(\d)Y(\d)(\d)", head)
                if m:
                    zero_omit = m.group(1) or "L"
                    incremental = m.group(2) == "I"
                    int_d, dec_d = int(m.group(3)), int(m.group(4))
            elif head.startswith("MO"):
                unit = _IN_TO_MM if head[2:4] == "IN" else 1.0
            elif head.startswith("AM"):
                macros[head[2:]] = [s for s in stmts[1:] if s.strip()]
            elif head.startswith("AD"):
                m = re.match(r"ADD(\d+)([A-Za-z_.$][A-Za-z0-9_.$\-]*)(?:,(.*))?", head)
                if m:
                    params = [float(v) for v in m.group(3).split("X")] if m.group(3) else []
                    try:
                        define_aperture(int(m.group(1)), m.group(2), params)
                    except (GerberError, ValueError, IndexError) as e:
                        warnings.append(str(e))
            elif head.startswith("LP"):
                dark = head[2:3] != "C"
            elif head.startswith("SR"):
                if sr is not None:
                    nx, ny, dx_, dy_, start = sr
                    block_shapes = shapes[start:]
                    for ix in range(nx):
                        for iy in range(ny):
                            if ix == 0 and iy == 0:
                                continue
                            ox_, oy_ = ix * dx_, iy * dy_
                            for s in block_shapes:
                                if s[0] == "C":
                                    shapes.append(("C", s[1] + ox_, s[2] + oy_, s[3], s[4]))
                                else:
                                    shapes.append(("P", [(a + ox_, b + oy_) for a, b in s[1]], s[2]))
                    sr = None
                m = re.match(r"SRX(\d+)Y(\d+)I([\d.]+)J([\d.]+)", head)
                if m and (int(m.group(1)) > 1 or int(m.group(2)) > 1):
                    sr = (int(m.group(1)), int(m.group(2)),
                          float(m.group(3)) * unit, float(m.group(4)) * unit, len(shapes))
            continue

        end = text.find("*", pos)
        if end < 0:
            break
        word = text[pos:end].strip()
        pos = end + 1
        if not word:
            continue
        if word.startswith("G04") or word.startswith("G4 "):
            continue
        if word in ("M02", "M00", "M2", "M0"):
            break

        for g in re.findall(r"G0*(\d+)", word):
            g = int(g)
            if g in (1, 2, 3):
                interp = g
            elif g == 74:
                multi_quadrant = False
            elif g == 75:
                multi_quadrant = True
            elif g == 36:
                region, contour, region_polys = True, [], []
            elif g == 37:
                if len(contour) > 2:
                    region_polys.append(contour)
                for c in region_polys:
                    emit(("P", c, dark))
                region, contour, region_polys = False, [], []
            elif g == 70:
                unit = _IN_TO_MM
            elif g == 71:
                unit = 1.0
            elif g == 90:
                incremental = False
            elif g == 91:
                incremental = True
        word_wo_g = re.sub(r"G0*\d+", "", word)

        m = re.fullmatch(r"D0*(\d+)", word_wo_g.strip())
        if m and int(m.group(1)) >= 10:
            cur = int(m.group(1))
            continue

        mx = re.search(r"X([+-]?[\d.]+)", word_wo_g)
        my = re.search(r"Y([+-]?[\d.]+)", word_wo_g)
        mi = re.search(r"I([+-]?[\d.]+)", word_wo_g)
        mj = re.search(r"J([+-]?[\d.]+)", word_wo_g)
        md = re.search(r"D0*([123])(?!\d)", word_wo_g)
        if not (mx or my or md):
            continue
        nx_ = coord(mx.group(1)) if mx else None
        ny_ = coord(my.group(1)) if my else None
        if incremental:
            nx_ = x + (nx_ or 0.0)
            ny_ = y + (ny_ or 0.0)
        else:
            nx_ = x if nx_ is None else nx_
            ny_ = y if ny_ is None else ny_
        d = int(md.group(1)) if md else last_d
        last_d = d

        try:
            if d == 2:
                if region and len(contour) > 2:
                    region_polys.append(contour)
                contour = [(nx_, ny_)] if region else contour
            elif d == 1:
                if interp == 1:
                    if region:
                        if not contour:
                            contour = [(x, y)]
                        contour.append((nx_, ny_))
                    else:
                        stroke(x, y, nx_, ny_)
                else:
                    i_ = coord(mi.group(1)) if mi else 0.0
                    j_ = coord(mj.group(1)) if mj else 0.0
                    pts, (cx, cy, rr) = arc_points(x, y, nx_, ny_, i_, j_, interp)
                    if region:
                        if not contour:
                            contour = [(x, y)]
                        contour += pts[1:]
                    else:
                        r = aperture_round.get(cur)
                        if r is not None and rr > r:
                            outer = [(cx + (px_ - cx) * (rr + r) / rr, cy + (py_ - cy) * (rr + r) / rr)
                                     for px_, py_ in pts]
                            inner = [(cx + (px_ - cx) * (rr - r) / rr, cy + (py_ - cy) * (rr - r) / rr)
                                     for px_, py_ in pts]
                            emit(("P", outer + inner[::-1], dark))
                            emit(("C", pts[0][0], pts[0][1], r, dark))
                            emit(("C", pts[-1][0], pts[-1][1], r, dark))
                        else:
                            for a, b in zip(pts, pts[1:]):
                                stroke(a[0], a[1], b[0], b[1])
            elif d == 3:
                flash_at(nx_, ny_)
        except GerberError as e:
            warnings.append(str(e))
        x, y = nx_, ny_

    xs, ys = [], []
    for s in shapes:
        if s[0] == "C":
            xs += [s[1] - s[3], s[1] + s[3]]
            ys += [s[2] - s[3], s[2] + s[3]]
        else:
            xs += [p[0] for p in s[1]]
            ys += [p[1] for p in s[1]]
    bounds = (min(xs), min(ys), max(xs), max(ys)) if xs else None
    return {"name": name, "shapes": shapes, "flashes": flashes,
            "bounds": bounds, "warnings": warnings[:20]}


def gerber_files_in(path):
    """
    (name, text) for every Gerber file in a folder, a .zip, or a .rar.
    RAR is opened with the tar that ships with Windows 10/11 (libarchive).
    """
    out = []
    if os.path.isdir(path):
        for base, _dirs, files in os.walk(path):
            for fn in files:
                full = os.path.join(base, fn)
                try:
                    with open(full, encoding="latin-1") as f:
                        text = f.read()
                except OSError:
                    continue
                out.append((fn, text))
    elif zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                out.append((os.path.basename(info.filename),
                            z.read(info).decode("latin-1", "replace")))
    else:
        import subprocess
        import tempfile
        tar = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "tar.exe")
        if not os.path.exists(tar):
            raise GerberError("This archive needs Windows' built-in tar.exe to open, "
                              "which was not found. Extract it and choose the folder instead.")
        dest = tempfile.mkdtemp(prefix="kfg-gerber-")
        flags = 0x08000000 if os.name == "nt" else 0       # no console window
        p = subprocess.run([tar, "-xf", path, "-C", dest], capture_output=True,
                           creationflags=flags)
        if p.returncode != 0:
            raise GerberError("Could not open the archive:\n\n%s"
                              % p.stderr.decode("utf-8", "replace").strip()[:400])
        return gerber_files_in(dest)
    return [(n, t) for n, t in out if _is_gerber_text(t)]


# =====================================
# PCB 2D map: bodies drawn to size and rotation
# =====================================
#
# Each component is drawn as its body outline, turned by its rotation, with a
# dot on pad 1. Pad 1 is what makes a rotation checkable at all: an 0603 at 0
# and at 180 degrees has the same outline, and only the dot tells them apart.
#
# The dot is drawn where the file says pad 1 is, and the body is turned by the
# file's rotation -- the map shows the data, not a guess about it. In Altium's
# own frame a chip's pads lie along X at 0 degrees, which is how the long axis
# is chosen for two-terminal parts; that was checked on BOARD-A, where pad 1
# sits at (-0.75, 0) in the part's own frame for every 0603 at every rotation.

BODY_SIZE_RULES = [
    # (pattern, (length, width) in mm) -- first match wins
    (r"(?<![0-9])0201(?![0-9])", (0.6, 0.3)),
    (r"(?<![0-9])0402(?![0-9])", (1.0, 0.5)),
    (r"(?<![0-9])0603(?![0-9])", (1.6, 0.8)),
    (r"(?<![0-9])0805(?![0-9])", (2.0, 1.25)),
    (r"(?<![0-9])1206(?![0-9])", (3.2, 1.6)),
    (r"(?<![0-9])1210(?![0-9])", (3.2, 2.5)),
    (r"(?<![0-9])1812(?![0-9])", (4.5, 3.2)),
    (r"(?<![0-9])2010(?![0-9])", (5.0, 2.5)),
    (r"(?<![0-9])2512(?![0-9])", (6.4, 3.2)),
    (r"(?<![0-9])3528(?![0-9])|CASE[_\- ]?B\b", (3.5, 2.8)),
    (r"(?<![0-9])6032(?![0-9])|CASE[_\- ]?C\b", (6.0, 3.2)),
    (r"(?<![0-9])7343(?![0-9])|CASE[_\- ]?D\b", (7.3, 4.3)),
    (r"SOD[_\-]?523", (1.2, 0.8)),
    (r"SOD[_\-]?323", (1.7, 1.25)),
    (r"SOD[_\-]?123", (2.7, 1.6)),
    (r"\bSMA\b", (4.3, 2.6)),
    (r"\bSMB\b", (4.3, 3.6)),
    (r"\bSMC\b", (6.9, 5.9)),
    (r"SOT[_\-]?(?:323|363)", (2.0, 1.25)),
    (r"SOT[_\-]?223", (6.5, 3.5)),
    (r"SOT[_\-]?89", (4.5, 2.5)),
    (r"SOT", (2.9, 1.6)),
    (r"LQFP[_\-]?64|TQFP[_\-]?64", (10.0, 10.0)),
    (r"LQFP[_\-]?100|TQFP[_\-]?100", (14.0, 14.0)),
    (r"LQFP|TQFP", (7.0, 7.0)),
    (r"TSSOP", (5.0, 4.4)),
    (r"MSOP", (3.0, 3.0)),
    (r"\bSO[_\-]?16\b|SOIC[_\-]?16", (9.9, 3.9)),
    (r"\bSO[_\-]?14\b|SOIC[_\-]?14", (8.65, 3.9)),
    (r"SOIC|\bSO[_\-]?8\b|SO_8", (4.9, 3.9)),
    (r"\bQFN\b|[_\-]QFN", (4.0, 4.0)),
    (r"\bDFN\b|[_\-]DFN", (3.0, 3.0)),
    (r"[_\-]D(\d+(?:\.\d+)?)H\d", None),      # electrolytic: D<diameter>H<height>
]

TWO_TERMINAL = re.compile(
    r"(?<![0-9])(?:0201|0402|0603|0805|1206|1210|1812|2010|2512|3216|3528|6032|7343)"
    r"(?![0-9])|SOD|\bSM[ABC]\b|MELF|CASE",
    re.IGNORECASE)


def body_size(footprint):
    """(length, width) of a footprint's body in mm, or None if not known."""
    name = str(footprint).upper()
    for pattern, size in BODY_SIZE_RULES:
        m = re.search(pattern, name)
        if not m:
            continue
        if size is None:
            try:
                d = float(m.group(1))
                return d, d
            except (IndexError, ValueError):
                continue
        return size
    return None


def rotate_about(px, py, cx, cy, degrees):
    t = math.radians(degrees)
    dx, dy = px - cx, py - cy
    return (cx + dx * math.cos(t) - dy * math.sin(t),
            cy + dx * math.sin(t) + dy * math.cos(t))


def set_component_rotation(r, new_rotation):
    """
    Turns a component to a new rotation about its centre. Pad 1 and the
    reference point turn with it -- leaving them behind would describe a part
    whose pads no longer agree with its rotation.
    """
    try:
        old = float(r[9])
    except (TypeError, ValueError):
        old = 0.0
    new = tidy_angle(new_rotation)
    delta = new - old
    try:
        cx, cy = float(r[2]), float(r[3])
    except (TypeError, ValueError):
        r[9] = new
        return
    for xi, yi in ((4, 5), (6, 7)):
        try:
            x, y = rotate_about(float(r[xi]), float(r[yi]), cx, cy, delta)
        except (TypeError, ValueError):
            continue
        r[xi] = round(clean_zero(x), 3)
        r[yi] = round(clean_zero(y), 3)
    r[9] = new


def component_shape(r):
    """
    Board-coordinate outline for one component:
    (corners, pad-1 point or None, size known?).
    """
    try:
        cx, cy = float(r[2]), float(r[3])
        rot = float(r[9])
    except (TypeError, ValueError):
        return None
    size = body_size(r[1])
    known = size is not None
    length, width = size if known else (1.5, 1.5)

    pad = None
    try:
        px, py = float(r[6]), float(r[7])
        if math.hypot(px - cx, py - cy) > 0.05:
            pad = (px, py)
    except (TypeError, ValueError):
        pass

    # pad 1 in the part's own frame
    lx = ly = 0.0
    if pad is not None:
        px_, py_ = rotate_about(pad[0], pad[1], cx, cy, -rot)
        lx, ly = px_ - cx, py_ - cy

    # For a two-terminal part the long axis runs between the pads; read it off
    # pad 1 rather than assuming.
    if pad is not None and TWO_TERMINAL.search(str(r[1]).upper()):
        if abs(ly) > abs(lx):
            length, width = width, length

    # Leads stick out past the body on gull-wing parts -- SOT-23 pad 1 sits
    # outside the moulding. Footprints are symmetric, so pad 1's offset gives
    # the reach of the pads; draw the larger of that and the body.
    hl = max(length / 2.0, abs(lx))
    hw = max(width / 2.0, abs(ly))
    corners = [rotate_about(cx + dx, cy + dy, cx, cy, rot)
               for dx, dy in ((-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw))]
    return corners, pad, known, min(hl, hw)


# =========================================================
# Machine angle pattern
# =========================================================
#
# Altium's Rotation is relative to however a library happened to draw the
# footprint. Two 0805 libraries can disagree by 90 or 180 degrees for a part
# sitting exactly the same way on the board -- BOARD-A alone carries 0805s
# with pad 1 to the left and to the right of centre, and both differ by 90 from
# the GPS board's library.
#
# The machine does not know any library. What it needs is the part's real
# orientation, and that is in the data regardless of library: the line from
# the part's centre to pad 1. So the angle sent to the machine is worked out
# per component from that line, against this pattern -- where pad 1 points
# when the machine is at 0 degrees -- for each kind of part.
#
# The pattern comes from the GPS board, whose machine file is known to place
# correctly: chips had pad 1 toward +Y at the machine's 0 degrees (39 of 39),
# the WL-SMCW LEDs and WCAP electrolytics toward +X (12 of 12).
#
# Only multiples of 90 degrees are ever added to Altium's own angle, so a part
# deliberately placed at 45 degrees stays at 45 plus the library correction.

# (name, test(footprint, designator), pad-1 direction at machine 0 deg,
#  symmetric (either end will do), proven on a working machine file)
def _designator_prefix(des):
    m = re.match(r"[A-Za-z]+", str(des).strip())
    return m.group(0).upper() if m else ""


_POLAR_PREFIXES = {"D", "LED", "LD", "CR", "Z", "ZD"}
_ACTIVE_PREFIXES = {"U", "IC", "Q", "T", "K", "X", "Y"}


def _is_polar_cap(fp, des):
    name = str(fp).upper()
    return (_designator_prefix(des) == "C" and
            (re.search(r"CAP[_\- ]?POL|TANTAL|CASE[_\- ]?[A-E]\b|SIZE[_\- ]?[A-E]\b"
                       r"|(?<![0-9])(?:3216|3528|6032|7343)(?![0-9])|[_\-]D\d+(?:\.\d+)?H\d", name)
             is not None))


def _is_plain_chip(fp, des):
    pre = _designator_prefix(des)
    return (clean_footprint(fp) in CHIP_SIZES and pre not in _POLAR_PREFIXES
            and pre not in _ACTIVE_PREFIXES and not _is_polar_cap(fp, des))


def _is_two_pin_diode(fp, des):
    pre = _designator_prefix(des)
    two_pin = TWO_TERMINAL.search(str(fp).upper()) is not None or "LED" in str(fp).upper()
    return two_pin and (pre in _POLAR_PREFIXES or "LED" in str(fp).upper())


MACHINE_ANGLE_PATTERN = [
    ("LED (WL-SMCW)", lambda fp, des: "WL-SMCW" in str(fp).upper(), 0.0, False, True),
    ("electrolytic (WCAP)", lambda fp, des: "WCAP" in str(fp).upper(), 0.0, False, True),
    ("chip R/C/L", _is_plain_chip, 90.0, True, True),
    ("LED / diode", _is_two_pin_diode, 0.0, False, False),
    ("polarised capacitor", _is_polar_cap, 0.0, False, False),
]

# id(row) -> why its angle wants a look. Cleared when the part is turned by hand.
angle_flags = {}


def machine_angle_for(r):
    """
    (angle, None) for the machine, or (None, reason) when the pattern cannot
    say. Pure: does not touch the row.
    """
    for name, test, ref, symmetric, proven in MACHINE_ANGLE_PATTERN:
        if test(r[1], r[0]):
            break
    else:
        return None, "no pattern for this kind of part -- angle kept as Altium gave it"
    try:
        cx, cy = float(r[2]), float(r[3])
        px, py = float(r[6]), float(r[7])
        alt = float(r[9])
    except (TypeError, ValueError):
        return None, "no usable pad-1 position"
    if math.hypot(px - cx, py - cy) < 0.05:
        return None, "pad 1 is at the centre -- direction unknown"
    phi = math.degrees(math.atan2(py - cy, px - cx))
    raw = phi - ref - alt
    corr = 90.0 * round(raw / 90.0)
    if abs(((raw - corr) + 180.0) % 360.0 - 180.0) > 10.0:
        return None, "pads are not on a right angle to the part -- angle kept"
    angle = (alt + corr) % 360.0
    if symmetric:
        angle %= 180.0
    return angle, (None if proven else "pattern for %s assumed, not yet proven on "
                                       "this machine -- check polarity" % name)


def normalise_rotations(rows_in=None):
    """
    Sets every component's Rotation to the machine's pattern, from its own pad 1.
    Idempotent: a second run changes nothing. Returns a summary.
    """
    target = rows_in if rows_in is not None else placeable_rows()
    changed = Counter()
    flagged = Counter()
    for r in target:
        angle, note = machine_angle_for(r)
        if angle is None:
            angle_flags[id(r)] = note
            flagged[note.split(" --")[0]] += 1
            continue
        try:
            old = float(r[9])
        except (TypeError, ValueError):
            old = 0.0
        new = int(round(angle)) if abs(angle - round(angle)) < 1e-6 else round(angle, 2)
        delta = (new - old) % 360
        if abs(delta) > 1e-6 and abs(delta - 360) > 1e-6:
            r[9] = new
            changed[int(round(delta))] += 1
        else:
            r[9] = new if not isinstance(r[9], str) else r[9]
        if note:
            angle_flags[id(r)] = note
            flagged["polarity assumed"] += 1
        else:
            angle_flags.pop(id(r), None)
    return changed, flagged


def angle_summary(changed, flagged):
    lines = []
    total = sum(changed.values())
    if total:
        parts = ", ".join("%d by %d deg" % (n, d) for d, n in sorted(changed.items()))
        lines.append("Rotations set to the machine's pattern: %d part(s) changed (%s)."
                     % (total, parts))
    else:
        lines.append("Rotations already match the machine's pattern.")
    if flagged:
        lines.append("To check on the 2D map (outlined orange): " +
                     "; ".join("%d %s" % (n, k) for k, n in sorted(flagged.items())) + ".")
    return "\n".join(lines)


map_state = {"zoom": 1.0, "pan": (0.0, 0.0), "selected": None, "frame": None}

# --------------------------------------------------------------------------
# Board transform
# --------------------------------------------------------------------------
# Every operation that moves the whole board -- offset, rotation, flip, the
# two-point fit -- is also recorded here, as one affine map from the design
# coordinates the board was loaded in to where it is now. The Gerber underlay
# is drawn through it, so it stays under the parts whatever is done to them.
#   x' = a*x + b*y + e,   y' = c*x + d*y + f
board_xform = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]


def xform_reset():
    board_xform[:] = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    map_state["frame"] = None


def xform_then(A, B, C, D, E, F):
    """Follow the current transform with another one."""
    a, b, c, d, e, f = board_xform
    board_xform[:] = [A * a + B * c, A * b + B * d,
                      C * a + D * c, C * b + D * d,
                      A * e + B * f + E, C * e + D * f + F]
    map_state["frame"] = None


def xform_then_rotation(theta_deg, cx=0.0, cy=0.0):
    t = math.radians(theta_deg)
    co, si = math.cos(t), math.sin(t)
    xform_then(co, -si, si, co, cx - co * cx + si * cy, cy - si * cx - co * cy)


def xform_apply(x, y):
    a, b, c, d, e, f = board_xform
    return a * x + b * y + e, c * x + d * y + f


def xform_invert(x, y):
    a, b, c, d, e, f = board_xform
    det = a * d - b * c
    if abs(det) < 1e-12:
        return x, y
    x, y = x - e, y - f
    return (d * x - b * y) / det, (-c * x + a * y) / det


def xform_scale():
    a, b, c, d, _, _ = board_xform
    return math.sqrt(abs(a * d - b * c))


# --------------------------------------------------------------------------
# Gerber underlay
# --------------------------------------------------------------------------
gerber_state = {
    "layers": [],          # parsed layers, bottom of the stack first
    "visible": {},         # layer key -> shown?
    "offset": (0.0, 0.0),  # Gerber minus Pick & Place, in design mm
    "source": None,
    "report": "",
}

MAP_BG = "#0f1418"


def _gerber_to_current(x, y):
    ox, oy = gerber_state["offset"]
    return xform_apply(x - ox, y - oy)


def register_gerber():
    """
    Finds where the Gerber sits relative to the components: the offset that
    puts the most pad-1 positions on a copper flash. Altium usually shares one
    origin between the two, but a board exported relative to a different
    origin is lined up the same way.
    """
    flashes = [(fx, fy) for L in gerber_state["layers"] if L["kind"] == "copper"
               for fx, fy, _, _ in L["flashes"]]
    pads = []
    for r in placeable_rows():
        try:
            pads.append(xform_invert(float(r[6]), float(r[7])))
        except (TypeError, ValueError):
            continue
    if not flashes or not pads:
        gerber_state["offset"] = (0.0, 0.0)
        return 0, len(pads)

    step = 0.05
    votes = Counter()
    sample = pads[:300]
    for px, py in sample:
        for fx, fy in flashes:
            dx, dy = fx - px, fy - py
            if abs(dx) < 1000 and abs(dy) < 1000:
                votes[(round(dx / step), round(dy / step))] += 1
    (bx, by), _ = votes.most_common(1)[0]
    gerber_state["offset"] = (bx * step, by * step)

    ox, oy = gerber_state["offset"]
    matched = 0
    for px, py in pads:
        best = min((fx - ox - px) ** 2 + (fy - oy - py) ** 2 for fx, fy in flashes)
        if best < 0.2 ** 2:
            matched += 1
    return matched, len(pads)


def pad_side_mismatches():
    """
    Components whose pad 1 sits on the other side's copper than the side the
    Pick & Place file lists them on. Returns {listed side: [designators]}.
    """
    ox, oy = gerber_state["offset"]
    by_side = {}
    for L in gerber_state["layers"]:
        if L["kind"] == "copper" and L["side"] in ("top", "bottom"):
            by_side.setdefault(L["side"], []).extend((fx, fy) for fx, fy, _, _ in L["flashes"])
    if not by_side:
        return {}
    wrong = {}
    for r in placeable_rows():
        listed = "bottom" if "bottom" in str(r[8]).lower() or str(r[8]).strip().upper() == "B" else "top"
        try:
            px, py = xform_invert(float(r[6]), float(r[7]))
        except (TypeError, ValueError):
            continue
        found = None
        for side, fl in by_side.items():
            if fl and min((fx - ox - px) ** 2 + (fy - oy - py) ** 2 for fx, fy in fl) < 0.2 ** 2:
                found = side
                break
        if found and found != listed:
            wrong.setdefault(listed, []).append(str(r[0]))
    return wrong


def gerber_layers_from_texts(pairs):
    """Parses (name, text) pairs into layers, bottom of the stack first."""
    layers = []
    for name, text in pairs:
        ident = identify_gerber_layer(name, text)
        if ident is None:
            continue
        key, label, side, kind = ident
        parsed = parse_gerber(text, name)
        if not parsed["shapes"]:
            continue
        parsed.update(key=key, label=label, side=side, kind=kind, text=text,
                      color=GERBER_COLORS.get(key, GERBER_COLORS.get(kind, GERBER_COLORS["other"])))
        layers.append(parsed)
    order = {"mask": 0, "copper": 1, "paste": 2, "pads": 3, "silk": 4,
             "mechanical": 5, "outline": 6, "other": 7}
    layers.sort(key=lambda L: ({"bottom": 0, "inner": 1, "both": 2, "top": 3}.get(L["side"], 2),
                               order.get(L["kind"], 9)))
    return layers


def _default_visibility(layers):
    """Show the copper and silkscreen of the side the pads are on, and the outline."""
    counts = Counter()
    for L in layers:
        if L["kind"] == "copper" and L["side"] in ("top", "bottom"):
            counts[L["side"]] += len(L["flashes"])
    side = counts.most_common(1)[0][0] if counts else "top"
    visible = {}
    has_outline = any(L["kind"] == "outline" for L in layers)
    for L in layers:
        on = (L["side"] == side and L["kind"] in ("copper", "silk")) or L["kind"] == "outline"
        if not has_outline and L["key"] in ("mech1", "mech"):
            on = True
        visible[L["key"]] = on
    return visible


def install_gerber(layers, source, visible=None, offset=None):
    gerber_state["layers"] = layers
    gerber_state["source"] = source
    gerber_state["visible"] = visible if visible is not None else _default_visibility(layers)
    if offset is None:
        matched, total = register_gerber()
    else:
        gerber_state["offset"] = tuple(offset)
        matched, total = None, None
    gerber_state["match"] = (matched, total)
    map_state["frame"] = None
    map_state["gerber_sig"] = None
    _rebuild_layer_panel()
    render_pcb_preview()
    return matched, total


def load_gerber_dialog():
    path = filedialog.askopenfilename(
        title="Load Gerber -- choose a .zip or .rar, or any one file of the set",
        filetypes=(("Gerber set", "*.zip *.rar *.gbr *.gtl *.gbl *.gto *.gbo *.gko *.gm1"),
                   ("All Files", "*.*")))
    if not path:
        return
    if not HAVE_PIL:
        messagebox.showerror("Load Gerber", "Showing a Gerber needs the Pillow package "
                                            "(pip install pillow).")
        return
    lower = path.lower()
    source = path if (lower.endswith(".zip") or lower.endswith(".rar")) else os.path.dirname(path)
    try:
        pairs = gerber_files_in(source)
        layers = gerber_layers_from_texts(pairs)
    except (GerberError, OSError) as e:
        messagebox.showerror("Load Gerber", str(e))
        return
    if not layers:
        messagebox.showerror("Load Gerber", "No Gerber layers were found there.")
        return

    matched, total = install_gerber(layers, source)
    notes = ["%d layers loaded from %s." % (len(layers), os.path.basename(source))]
    if total:
        ox, oy = gerber_state["offset"]
        notes.append("Lined up with the parts: pad 1 of %d of %d components sits on a "
                     "copper pad%s." % (matched, total,
                                        "" if abs(ox) < 1e-9 and abs(oy) < 1e-9
                                        else " (Gerber shifted %.2f, %.2f mm)" % (-ox, -oy)))
        if matched < 0.6 * total:
            notes.append("That is too few to trust -- the Gerber may be from another "
                         "revision of the board.")
    wrong = pad_side_mismatches()
    for listed, des in sorted(wrong.items()):
        other = "Bottom" if listed == "top" else "Top"
        notes.append("%d component(s) listed as %s layer have their pads on %s copper -- "
                     "the Pick & Place layer column is wrong for them, and they are "
                     "really placed on the %s." % (len(des), listed.title(), other,
                                                   other.lower()))
    gerber_state["report"] = "\n\n".join(notes)
    messagebox.showinfo("Load Gerber", gerber_state["report"])


def clear_gerber():
    board_canvas.delete("gerber")
    gerber_state.update(layers=[], visible={}, offset=(0.0, 0.0), source=None, report="")
    map_state["frame"] = None
    map_state["gerber_sig"] = None
    _rebuild_layer_panel()
    render_pcb_preview()


def _set_layer_visible(key, on):
    gerber_state["visible"][key] = on
    _map_draw_gerber()


def _rebuild_layer_panel():
    for w in gerber_layer_box.winfo_children():
        w.destroy()
    layers = gerber_state["layers"]
    if not layers:
        gerber_info.config(text="No Gerber loaded.")
        return
    m = gerber_state.get("match") or (None, None)
    if m[1]:
        gerber_info.config(text="Pads matched: %d / %d" % m,
                           fg="#5ce65c" if m[0] >= 0.9 * m[1] else "#ff9900")
    else:
        gerber_info.config(text="%d layers" % len(layers), fg=fg_color)
    for L in reversed(layers):                       # top of the stack first
        row = tk.Frame(gerber_layer_box, bg=bg_color)
        row.pack(fill="x", pady=1)
        tk.Label(row, bg=L["color"], width=2).pack(side="left", padx=(0, 6))
        var = tk.BooleanVar(value=gerber_state["visible"].get(L["key"], False))
        tk.Checkbutton(row, text=L["label"], variable=var, bg=bg_color, fg=fg_color,
                       selectcolor=tree_bg, activebackground=bg_color,
                       activeforeground=fg_color, font=("Segoe UI", 9), anchor="w",
                       command=lambda k=L["key"], v=var: _set_layer_visible(k, v.get())
                       ).pack(side="left", fill="x")


# --------------------------------------------------------------------------
# Projection
# --------------------------------------------------------------------------
def _map_canvas_size():
    cw, ch_ = board_canvas.winfo_width(), board_canvas.winfo_height()
    if cw < 10 or ch_ < 10:
        top = board_canvas.winfo_toplevel()
        top.update_idletasks()
        cw = max(top.winfo_width() - 300, 800)
        ch_ = max(top.winfo_height() - 180, 500)
    return cw, ch_


def _map_compute_frame(shapes):
    """
    The fitted view. It is fixed until something asks for a new one, so
    editing a part never makes the board jump.
    """
    xs, ys = [], []
    for s in shapes:
        xs += [p[0] for p in s[2]]
        ys += [p[1] for p in s[2]]
    for L in gerber_state["layers"]:
        if L["bounds"]:
            x0, y0, x1, y1 = L["bounds"]
            for gx, gy in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
                cx, cy = _gerber_to_current(gx, gy)
                xs.append(cx)
                ys.append(cy)
    if not xs:
        return None
    min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
    bw, bh = max(max_x - min_x, 1.0), max(max_y - min_y, 1.0)
    cw, ch_ = _map_canvas_size()
    pad_px = 30
    fit = min((cw - 2 * pad_px) / bw, (ch_ - 2 * pad_px) / bh)
    return {"min_x": min_x, "max_y": max_y, "fit": fit,
            "ox": (cw - bw * fit) / 2, "oy": (ch_ - bh * fit) / 2, "size": (cw, ch_),
            "box": (min_x, min_y, max_x, max_y)}


def _map_projection():
    fr = map_state["frame"]
    z = map_state["zoom"]
    pan_x, pan_y = map_state["pan"]
    min_x, max_y, fit, ox, oy = fr["min_x"], fr["max_y"], fr["fit"], fr["ox"], fr["oy"]

    def to_px(x, y):
        # board Y points up, the canvas's down; zoom and pan applied last
        return ((ox + (x - min_x) * fit) * z + pan_x,
                (oy + (max_y - y) * fit) * z + pan_y)
    return to_px, fit * z


def _hex_rgb(color):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def _gerber_prepare(layer):
    """Flat coordinate lists and bounding boxes, worked out once per layer."""
    if "prepared" in layer:
        return layer["prepared"]
    prepared = []
    for sh in layer["shapes"]:
        if sh[0] == "C":
            prepared.append(("C", sh[1], sh[2], sh[3], sh[4],
                             (sh[1] - sh[3], sh[2] - sh[3], sh[1] + sh[3], sh[2] + sh[3])))
        else:
            xs = [p[0] for p in sh[1]]
            ys = [p[1] for p in sh[1]]
            flat = [c for p in sh[1] for c in p]
            prepared.append(("P", flat, sh[2], (min(xs), min(ys), max(xs), max(ys))))
    layer["prepared"] = prepared
    return prepared


def _gerber_to_screen_matrix():
    """One affine map straight from Gerber millimetres to canvas pixels."""
    fr = map_state["frame"]
    z = map_state["zoom"]
    pan_x, pan_y = map_state["pan"]
    k = z * fr["fit"]
    p_off = z * (fr["ox"] - fr["min_x"] * fr["fit"]) + pan_x
    q_off = z * (fr["oy"] + fr["max_y"] * fr["fit"]) + pan_y
    a, b, c, d, e, f = board_xform
    ox, oy = gerber_state["offset"]
    e2 = e - a * ox - b * oy
    f2 = f - c * ox - d * oy
    return (k * a, k * b, -k * c, -k * d, k * e2 + p_off, -k * f2 + q_off)


def _gerber_signature():
    fr = map_state["frame"]
    return (id(gerber_state["layers"]), tuple(board_xform), gerber_state["offset"],
            tuple(sorted(gerber_state["visible"].items())),
            (fr["min_x"], fr["max_y"], fr["fit"], fr["ox"], fr["oy"]) if fr else None,
            map_state["zoom"], map_state["pan"], _map_canvas_size())


def _map_draw_gerber(to_px=None, scale=None):
    """Renders the visible Gerber layers into one image filling the view."""
    board_canvas.delete("gerber")
    map_state["gerber_sig"] = _gerber_signature()
    if not HAVE_PIL or not gerber_state["layers"]:
        return
    cw, ch_ = _map_canvas_size()
    ma, mb, mc, md, me, mf = _gerber_to_screen_matrix()
    det = ma * md - mb * mc
    if abs(det) < 1e-12:
        return

    # the view's footprint in Gerber coordinates, to skip what is off screen
    def inv(px, py):
        px, py = px - me, py - mf
        return (md * px - mb * py) / det, (-mc * px + ma * py) / det
    corners = [inv(0, 0), inv(cw, 0), inv(cw, ch_), inv(0, ch_)]
    vx0 = min(c[0] for c in corners); vx1 = max(c[0] for c in corners)
    vy0 = min(c[1] for c in corners); vy1 = max(c[1] for c in corners)
    rscale = math.sqrt(abs(det))

    bg = _hex_rgb(MAP_BG)
    img = Image.new("RGB", (cw, ch_), bg)
    draw = ImageDraw.Draw(img)
    for layer in gerber_state["layers"]:
        if not gerber_state["visible"].get(layer["key"]):
            continue
        color = _hex_rgb(layer["color"])
        for sh in _gerber_prepare(layer):
            bx0, by0, bx1, by1 = sh[-1]
            if bx1 < vx0 or bx0 > vx1 or by1 < vy0 or by0 > vy1:
                continue
            if sh[0] == "C":
                cx, cy = ma * sh[1] + mb * sh[2] + me, mc * sh[1] + md * sh[2] + mf
                r = sh[3] * rscale
                draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color if sh[4] else bg)
            else:
                flat = sh[1]
                out = [0.0] * len(flat)
                for i in range(0, len(flat), 2):
                    x, y = flat[i], flat[i + 1]
                    out[i] = ma * x + mb * y + me
                    out[i + 1] = mc * x + md * y + mf
                if len(out) >= 6:
                    draw.polygon(out, fill=color if sh[2] else bg)
    photo = ImageTk.PhotoImage(img)
    map_state["gerber_photo"] = photo           # Tk keeps no reference of its own
    board_canvas.create_image(0, 0, anchor="nw", image=photo, tags=("gerber",))
    board_canvas.tag_lower("gerber")


def render_pcb_preview(components_data=None):
    """
    Draws the parts over the Gerber, if one is loaded: every component as its
    rotated body with its centre and a pad-1 dot. The Gerber is drawn once and
    then only stretched and moved, so redrawing the parts after an edit stays
    quick even with thousands of Gerber shapes underneath.
    """
    board_canvas.delete("overlay")

    shapes = []
    for idx, r in enumerate(rows):
        shape = component_shape(r)
        if shape is not None:
            shapes.append((idx, r) + shape)
    if not shapes and not gerber_state["layers"]:
        board_canvas.delete("gerber")
        map_status.config(text="Load a Pick & Place file to see the board.")
        return

    fr = map_state.get("frame")
    if fr is None or fr["size"] != _map_canvas_size():
        map_state["frame"] = _map_compute_frame(shapes)
    if map_state["frame"] is None:
        return
    to_px, scale = _map_projection()
    map_state["to_px"] = to_px
    map_state["scale"] = scale

    if gerber_state["layers"]:
        if map_state.get("gerber_sig") != _gerber_signature():
            _map_draw_gerber()
    else:
        board_canvas.delete("gerber")
        min_x, min_y, max_x, max_y = map_state["frame"]["box"]
        x0, y0 = to_px(min_x, max_y)
        x1, y1 = to_px(max_x, min_y)
        board_canvas.create_rectangle(x0 - 6, y0 - 6, x1 + 6, y1 + 6,
                                      outline="#3e4a52", width=1, tags=("board", "overlay"))

    selected = _map_selected_index()
    sel_key = part_name_key(rows[selected][10]) if selected is not None else None
    over_gerber = bool(gerber_state["layers"])

    for idx, r, corners, pad, known, half_min in shapes:
        tag = "c%d" % idx
        pts = [c for xy in corners for c in to_px(*xy)]
        is_fid = str(r[0]).strip().upper().startswith("FID")
        if idx == selected:
            outline, width, fill = "#ff4fd8", 2, "" if over_gerber else "#4a1f44"
        elif sel_key is not None and part_name_key(r[10]) == sel_key:
            outline, width, fill = "#d77bc8", 1, "" if over_gerber else "#2e1d2c"
        elif is_fid:
            outline, width, fill = "#5ce65c", 1, ""
        elif id(r) in angle_flags:
            outline, width, fill = "#ff9f1a", 2, "" if over_gerber else "#3a2a12"
        else:
            # Over a Gerber the outline stays hollow, so the pads show through
            # and a part that does not sit on its pads is plain to see.
            outline, width, fill = "#7fd0ff", 1, "" if over_gerber else "#1d3247"

        tags = (tag, "comp", "overlay")
        if is_fid:
            cx, cy = to_px(float(r[2]), float(r[3]))
            rad = max(3, 0.5 * scale)
            board_canvas.create_oval(cx - rad, cy - rad, cx + rad, cy + rad,
                                     outline=outline, width=width, tags=tags)
        else:
            board_canvas.create_polygon(
                pts, outline=outline, fill=fill, width=width,
                dash=() if known else (3, 2), tags=tags)
        if not is_fid:
            # The placement point: Center-X/Y, the spot the machine puts the
            # part on. It is the middle of the outline by construction; drawn
            # so that it is never mistaken for the pad-1 dot.
            mx, my = to_px(float(r[2]), float(r[3]))
            arm = max(2.0, min(6.0, 0.45 * half_min * scale))
            cross = "#ff4fd8" if idx == selected else "#e6edf2"
            board_canvas.create_line(mx - arm, my, mx + arm, my, fill=cross,
                                     width=1, tags=tags + ("centre",))
            board_canvas.create_line(mx, my - arm, mx, my + arm, fill=cross,
                                     width=1, tags=tags + ("centre",))

        if pad is not None and not is_fid:
            px, py = to_px(*pad)
            # in proportion to the part, but never lost and never a blob
            dot = max(1.8, min(5.0, 0.35 * half_min * scale))
            board_canvas.create_oval(px - dot, py - dot, px + dot, py + dot,
                                     fill="#ff6b6b", outline="", tags=tags)


    map_state["tip_bg"] = board_canvas.create_rectangle(0, 0, 0, 0, fill="#333333",
                                                        outline="", state="hidden",
                                                        tags=("overlay",))
    map_state["tip"] = board_canvas.create_text(0, 0, text="", anchor="sw", fill="white",
                                                font=("Segoe UI", 9), state="hidden",
                                                tags=("overlay",))

    unknown = sum(1 for s in shapes if not s[4])
    _map_refresh_panel()
    if unknown and selected is None:
        map_status.config(text="%d component(s) drawn dashed: body size not known from the "
                               "footprint name." % unknown)


def _map_current_index():
    """Index of the component under the pointer, from its c<index> tag."""
    item = board_canvas.find_withtag("current")
    if not item:
        return None
    for t in board_canvas.gettags(item[0]):
        if t[:1] == "c" and t[1:].isdigit():
            return int(t[1:])
    return None


def _map_on_enter(event):
    i = _map_current_index()
    if i is not None:
        _map_hover(event, i)


def _map_on_click(event):
    i = _map_current_index()
    if i is not None:
        _map_select(i)


def _map_on_double(event):
    i = _map_current_index()
    if i is not None:
        _map_open_editor(i)


def _map_hover(event, idx):
    if idx >= len(rows):
        return
    r = rows[idx]
    text = "%s   %s\n%s   %s deg\ncentre  X %s   Y %s" % (
        r[0], r[10], r[1], r[9], _mm(r[2]), _mm(r[3]))
    if id(r) in angle_flags:
        text += "\nangle: " + angle_flags[id(r)]
    board_canvas.itemconfig(map_state["tip"], text=text, state="normal")
    board_canvas.coords(map_state["tip"], event.x + 14, event.y - 10)
    bbox = board_canvas.bbox(map_state["tip"])
    if bbox:
        board_canvas.coords(map_state["tip_bg"], bbox[0] - 4, bbox[1] - 3,
                            bbox[2] + 4, bbox[3] + 3)
        board_canvas.itemconfig(map_state["tip_bg"], state="normal")
        board_canvas.tag_raise(map_state["tip_bg"])
        board_canvas.tag_raise(map_state["tip"])


def _mm(v):
    try:
        return "%.2f" % float(v)
    except (TypeError, ValueError):
        return str(v)


def _map_unhover(event=None):
    if "tip" in map_state:
        board_canvas.itemconfig(map_state["tip"], state="hidden")
        board_canvas.itemconfig(map_state["tip_bg"], state="hidden")


def _map_selected_index():
    """Index of the selected component, found by identity so that deleting
    other rows cannot move the selection onto a different part."""
    obj = map_state.get("selected")
    if obj is None:
        return None
    for i, r in enumerate(rows):
        if r is obj:
            return i
    map_state["selected"] = None
    return None


def _map_select(idx):
    map_state["selected"] = rows[idx] if idx < len(rows) else None
    board_canvas.focus_set()
    render_pcb_preview()
    # keep the table in step with the map
    iid = str(idx)
    if tree.exists(iid):
        tree.selection_set(iid)
        tree.see(iid)


def _map_open_editor(idx):
    iid = str(idx)
    if not tree.exists(iid):
        return
    tree.selection_set(iid)
    tree.focus(iid)

    class _Event:
        widget = tree
    on_row_double_click(_Event())


def _map_refresh_panel():
    idx = _map_selected_index()
    if idx is None:
        map_sel_label.config(text="Click a part to select it.")
        map_angle_var.set("")
        map_status.config(text="+ centre (where the part is placed)    \u25cf pad 1    "
                               "Wheel: zoom    Right-drag: pan    R / Shift+R: turn    "
                               "Double-click: full editor")
        return
    r = rows[idx]
    same = sum(1 for x in placeable_rows() if part_name_key(x[10]) == part_name_key(r[10]))
    map_sel_label.config(text="%s   %s   %s     centre %s, %s"
                              % (r[0], r[10], r[1], _mm(r[2]), _mm(r[3])))
    map_angle_var.set(str(r[9]))
    map_all_check.config(text="Turn every %s (%d)" % (r[10], same))
    if id(r) in angle_flags:
        map_status.config(text="Angle to check: %s. Turning it here marks it checked."
                               % angle_flags[id(r)])
    else:
        map_status.config(text="+ is the centre, where the part is placed; the red dot is "
                               "pad 1. Parts turn about their centre. Buttons turn by that "
                               "many degrees; typing an angle sets it.")


def _map_rotate(delta=None, absolute=None):
    """Turn the selected part, and with the box ticked every part like it."""
    idx = _map_selected_index()
    if idx is None:
        return
    target = rows[idx]
    try:
        current = float(target[9])
    except (TypeError, ValueError):
        current = 0.0
    if absolute is not None:
        delta = float(absolute) - current
    if not delta:
        return
    affected = [target]
    if map_all_var.get():
        key = part_name_key(target[10])
        # The same delta for every one: they are placed at different angles,
        # and what is wrong is the offset, not the angle.
        affected = [r for r in rows if part_name_key(r[10]) == key]
    for r in affected:
        try:
            base = float(r[9])
        except (TypeError, ValueError):
            base = 0.0
        set_component_rotation(r, base + delta)
        angle_flags.pop(id(r), None)
    refresh_table()


def _map_apply_typed(event=None):
    text = map_angle_var.get().strip()
    try:
        value = float(text)
    except ValueError:
        messagebox.showerror("Rotation", "'%s' is not an angle." % text)
        return
    _map_rotate(absolute=value)


def _map_zoom(event):
    factor = 1.15 if (getattr(event, "delta", 0) > 0 or getattr(event, "num", 0) == 4) else 1 / 1.15
    new_zoom = max(0.5, min(40.0, map_state["zoom"] * factor))
    factor = new_zoom / map_state["zoom"]
    if factor == 1.0:
        return
    map_state["zoom"] = new_zoom
    # keep the point under the cursor where it is
    dx, dy = map_state["pan"]
    map_state["pan"] = (event.x - (event.x - dx) * factor, event.y - (event.y - dy) * factor)
    # The Gerber is all filled shapes, so stretching it is exact and cheap; the
    # parts are redrawn so their outlines and pad dots keep their weight.
    render_pcb_preview()


def _map_pan_start(event):
    map_state["drag"] = (event.x, event.y)
    map_state["drag_from"] = (event.x, event.y)
    map_state["press_index"] = _map_current_index()


def _map_pan_move(event):
    if "drag" not in map_state:
        return
    lx, ly = map_state["drag"]
    dx, dy = event.x - lx, event.y - ly
    map_state["drag"] = (event.x, event.y)
    px, py = map_state["pan"]
    map_state["pan"] = (px + dx, py + dy)
    board_canvas.move("all", dx, dy)
    if map_state.get("gerber_sig") is not None:
        # the image has moved with the pan; keep it until the drag ends
        map_state["gerber_sig"] = _gerber_signature()


def _map_pan_end(event=None):
    map_state.pop("drag", None)
    start = map_state.pop("drag_from", None)
    idx = map_state.pop("press_index", None)
    if gerber_state["layers"]:
        _map_draw_gerber()
    # a right-click that did not drag, on a part: the part's menu
    if event is not None and start is not None and idx is not None \
            and abs(event.x - start[0]) + abs(event.y - start[1]) < 5:
        _map_part_menu(event, idx)


def delete_rows_where(match, title, what):
    """Deletes every Pick & Place line `match` accepts, after asking. Returns the count."""
    global rows
    hit = [r for r in rows if match(r)]
    if not hit:
        return 0
    names = ", ".join(str(r[0]) for r in hit[:12]) + (" ..." if len(hit) > 12 else "")
    if not messagebox.askyesno(title, "Delete %s?\n\n%d component(s): %s"
                                      % (what, len(hit), names), icon="warning"):
        return 0
    gone = {id(r) for r in hit}
    rows[:] = [r for r in rows if id(r) not in gone]
    map_state["selected"] = None
    refresh_table()
    return len(hit)


def _map_part_menu(event, idx):
    if idx >= len(rows):
        return
    r = rows[idx]
    _map_select(idx)
    des, value, fp = str(r[0]), str(r[10]), str(r[1])
    n_val = sum(1 for x in rows if str(x[10]) == value)
    n_fp = sum(1 for x in rows if str(x[1]) == fp)
    menu = tk.Menu(root, tearoff=0, bg=entry_bg, fg=fg_color,
                   activebackground=btn_bg, activeforeground=btn_fg)
    menu.add_command(label="Edit %s..." % des, command=lambda: _map_open_editor(idx))
    menu.add_command(label="Turn +90", command=lambda: _map_rotate(90))
    menu.add_command(label="Turn -90", command=lambda: _map_rotate(-90))
    menu.add_separator()
    menu.add_command(label="Delete %s" % des, command=lambda: delete_rows_where(
        lambda x: x is r, "Delete Component", "component %s" % des))
    menu.add_command(label="Delete every %s  (%d)" % (value, n_val),
                     command=lambda: delete_rows_where(
                         lambda x: str(x[10]) == value, "Delete Part", "every %s" % value))
    menu.add_command(label="Delete everything on footprint %s  (%d)" % (fp, n_fp),
                     command=lambda: delete_rows_where(
                         lambda x: str(x[1]) == fp, "Delete Footprint",
                         "everything on footprint %s" % fp))
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        menu.grab_release()


def _map_delete_selected(event=None):
    i = _map_selected_index()
    if i is not None:
        r = rows[i]
        delete_rows_where(lambda x: x is r, "Delete Component", "component %s" % r[0])


def _map_reset_view(event=None):
    map_state["zoom"] = 1.0
    map_state["pan"] = (0.0, 0.0)
    map_state["frame"] = None
    render_pcb_preview()


# 1. Variant Table
var_container, variant_tree = create_sub_tree(
    bottom_frame, ("Part Name", "Qty", "Height"), (150, 45, 60))
variant_tree.bind("<Double-1>", on_part_double_click)
var_container.pack(side="left", padx=(0, 10))

# 2. Footprint Table
foot_container, footprint_tree = create_sub_tree(
    bottom_frame, ("Footprint Name", "Qty", "Height"), (150, 45, 60))
foot_container.pack(side="left", padx=10)

# 3. Part Editor Group
edit_lf = tk.LabelFrame(bottom_frame, text=" Edit Library ", bg=bg_color, fg=frame_title_color, font=("Segoe UI", 10, "bold"), padx=10, pady=5)
edit_lf.pack(side="left", fill="y", padx=2)

create_label(edit_lf, "Rename to:").pack(anchor="w")
rename_entry = create_entry(edit_lf, width=25)
rename_entry.pack(pady=3)

edit_btn_frame = tk.Frame(edit_lf, bg=bg_color)
edit_btn_frame.pack(fill="x", pady=5)
create_btn(edit_btn_frame, "Rename Part", rename_part, width=10).pack(side="left", padx=2)
create_btn(edit_btn_frame, "Del Part", remove_part, width=10).pack(side="left", padx=2)
create_btn(edit_btn_frame, "Del Footprint", remove_footprint, width=10).pack(side="left", padx=2)
create_btn(edit_lf, "Find Similar Names", open_similar_names).pack(fill="x", pady=(2, 0))

# 4. Fiducial Group
fid_lf = tk.LabelFrame(bottom_frame, text=" Fiducial Setting ", bg=bg_color, fg=frame_title_color, font=("Segoe UI", 10, "bold"), padx=10, pady=5)
fid_lf.pack(side="left", fill="y", padx=10)

f_grid = tk.Frame(fid_lf, bg=bg_color)
f_grid.pack(pady=5)
create_label(f_grid, "X (mm):").grid(row=0, column=0, sticky="e", padx=2, pady=2)
fid_x = create_entry(f_grid, width=10)
fid_x.grid(row=0, column=1, padx=2, pady=2)

create_label(f_grid, "Y (mm):").grid(row=1, column=0, sticky="e", padx=2, pady=2)
fid_y = create_entry(f_grid, width=10)
fid_y.grid(row=1, column=1, padx=2, pady=2)

create_btn(fid_lf, "Add FIDUCIAL", add_FIDUCIAL).pack(fill="x", pady=5)

# 5. Statistics Group
stat_lf = tk.LabelFrame(bottom_frame, text=" Statistics ", bg=bg_color, fg=frame_title_color, font=("Segoe UI", 10, "bold"), padx=10, pady=5)
stat_lf.pack(side="left", fill="both", expand=True, padx=10)

total_label = tk.Label(stat_lf, text="Total Components: 0", font=("Segoe UI", 11, "bold"), bg=bg_color, fg="#5ce65c")
total_label.pack(anchor="w", pady=5)

footprints_label = tk.Label(stat_lf, text="Different Footprints: 0", font=("Segoe UI", 11, "bold"), bg=bg_color, fg="#ffcc00")
footprints_label.pack(anchor="w", pady=5)

boms_label = tk.Label(stat_lf, text="Different BOMs: 0", font=("Segoe UI", 11, "bold"), bg=bg_color, fg="#ff6666")
boms_label.pack(anchor="w", pady=5)
# =========================================================
# Machine File: model, feeder setup, and placement ordering
# =========================================================
#
# Layout of the machine file (as exported/imported by the machine):
#
#   #Feeder,...          header naming the first 17 stack columns
#   stack,...            one row per feeder  (26 columns: 17 named + 9 unnamed)
#   pcb,... mark,...     board / fiducial / mirror setup rows
#   #SMD,...             header for the placement section
#   comp,...             one row per component, in placement order
#
# Every row is kept as its exact list of fields so that untouched values are
# written back character for character. Only fields the user edits change.

MACHINE_RECORD_ORDER_NOTE = (
    "Rows are exported in exactly the order shown here."
)

# ---- Feeder columns -------------------------------------------------------
# (field index, label, kind). Names come from the file's own #Feeder header.
FEEDER_COLS = [
    (1,  "Feeder ID",         "int"),
    (2,  "Type",              "int"),
    (3,  "Nozzles",           "nozzle"),
    (4,  "X (mm)",            "float"),
    (5,  "Y (mm)",            "float"),
    (6,  "Angle (deg)",       "float"),
    (7,  "Footprint",         "text"),
    (8,  "Part (Footprint/Value)", "part"),
    (9,  "Pick Height (mm)",  "float"),
    (10, "Pick Delay (ms)",   "int"),
    (11, "Place Height (mm)", "float"),
    (12, "Place Delay (ms)",  "int"),
    (13, "Vacuum Detection",  "yesno"),
    (14, "Threshold",         "int"),
    (15, "Vision Alignment",  "int"),
    (16, "Speed (%)",         "int"),
]

# The #Feeder header names 17 columns but every stack row carries 26. These are
# the remaining ones: editable, exported verbatim, and labelled by their column
# number in the file. The hint is only what this project's own file contains --
# confirm each against the machine's own screen before relying on it.
FEEDER_EXTRA_COLS = [
    (17, "Feed Rhythm (mm)", "tape step: 4 for 8mm tape (set by default), 8 for wider"),
    (18, "Feeder Power", "set on the machine; default from Options"),
    (19, "Peeler Power", "set on the machine; default from Options"),
    (20, "Col 21", "values seen in file: Yes / No"),
    (21, "Col 22", "values seen in file: No"),
    (22, "Col 23", "values seen in file: -40"),
    (23, "Col 24", "values seen in file: -40"),
    (24, "Col 25", "values seen in file: -40"),
    (25, "Col 26", "values seen in file: -40"),
]

FEEDER_TABLE_COLS = [1, 8, 3, 4, 5, 6, 9, 11, 10, 12, 16, 17, 18, 19]
FEEDER_TAG_COLOURS = {"fid": "#e0a040", "bank": "#e8e8e8", "big": "#7fc8d8",
                      "later": "#8a8a8a", "empty": "#707070", "warn": "#ff6b5b"}
# short headings so none is cut off; every column is still in the editor
FEEDER_TABLE_HEADINGS = {
    1: ("ID", 50), 8: ("Part (Footprint/Value)", 210), 3: ("Nozzles", 65),
    4: ("Pick X", 75), 5: ("Pick Y", 75), 6: ("Angle", 60), 9: ("Pick H", 65),
    11: ("Place H", 70), 10: ("Pick ms", 65), 12: ("Place ms", 70), 16: ("Speed %", 70),
    17: ("Feed", 50), 18: ("Feeder Pwr", 80), 19: ("Peeler Pwr", 80),
}

# ---- Component columns (the #SMD section) ---------------------------------
COMP_COLS = [
    (1, "Feeder ID", "int"),
    (2, "Nozzle",    "int"),
    (3, "Name",      "text"),
    (4, "Value",     "text"),
    (5, "Footprint", "text"),
    (6, "X (mm)",    "float"),
    (7, "Y (mm)",    "float"),
    (8, "Rotation",  "float"),
    (9, "Skip",      "yesno"),
]

machine_model = {
    "feeder_header": [],   # the '#Feeder,...' row
    "pre_records": [],     # every row before '#SMD', in file order (stack, pcb, mark, ...)
    "smd_header": [],      # the '#SMD,...' row
    "comps": [],           # every row after '#SMD'
    "post_records": [],    # rows belonging to any later '#...' section
    "path": None,
}

# Treeview item id -> the exact field list it displays. Values read back out of
# a Treeview are re-typed by Tcl ("242.80" -> 242.8, "0603" -> 603), so the
# widget is display-only and every edit goes through this map.
machine_items = {}


def machine_item_id(fields):
    """Stable item id for a row: the row list is kept alive by machine_model."""
    return "r%d" % id(fields)


def machine_feeders():
    return [r for r in machine_model["pre_records"] if r and r[0] == "stack"]


def machine_board_records():
    return [r for r in machine_model["pre_records"] if r and r[0] != "stack"]


def machine_is_loaded():
    return bool(machine_model["smd_header"] or machine_model["pre_records"])


def field_at(fields, index, default=""):
    return fields[index] if index < len(fields) else default


def set_field(fields, index, value):
    """Assigns a field, padding the row if it is shorter than expected."""
    while len(fields) <= index:
        fields.append("")
    fields[index] = value


# ---- Nozzle sets ----------------------------------------------------------
# The Nozzle field is a set of digits, not a number: "13" means nozzles 1 and 3
# are both enabled for that feeder, "1" means only nozzle 1. Verified against
# the nozzle numbers the comp rows of each feeder actually use.
def parse_nozzles(value):
    text = str(value).strip()
    if text and all(ch in "1234" for ch in text):
        return sorted({int(ch) for ch in text})
    return None


def format_nozzles(nozzles):
    return "".join(str(n) for n in sorted(set(nozzles)))


def machine_part_choices():
    """'Footprint/Value' strings, gathered from the components in the file."""
    choices = set()
    for c in machine_model["comps"]:
        footprint = str(field_at(c, 5)).strip()
        value = str(field_at(c, 4)).strip()
        if footprint or value:
            choices.add("%s/%s" % (footprint, value))
    for f in machine_feeders():
        part = str(field_at(f, 8)).strip()
        if part:
            choices.add(part)
    return sorted(choices)


def machine_footprints():
    """Distinct footprints across the components, for the package library."""
    found = []
    for c in machine_model["comps"]:
        footprint = str(field_at(c, 5)).strip()
        if footprint and footprint not in found:
            found.append(footprint)
    return sorted(found)


def part_match_key(value):
    """
    Key for matching a part against a feeder, given as "footprint/value" or as
    a bare footprint. Case never distinguishes two parts, and the footprint half
    is cleaned the same way the import cleans it -- a machine file built before
    the clean-up still says "SMD-0603C" where the board now says "0603".
    """
    text = str(value)
    if "/" in text:
        fp, rest = text.split("/", 1)
        text = clean_footprint(fp) + "/" + rest
    else:
        text = clean_footprint(text)
    return "".join(text.split()).casefold()


def feeder_footprint(feeder):
    """The footprint half of a feeder's 'Footprint/Value' field."""
    part = str(field_at(feeder, 8)).strip()
    return part.split("/", 1)[0].strip() if part else ""


# =====================================
# Machine file: import / export
# =====================================
def import_machine_file():
    filepath = filedialog.askopenfilename(
        title="Select Machine File",
        filetypes=(("CSV Files", "*.csv"), ("All Files", "*.*"))
    )
    if not filepath:
        return

    feeder_header = []
    pre_records = []
    smd_header = []
    comps = []
    post_records = []

    try:
        with open(filepath, 'r', newline='', encoding='utf-8') as file:
            section = "pre"
            for row in csv.reader(file):
                if not row:
                    continue
                first = row[0].strip()

                if first.startswith("#Feeder"):
                    feeder_header = row
                    continue
                if first.startswith("#SMD"):
                    smd_header = row
                    section = "comp"
                    continue
                if first.startswith("#"):
                    # Any further section header ends the component list
                    section = "post"
                    post_records.append(row)
                    continue

                if section == "pre":
                    pre_records.append(row)
                elif section == "comp":
                    comps.append(row)
                else:
                    post_records.append(row)
    except Exception as e:
        messagebox.showerror("Import Machine File", "Error importing machine file:\n\n%s" % e)
        return

    if not smd_header:
        messagebox.showwarning(
            "Import Machine File",
            "No '#SMD' section found in this file.\nNothing was loaded."
        )
        return

    machine_model["feeder_header"] = feeder_header
    machine_model["pre_records"] = pre_records
    machine_model["smd_header"] = smd_header
    machine_model["comps"] = comps
    machine_model["post_records"] = post_records
    machine_model["path"] = filepath
    machine_model["linked"] = False
    machine_model["stage1"] = []

    refresh_feeder_table()
    refresh_machine_comp_table()
    refresh_board_table()
    machine_path_label.config(text=filepath, fg="#5ce65c")

    messagebox.showinfo(
        "Import Machine File",
        "Loaded %d feeders and %d components." % (len(machine_feeders()), len(comps))
    )


def export_machine_table():
    """
    Writes the machine file back out: the feeder header and every pre-#SMD row
    in order, then the '#SMD' header, then the components in the order shown in
    the Components table, then any trailing sections.
    """
    if not machine_is_loaded():
        messagebox.showerror("Export Machine File", "Import a machine file first.")
        return

    output_path = filedialog.asksaveasfilename(
        title="Save Machine File",
        defaultextension=".csv",
        filetypes=(("CSV Files", "*.csv"), ("All Files", "*.*"))
    )
    if not output_path:
        return

    try:
        with open(output_path, 'w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            pin_fiducial_feeder_first()
            if machine_model["feeder_header"]:
                writer.writerow(machine_model["feeder_header"])
            for row in machine_model["pre_records"]:
                writer.writerow(row)
            writer.writerow(machine_model["smd_header"])
            pin_fiducials_first()
            for row in machine_model["comps"]:
                writer.writerow(row)
            for row in machine_model["post_records"]:
                writer.writerow(row)
    except Exception as e:
        messagebox.showerror("Export Machine File", "Error writing machine file:\n\n%s" % e)
        return

    messagebox.showinfo("Export Machine File", "File saved to:\n%s" % output_path)


# =====================================
# Shared editor dialog
# =====================================
def open_field_editor(title, fields, spec, extra_spec=None, part_choices=None,
                      on_saved=None, on_delete=None):
    """
    Builds an edit dialog for one raw record.

    `spec` is a list of (field index, label, kind); `extra_spec` is a list of
    (field index, label, hint) rendered as plain text entries. Only the fields
    present in the dialog are written back, so unlisted columns stay untouched.
    """
    popup = tk.Toplevel(root)
    popup.title(title)
    popup.configure(bg=bg_color)
    popup.transient(root)

    outer = tk.Frame(popup, bg=bg_color, padx=15, pady=15)
    outer.pack(fill="both", expand=True)

    widgets = {}   # field index -> ("kind", accessor)

    main_lf = tk.LabelFrame(outer, text=" Settings ", bg=bg_color, fg=frame_title_color,
                            font=("Segoe UI", 9, "bold"), padx=10, pady=8)
    main_lf.pack(fill="both", expand=True)

    for row_i, (index, label, kind) in enumerate(spec):
        col = 0 if row_i < (len(spec) + 1) // 2 else 2
        grid_row = row_i if col == 0 else row_i - (len(spec) + 1) // 2

        create_label(main_lf, label + ":").grid(row=grid_row, column=col, sticky="e", padx=5, pady=3)
        current = field_at(fields, index)

        if kind == "nozzle":
            holder = tk.Frame(main_lf, bg=bg_color)
            holder.grid(row=grid_row, column=col + 1, sticky="w", padx=5, pady=3)
            selected = parse_nozzles(current)
            if selected is None:
                # Unrecognised encoding: never guess, edit it as raw text.
                var = tk.StringVar(value=str(current))
                create_entry(holder, width=14, var=var).pack(side="left")
                tk.Label(holder, text=" (raw)", bg=bg_color, fg="#ff9900",
                         font=("Segoe UI", 8)).pack(side="left")
                widgets[index] = ("text", var)
            else:
                nozzle_vars = {}
                for n in (1, 2, 3, 4):
                    v = tk.BooleanVar(value=n in selected)
                    nozzle_vars[n] = v
                    tk.Checkbutton(holder, text=str(n), variable=v, bg=bg_color, fg=fg_color,
                                   selectcolor=tree_bg, activebackground=bg_color,
                                   activeforeground=fg_color).pack(side="left")
                widgets[index] = ("nozzle", nozzle_vars)

        elif kind == "yesno":
            var = tk.StringVar(value=str(current))
            combo = ttk.Combobox(main_lf, textvariable=var, values=["Yes", "No"],
                                 state="readonly", width=12)
            combo.grid(row=grid_row, column=col + 1, sticky="w", padx=5, pady=3)
            widgets[index] = ("text", var)

        elif kind == "part":
            var = tk.StringVar(value=str(current))
            combo = ttk.Combobox(main_lf, textvariable=var, values=part_choices or [], width=30)
            combo.grid(row=grid_row, column=col + 1, sticky="w", padx=5, pady=3)
            widgets[index] = ("text", var)

        else:
            var = tk.StringVar(value=str(current))
            create_entry(main_lf, width=16, var=var).grid(row=grid_row, column=col + 1,
                                                          sticky="w", padx=5, pady=3)
            widgets[index] = (kind, var)

    if extra_spec:
        extra_lf = tk.LabelFrame(
            outer,
            text=" Unnamed columns (exported unchanged unless you edit them) ",
            bg=bg_color, fg="#ff9900", font=("Segoe UI", 9, "bold"), padx=10, pady=8)
        extra_lf.pack(fill="x", pady=(12, 0))

        tk.Label(extra_lf,
                 text="The file's #Feeder header only names the first 17 columns. "
                      "These are the rest, by column number.",
                 bg=bg_color, fg=fg_color, font=("Segoe UI", 8, "italic"),
                 wraplength=640, justify="left").grid(row=0, column=0, columnspan=6,
                                                      sticky="w", pady=(0, 6))

        for i, (index, label, hint) in enumerate(extra_spec):
            grid_row = 1 + i // 3
            col = (i % 3) * 2
            create_label(extra_lf, label + ":").grid(row=grid_row, column=col,
                                                     sticky="e", padx=5, pady=3)
            var = tk.StringVar(value=str(field_at(fields, index)))
            entry = create_entry(extra_lf, width=12, var=var)
            entry.grid(row=grid_row, column=col + 1, sticky="w", padx=5, pady=3)
            _hint_tooltip(entry, hint)
            widgets[index] = ("text", var)

    btn_frame = tk.Frame(outer, bg=bg_color)
    btn_frame.pack(fill="x", pady=(15, 0))

    def apply_changes():
        staged = {}
        for index, (kind, accessor) in widgets.items():
            if kind == "nozzle":
                chosen = [n for n, v in accessor.items() if v.get()]
                if not chosen:
                    messagebox.showerror("Invalid Nozzles",
                                         "At least one nozzle must be selected.")
                    return
                staged[index] = format_nozzles(chosen)
                continue

            text = accessor.get().strip()
            if kind == "int":
                try:
                    int(float(text))
                except ValueError:
                    messagebox.showerror("Invalid Value",
                                         "'%s' is not a valid number." % text)
                    return
            elif kind == "float":
                try:
                    float(text)
                except ValueError:
                    messagebox.showerror("Invalid Value",
                                         "'%s' is not a valid number." % text)
                    return
            staged[index] = text

        for index, value in staged.items():
            set_field(fields, index, value)

        popup.destroy()
        if on_saved:
            on_saved()

    if on_delete:
        tk.Button(btn_frame, text="Delete", command=lambda: (popup.destroy(), on_delete()),
                  bg="#d9534f", fg="white", width=14,
                  font=("Segoe UI", 9, "bold")).pack(side="left")

    tk.Button(btn_frame, text="Cancel", command=popup.destroy, bg=entry_bg, fg=fg_color,
              width=12, font=("Segoe UI", 9)).pack(side="right", padx=(8, 0))
    tk.Button(btn_frame, text="Save / Apply", command=apply_changes, bg=btn_bg, fg=btn_fg,
              width=14, font=("Segoe UI", 9, "bold")).pack(side="right")


def _hint_tooltip(widget, text):
    """Small hover hint, used for the columns the file header does not name."""
    tip = {"win": None}

    def show(event):
        if tip["win"] or not text:
            return
        win = tk.Toplevel(widget)
        win.wm_overrideredirect(True)
        win.wm_geometry("+%d+%d" % (event.x_root + 12, event.y_root + 12))
        tk.Label(win, text=text, bg="#333333", fg="#ffffff", font=("Segoe UI", 8),
                 padx=6, pady=3).pack()
        tip["win"] = win

    def hide(event):
        if tip["win"]:
            tip["win"].destroy()
            tip["win"] = None

    widget.bind("<Enter>", show)
    widget.bind("<Leave>", hide)


# =====================================
# Feeder table
# =====================================
def feeder_is_fiducial(f):
    return "FIDUCIAL" in str(field_at(f, 8)).upper()


def pin_fiducial_feeder_first():
    """
    Puts the fiducial's pseudo-feeder at the head of the feeder rows, as in the
    machine file known to work. Other records keep their places. Returns True
    if anything moved.
    """
    pre = machine_model["pre_records"]
    fids = [r for r in pre if r and r[0] == "stack" and feeder_is_fiducial(r)]
    if not fids:
        return False
    first_stack = next((i for i, r in enumerate(pre) if r and r[0] == "stack"), None)
    if first_stack is not None and all(pre[first_stack + k] is fids[k] for k in range(len(fids))
                                       if first_stack + k < len(pre)):
        return False
    rest = [r for r in pre if not any(r is f for f in fids)]
    at = next((i for i, r in enumerate(rest) if r and r[0] == "stack"), len(rest))
    pre[:] = rest[:at] + fids + rest[at:]
    return True


def overlapping_feeders(tolerance=3.0):
    """Ids of feeders whose pick point sits on another's: not set up on the machine."""
    pts = []
    for f in machine_feeders():
        if feeder_is_fiducial(f):
            continue
        try:
            pts.append((str(field_at(f, 1)), float(field_at(f, 4)), float(field_at(f, 5))))
        except ValueError:
            continue
    bad = set()
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            if max(abs(pts[i][1] - pts[j][1]), abs(pts[i][2] - pts[j][2])) < tolerance:
                bad.update((pts[i][0], pts[j][0]))
    return bad


def feeder_row_tag(f, overlap):
    if feeder_is_fiducial(f):
        return "fid"
    if str(field_at(f, 1)) in overlap:
        return "warn"
    part = str(field_at(f, 8)).strip()
    if not part:
        return "empty"
    eight = tape_width_for(part.split("/", 1)[0]) == 8
    pinned = machine_model.get("stage1")
    if pinned:                            # the layout taken from the machine
        if part_match_key(part) in set(pinned):
            return "bank" if eight else "big"
        return "later" if eight else "big"
    if eight:
        # in the bank, or an 8mm part the bank had no room for (a later stage)
        return "bank" if str(field_at(f, 1)) in EIGHT_MM_BANK else "later"
    return "big"


def refresh_feeder_table():
    pin_fiducial_feeder_first()
    feeder_tree.delete(*feeder_tree.get_children())
    overlap = overlapping_feeders()
    for f in machine_feeders():
        item_id = machine_item_id(f)
        machine_items[item_id] = f
        feeder_tree.insert("", "end", iid=item_id,
                           values=[field_at(f, i) for i in FEEDER_TABLE_COLS],
                           tags=(feeder_row_tag(f, overlap),))
    feeder_count_label.config(text="Feeders: %d" % len(machine_feeders()))


def selected_feeder():
    sel = feeder_tree.selection()
    if not sel:
        return None
    return machine_items.get(sel[0])


def selected_feeders():
    return [machine_items[i] for i in feeder_tree.selection() if i in machine_items]


def group_edit_fields():
    """(field index, label, kind) a group edit may set: never a slot's id or place."""
    out = [(i, label, kind) for i, label, kind in FEEDER_COLS if i not in (1, 4, 5, 7, 8)]
    out += [(i, label, "text") for i, label, _ in FEEDER_EXTRA_COLS]
    return out


def group_edit_feeders(feeders, index, value):
    """Sets one field on many feeders. Returns how many changed."""
    n = 0
    for f in feeders:
        if str(field_at(f, index)) != value:
            set_field(f, index, value)
            n += 1
    return n


def open_group_edit():
    if not machine_feeders():
        messagebox.showinfo("Group Edit", "There are no feeders to edit.")
        return
    chosen = [f for f in selected_feeders() if not feeder_is_fiducial(f)]
    popup = tk.Toplevel(root)
    popup.title("Group Edit Feeders")
    popup.configure(bg=bg_color)
    popup.transient(root)
    frame = tk.Frame(popup, bg=bg_color, padx=15, pady=15)
    frame.pack(fill="both", expand=True)

    fields = group_edit_fields()
    labels = [label for _, label, _ in fields]
    create_label(frame, "Setting:").grid(row=0, column=0, sticky="e", padx=5, pady=5)
    field_var = tk.StringVar(value=next((l for i, l, _ in fields if i == 11), labels[0]))
    ttk.Combobox(frame, textvariable=field_var, values=labels, state="readonly",
                 width=26).grid(row=0, column=1, sticky="w", padx=5, pady=5)
    create_label(frame, "New value:").grid(row=1, column=0, sticky="e", padx=5, pady=5)
    value_var = tk.StringVar()
    create_entry(frame, width=14, var=value_var).grid(row=1, column=1, sticky="w", padx=5, pady=5)

    create_label(frame, "Apply to:").grid(row=2, column=0, sticky="ne", padx=5, pady=5)
    scope_var = tk.StringVar(value="selected" if chosen else "8mm")
    scopes = tk.Frame(frame, bg=bg_color)
    scopes.grid(row=2, column=1, sticky="w", padx=5, pady=5)
    for text, val in (("Selected feeders (%d)" % len(chosen), "selected"),
                      ("All 8mm feeders", "8mm"),
                      ("All feeders except the fiducial", "all")):
        tk.Radiobutton(scopes, text=text, variable=scope_var, value=val, bg=bg_color,
                       fg=fg_color, selectcolor=tree_bg, activebackground=bg_color,
                       activeforeground=fg_color,
                       state="normal" if (val != "selected" or chosen) else "disabled"
                       ).pack(anchor="w")

    def apply():
        index, label, kind = fields[labels.index(field_var.get())]
        value = value_var.get().strip()
        if kind in ("float", "int"):
            try:
                v = float(value)
            except ValueError:
                messagebox.showerror("Group Edit", "'%s' is not a number." % value)
                return
            value = ("%.2f" % v) if kind == "float" else str(int(round(v)))
        elif kind == "nozzle":
            nz = parse_nozzles(value)
            if not nz:
                messagebox.showerror("Group Edit", "Nozzles are digits 1-4, e.g. 13.")
                return
            value = format_nozzles(nz)
        elif kind == "yesno":
            value = "Yes" if value.lower() in ("yes", "y", "1", "true") else "No"
        scope = scope_var.get()
        if scope == "selected":
            targets = chosen
        else:
            targets = [f for f in machine_feeders() if not feeder_is_fiducial(f)]
            if scope == "8mm":
                targets = [f for f in targets
                           if tape_width_for(str(field_at(f, 8)).split("/", 1)[0]) == 8
                           or (not str(field_at(f, 8)).strip() and str(field_at(f, 1)) in EIGHT_MM_BANK)]
        n = group_edit_feeders(targets, index, value)
        refresh_feeder_table()
        popup.destroy()
        messagebox.showinfo("Group Edit", "%s set to %s on %d feeder(s)." % (label, value, n))

    tk.Button(frame, text="Apply", command=apply, bg=btn_bg, fg=btn_fg, width=12,
              font=("Segoe UI", 9, "bold")).grid(row=3, column=1, sticky="e", pady=(12, 0))


def edit_selected_feeder(event=None):
    feeder = selected_feeder()
    if feeder is None:
        messagebox.showinfo("Edit Feeder", "Select a feeder first.")
        return

    def delete_feeder():
        if not messagebox.askyesno(
                "Delete Feeder",
                "Delete feeder %s?\n\nComponents still assigned to it will keep "
                "that Feeder ID." % field_at(feeder, 1)):
            return
        machine_model["pre_records"].remove(feeder)
        refresh_feeder_table()

    open_field_editor(
        "Feeder %s" % field_at(feeder, 1),
        feeder,
        FEEDER_COLS,
        extra_spec=FEEDER_EXTRA_COLS,
        part_choices=machine_part_choices(),
        on_saved=refresh_feeder_table,
        on_delete=delete_feeder,
    )


def add_feeder():
    if not machine_is_loaded():
        messagebox.showerror("Add Feeder", "Import a machine file first.")
        return

    existing = machine_feeders()
    if existing:
        template = list(existing[-1])
        used = set()
        for f in existing:
            try:
                used.add(int(field_at(f, 1)))
            except ValueError:
                continue
        next_id = 1
        while next_id in used:
            next_id += 1
        set_field(template, 1, str(next_id))
        set_field(template, 8, "")
        insert_at = machine_model["pre_records"].index(existing[-1]) + 1
    else:
        # No feeder to copy: build a row as wide as the header describes.
        template = ["stack"] + [""] * 25 + [""]
        set_field(template, 1, "1")
        set_field(template, 3, "1")
        insert_at = 0

    machine_model["pre_records"].insert(insert_at, template)
    refresh_feeder_table()
    feeder_tree.selection_set(machine_item_id(template))
    feeder_tree.see(machine_item_id(template))
    edit_selected_feeder()


# =====================================
# Package height library
# =====================================
def open_package_library():
    """
    Sets pick height, place height and speed per package, across every feeder
    carrying that footprint at once.
    """
    if not machine_is_loaded():
        messagebox.showerror("Package Library", "Import a machine file first.")
        return

    popup = tk.Toplevel(root)
    popup.title("Package Height Library")
    popup.geometry("760x480")
    popup.configure(bg=bg_color)
    popup.transient(root)

    tk.Label(popup,
             text="Pick / Place height and speed are stored per feeder. "
                  "Set them here once per package and apply to every feeder that carries it.",
             bg=bg_color, fg=fg_color, font=("Segoe UI", 9), wraplength=720,
             justify="left").pack(anchor="w", padx=15, pady=(12, 8))

    table_frame = tk.Frame(popup, bg=bg_color)
    table_frame.pack(fill="both", expand=True, padx=15)

    cols = ("Footprint", "Feeders", "Components", "Pick Height", "Place Height", "Speed")
    scroll = ttk.Scrollbar(table_frame, orient="vertical")
    scroll.pack(side="right", fill="y")
    pkg_tree = ttk.Treeview(table_frame, columns=cols, show="headings",
                            yscrollcommand=scroll.set)
    scroll.config(command=pkg_tree.yview)
    for c, w in zip(cols, (240, 70, 90, 100, 100, 70)):
        pkg_tree.heading(c, text=c)
        pkg_tree.column(c, width=w, anchor="w")
    pkg_tree.pack(side="left", fill="both", expand=True)

    def feeders_for(footprint):
        key = part_match_key(footprint)
        return [f for f in machine_feeders()
                if part_match_key(feeder_footprint(f)) == key]

    def comps_for(footprint):
        key = part_match_key(footprint)
        return [c for c in machine_model["comps"]
                if part_match_key(field_at(c, 5)) == key]

    def load_packages():
        pkg_tree.delete(*pkg_tree.get_children())
        for footprint in machine_footprints():
            fs = feeders_for(footprint)
            pick = sorted({str(field_at(f, 9)) for f in fs})
            place = sorted({str(field_at(f, 11)) for f in fs})
            speed = sorted({str(field_at(f, 16)) for f in fs})
            pkg_tree.insert("", "end", iid=tv_key(footprint), values=(
                footprint,
                len(fs),
                len(comps_for(footprint)),
                " / ".join(pick) if pick else "-",
                " / ".join(place) if place else "-",
                " / ".join(speed) if speed else "-",
            ))

    load_packages()

    form = tk.LabelFrame(popup, text=" Apply to selected package ", bg=bg_color,
                         fg=frame_title_color, font=("Segoe UI", 9, "bold"),
                         padx=10, pady=8)
    form.pack(fill="x", padx=15, pady=12)

    pick_var = tk.StringVar()
    place_var = tk.StringVar()
    speed_var = tk.StringVar()

    for col, (label, var) in enumerate([("Pick Height (mm):", pick_var),
                                        ("Place Height (mm):", place_var),
                                        ("Speed (%):", speed_var)]):
        create_label(form, label).grid(row=0, column=col * 2, sticky="e", padx=5, pady=5)
        create_entry(form, width=10, var=var).grid(row=0, column=col * 2 + 1,
                                                   sticky="w", padx=5, pady=5)

    status = tk.Label(form, text="Blank fields are left unchanged.", bg=bg_color,
                      fg="#ff9900", font=("Segoe UI", 8, "italic"))
    status.grid(row=1, column=0, columnspan=6, sticky="w", padx=5)

    def on_select(event=None):
        footprint = tv_selected_key(pkg_tree)
        if footprint is None:
            return
        fs = feeders_for(footprint)
        if len(fs) == 1:
            pick_var.set(str(field_at(fs[0], 9)))
            place_var.set(str(field_at(fs[0], 11)))
            speed_var.set(str(field_at(fs[0], 16)))

    pkg_tree.bind("<<TreeviewSelect>>", on_select)

    def apply_package():
        footprint = tv_selected_key(pkg_tree)
        if footprint is None:
            messagebox.showinfo("Package Library", "Select a package first.")
            return

        updates = []
        for index, var, label in ((9, pick_var, "Pick Height"),
                                  (11, place_var, "Place Height"),
                                  (16, speed_var, "Speed")):
            text = var.get().strip()
            if not text:
                continue
            try:
                float(text)
            except ValueError:
                messagebox.showerror("Invalid Value",
                                     "%s: '%s' is not a valid number." % (label, text))
                return
            updates.append((index, text))

        if not updates:
            messagebox.showinfo("Package Library", "Nothing to apply -- all fields are blank.")
            return

        targets = feeders_for(footprint)
        if not targets:
            messagebox.showwarning(
                "Package Library",
                "No feeder carries footprint '%s'.\n\nSet a feeder's "
                "'Part (Footprint/Value)' field first." % footprint)
            return

        for f in targets:
            for index, text in updates:
                set_field(f, index, text)

        load_packages()
        refresh_feeder_table()
        status.config(text="Applied to %d feeder(s) carrying '%s'." % (len(targets), footprint))

    tk.Button(form, text="Apply to Package", command=apply_package, bg=btn_bg, fg=btn_fg,
              font=("Segoe UI", 9, "bold"), relief="flat", padx=12,
              pady=3).grid(row=0, column=6, padx=15)


# =====================================
# Component table and placement order
# =====================================
def comp_is_fiducial(c):
    return (str(field_at(c, 3)).strip().upper().startswith("FID")
            or "FIDUCIAL" in str(field_at(c, 4)).upper()
            or "FIDUCIAL" in str(field_at(c, 5)).upper())


def pin_fiducials_first(comps=None):
    """
    The machine reads the fiducials before it places anything, so they always
    lead the placement list -- whatever order the rest was sorted or dragged
    into. Several fiducials keep their order among themselves. Returns True if
    anything moved.
    """
    comps = machine_model["comps"] if comps is None else comps
    fids = [c for c in comps if comp_is_fiducial(c)]
    if not fids:
        return False
    rest = [c for c in comps if not comp_is_fiducial(c)]
    new = fids + rest
    moved = any(a is not b for a, b in zip(new, comps))
    comps[:] = new
    return moved


def refresh_machine_comp_table(select=None):
    # every view of the program, and so every edit, shows fiducials first
    pin_fiducials_first()
    machine_comp_tree.delete(*machine_comp_tree.get_children())
    for c in machine_model["comps"]:
        item_id = machine_item_id(c)
        machine_items[item_id] = c
        machine_comp_tree.insert("", "end", iid=item_id,
                                 values=[field_at(c, i) for i, _, _ in COMP_COLS])
    machine_comp_count_label.config(text="Components: %d" % len(machine_model["comps"]))

    if select:
        keep = [i for i in select if machine_comp_tree.exists(i)]
        if keep:
            machine_comp_tree.selection_set(keep)
            machine_comp_tree.see(keep[0])


def selected_comp_rows():
    return [machine_items[i] for i in machine_comp_tree.selection() if i in machine_items]


def move_comps(direction):
    """direction: -1 up, +1 down, 'top', 'bottom'."""
    comps = machine_model["comps"]
    selected = selected_comp_rows()
    if not selected:
        messagebox.showinfo("Move", "Select one or more components first.")
        return

    positions = sorted(comps.index(r) for r in selected)

    if direction == "top":
        chosen = [comps[p] for p in positions]
        rest = [r for i, r in enumerate(comps) if i not in set(positions)]
        comps[:] = chosen + rest
    elif direction == "bottom":
        chosen = [comps[p] for p in positions]
        rest = [r for i, r in enumerate(comps) if i not in set(positions)]
        comps[:] = rest + chosen
    elif direction < 0:
        if positions[0] == 0:
            return
        for p in positions:
            comps[p - 1], comps[p] = comps[p], comps[p - 1]
    else:
        if positions[-1] == len(comps) - 1:
            return
        for p in reversed(positions):
            comps[p + 1], comps[p] = comps[p], comps[p + 1]

    refresh_machine_comp_table(select=[machine_item_id(r) for r in selected])


def edit_selected_comp(event=None):
    rows_selected = selected_comp_rows()
    if not rows_selected:
        return
    comp = rows_selected[0]

    def delete_comp():
        if not messagebox.askyesno("Delete Component",
                                   "Delete %s?" % field_at(comp, 3)):
            return
        machine_model["comps"].remove(comp)
        refresh_machine_comp_table()

    def saved():
        if push_comp_to_row(comp):
            refresh_table()
        refresh_machine_comp_table()

    open_field_editor(
        "Component %s" % field_at(comp, 3),
        comp,
        COMP_COLS,
        on_saved=saved,
        on_delete=delete_comp,
    )


def toggle_comp_skip():
    rows_selected = selected_comp_rows()
    if not rows_selected:
        messagebox.showinfo("Skip", "Select one or more components first.")
        return
    # If anything in the selection is currently placed, skip the whole selection.
    turning_on = any(str(field_at(r, 9)).strip().lower() != "yes" for r in rows_selected)
    for r in rows_selected:
        set_field(r, 9, "Yes" if turning_on else "No")
    refresh_machine_comp_table(select=[machine_item_id(r) for r in rows_selected])


def assign_feeder_to_selection():
    rows_selected = selected_comp_rows()
    if not rows_selected:
        messagebox.showinfo("Assign Feeder", "Select one or more components first.")
        return

    popup = tk.Toplevel(root)
    popup.title("Assign Feeder / Nozzle")
    popup.configure(bg=bg_color)
    popup.transient(root)

    frame = tk.Frame(popup, bg=bg_color, padx=15, pady=15)
    frame.pack(fill="both", expand=True)

    tk.Label(frame, text="%d component(s) selected" % len(rows_selected), bg=bg_color,
             fg=frame_title_color, font=("Segoe UI", 10, "bold")).grid(
                 row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

    feeder_labels = ["%s  --  %s" % (field_at(f, 1), field_at(f, 8) or "(no part)")
                     for f in machine_feeders()]
    feeder_ids = [str(field_at(f, 1)) for f in machine_feeders()]

    create_label(frame, "Feeder:").grid(row=1, column=0, sticky="e", padx=5, pady=5)
    feeder_var = tk.StringVar()
    feeder_combo = ttk.Combobox(frame, textvariable=feeder_var, values=feeder_labels,
                                state="readonly", width=36)
    feeder_combo.grid(row=1, column=1, sticky="w", padx=5, pady=5)

    create_label(frame, "Nozzle:").grid(row=2, column=0, sticky="e", padx=5, pady=5)
    nozzle_var = tk.StringVar(value="(unchanged)")
    ttk.Combobox(frame, textvariable=nozzle_var,
                 values=["(unchanged)", "1", "2", "3", "4"],
                 state="readonly", width=14).grid(row=2, column=1, sticky="w", padx=5, pady=5)

    def apply_assignment():
        choice = feeder_combo.current()
        if choice < 0 and nozzle_var.get() == "(unchanged)":
            messagebox.showinfo("Assign Feeder", "Nothing selected to apply.")
            return
        for r in rows_selected:
            if choice >= 0:
                set_field(r, 1, feeder_ids[choice])
            if nozzle_var.get() != "(unchanged)":
                set_field(r, 2, nozzle_var.get())
        popup.destroy()
        refresh_machine_comp_table(select=[machine_item_id(r) for r in rows_selected])

    tk.Button(frame, text="Apply", command=apply_assignment, bg=btn_bg, fg=btn_fg,
              width=14, font=("Segoe UI", 9, "bold")).grid(row=3, column=1, sticky="e", pady=(12, 0))


def auto_sort_machine_table():
    """
    Orders the placement by component body height, shortest first, so the head
    never has to reach over a part that is already on the board. Within one
    height, footprints stay grouped to save nozzle changes and each group is
    routed nearest-neighbour. Fiducials stay at the top.
    """
    comps = machine_model["comps"]
    if not comps:
        return

    NAME_IDX, FOOTPRINT_IDX, X_IDX, Y_IDX = 3, 5, 6, 7

    fid_rows = []
    placeable = []
    for r in comps:
        if str(field_at(r, NAME_IDX)).strip().upper().startswith("FID"):
            fid_rows.append(r)
        else:
            placeable.append(r)

    suspects = suspect_part_keys()
    groups = {}
    for r in placeable:
        footprint = str(field_at(r, FOOTPRINT_IDX)).strip() or "Unknown"
        height = component_height(footprint, field_at(r, 4), suspects)
        groups.setdefault((height, footprint), []).append(r)

    # Shortest body first: once a tall part is down, the head can strike it.
    # Within one height, footprints stay together to save nozzle changes.
    order = sorted(groups)

    sorted_rows = []
    for footprint in order:
        unvisited = groups[footprint][:]
        current_x, current_y = 0.0, 0.0
        while unvisited:
            nearest_pos = None
            nearest_dist = float('inf')
            for pos, r in enumerate(unvisited):
                try:
                    dist = math.hypot(float(field_at(r, X_IDX)) - current_x,
                                      float(field_at(r, Y_IDX)) - current_y)
                except ValueError:
                    dist = float('inf')
                if dist < nearest_dist:
                    nearest_dist = dist
                    nearest_pos = pos

            if nearest_pos is None:
                # No usable coordinates left: keep the remainder as-is.
                sorted_rows.extend(unvisited)
                break

            chosen = unvisited.pop(nearest_pos)
            try:
                current_x = float(field_at(chosen, X_IDX))
                current_y = float(field_at(chosen, Y_IDX))
            except ValueError:
                pass
            sorted_rows.append(chosen)

    comps[:] = fid_rows + sorted_rows
    refresh_machine_comp_table()


# =====================================
# Board / vision records
# =====================================
def refresh_board_table():
    board_tree.delete(*board_tree.get_children())
    for r in machine_board_records():
        item_id = machine_item_id(r)
        machine_items[item_id] = r
        board_tree.insert("", "end", iid=item_id,
                          values=(r[0], ", ".join(str(v) for v in r[1:] if v != "")))


def edit_selected_board_record(event=None):
    sel = board_tree.selection()
    if not sel:
        messagebox.showinfo("Edit Record", "Select a row first.")
        return
    record = machine_items.get(sel[0])
    if record is None:
        return

    # These rows are not described by any header in the file, so every field is
    # offered as raw text, labelled by its column number.
    spec = [(i, "Col %d" % (i + 1), "text") for i in range(1, len(record)) if i < len(record)]
    open_field_editor("Record: %s" % record[0], record, spec,
                      on_saved=refresh_board_table)


# =========================================================
# Build a machine file from the Pick & Place table
# =========================================================
#
# Each distinct (Footprint, Value) in the Pick & Place table needs one feeder.
# Feeder slot X/Y are physical positions taught on the machine, so they are
# never invented here: an already-imported machine file is reused as a template
# and keeps its taught slots, its board/mark/mirror rows and its untouched
# columns. Only when there is no template are slots generated from a pitch, and
# those must then be taught on the machine.

# A stack row is 26 fields plus the trailing empty one; a comp row is 10 plus one.
FEEDER_ROW_WIDTH = 27
COMP_ROW_WIDTH = 11

DEFAULT_SMD_HEADER = ["#SMD", "Feeder ID", "Nozzle", "Name", "Value",
                      "Footprint", "X", "Y", "Rotation", "Skip"]

DEFAULT_FEEDER_HEADER = ["#Feeder", "Feeder ID", "Type", "Nozzle", "X", "Y", "Angle",
                         "Footprint", "Value", "Pick height", "Pick delay",
                         "Place Height", "Place Delay", "Vacuum detection", "Threshold",
                         "Vision Alignment", "Speed", ""]

# Used only when there is no template feeder to copy. These mirror the values an
# untuned feeder carries in this project's own machine file rather than being
# invented: heights and delays stay at zero so nothing is silently wrong.
FALLBACK_FEEDER = {
    2: "0", 3: "1", 6: "0.00", 7: "", 9: "0.00", 10: "100", 11: "0.00", 12: "100",
    13: "No", 14: "-40", 15: "1", 16: "80",
}


def _blank_feeder_row():
    row = ["stack"] + [""] * (FEEDER_ROW_WIDTH - 1)
    for index, value in FALLBACK_FEEDER.items():
        row[index] = value
    return row


def _format_machine_angle(value):
    """Machine rotations run -180..180 with two decimals; Altium gives 0..360."""
    try:
        angle = float(value)
    except (ValueError, TypeError):
        return "0.00"
    # (-180, 180], matching the machine's own files: they write 180.00, never -180.00
    angle = angle % 360.0
    if angle > 180.0:
        angle -= 360.0
    return "%.2f" % angle


def _format_machine_coord(value):
    try:
        return "%.2f" % float(value)
    except (ValueError, TypeError):
        return "0.00"


def pnp_rows_in_table_order():
    """The Pick & Place rows in the order currently displayed, sort model included."""
    ordered = []
    for item in tree.get_children():
        try:
            index = int(item)
        except ValueError:
            continue
        if 0 <= index < len(rows):
            ordered.append(rows[index])
    return ordered or list(rows)


def collect_pnp_parts(source_rows):
    """Groups the Pick & Place rows into the distinct parts that each need a feeder."""
    parts = []
    seen = {}
    for r in source_rows:
        key = (str(r[1]).strip(), str(r[10]).strip())
        if key not in seen:
            entry = {"footprint": key[0], "value": key[1], "rows": [],
                     "feeder": "", "nozzles": "1", "pick": "", "place": "",
                     "speed": "", "source": ""}
            seen[key] = entry
            parts.append(entry)
        seen[key]["rows"].append(r)
    return parts


# --------------------------------------------------------------------------
# Feeder layout rules for this machine
# --------------------------------------------------------------------------
# Slots 1-16 hold the machine's 16 8mm feeders (0402 to 1206, SOT-23,
# SOD-123). The busiest 8mm parts go in the middle of that bank, nearest the
# work: 7, 8, 6, 9, 5, 10 ... -- the order the user gave. The big feeders
# (12mm and up: SMA diodes, 1210, SO-16, SMD-4 ...) start at 20. Feeder 49 is
# the fiducial's, always, and its placement is skipped. A job with more 8mm
# parts than the bank holds is split into stages (Export Stages).
EIGHT_MM_BANK = [str(i) for i in range(1, 17)]   # Options: "8mm feeders"
BANK_CENTRE = 7.5
FIDUCIAL_FEEDER_ID = "49"
OTHER_FEEDERS_FROM = 20


EIGHT_MM_NOZZLES = "13"            # nozzles 1 and 3 pick from the 8mm bank
EIGHT_MM_FEED_RHYTHM = "4"         # 4mm step; fine-tuned on the machine
EIGHT_MM_PLACE_HEIGHT = "2.60"
FEED_RHYTHM_FIELD = 17

# Pick positions the machine itself taught, from the proven GPS file. The
# feeder angle is 0 on every slot: the GPS file's 90 on the right bank made
# the machine place every part a quarter turn out on BOARD-A. A slot
# with no taught position gets one from its nearest taught neighbour and the
# bank's pitch, so it lands where that slot physically is -- never on top of
# another feeder. Teach every such slot on the machine before running.
TAUGHT_SLOTS = {
    "1": (403.28, 90.74, 0.0), "2": (403.14, 107.75, 0.0),
    "5": (409.59, 143.29, 0.0), "6": (409.45, 156.86, 0.0),
    "7": (409.91, 170.12, 0.0),
    "21": (26.88, 39.25, 0.0), "22": (26.62, 52.51, 0.0), "23": (25.24, 66.87, 0.0),
    "24": (26.28, 79.78, 0.0), "25": (22.70, 93.85, 0.0), "26": (26.40, 107.28, 0.0),
    "27": (25.15, 121.96, 0.0), "28": (23.15, 134.81, 0.0), "29": (22.84, 148.06, 0.0),
    "47": (30.00, 381.00, 0.0), "48": (30.00, 394.00, 0.0),
}
SLOT_BANKS = (            # (first id, last id, pitch along Y, default angle)
    (1, 20, 13.3, 0.0),
    (21, 48, 13.15, 0.0),
    (49, 99, 13.15, 0.0),
)


# ---- Slots set up on the machine ------------------------------------------
# What the operator set up on the machine for each slot -- pick X / Y, feeder
# angle, feeder power and peeler power -- read back from a machine file the
# machine itself wrote (Options -> Learn from machine file). These belong to
# the slot, not to the reel in it, so every later output uses them for
# whatever part is loaded there.
FEEDER_POWER_FIELD = 18
PEELER_POWER_FIELD = 19
SLOT_PROFILE_FIELDS = (4, 5, 6, FEEDER_POWER_FIELD, PEELER_POWER_FIELD)
LEARNED_SLOTS = {}                 # feeder id -> {field index: value}
DEFAULT_FEEDER_POWER = "10"
DEFAULT_PEELER_POWER = "10"


def learn_slots_from_machine_file(path):
    """Reads every stack row of a machine file into LEARNED_SLOTS. Returns the ids."""
    learned = []
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        for r in csv.reader(fh):
            if not r or r[0].strip() != "stack" or len(r) <= max(SLOT_PROFILE_FIELDS):
                continue
            fid = r[1].strip()
            try:
                float(r[4]), float(r[5])
            except ValueError:
                continue
            LEARNED_SLOTS[fid] = {i: r[i].strip() for i in SLOT_PROFILE_FIELDS}
            learned.append(fid)
    return learned


def apply_slot_profile(row):
    """Puts the slot's set-up values into a feeder row. Returns True if changed."""
    fid = str(field_at(row, 1)).strip()
    want = dict(LEARNED_SLOTS.get(fid, {}))
    if not feeder_is_fiducial(row):
        for i, default in ((FEEDER_POWER_FIELD, DEFAULT_FEEDER_POWER),
                           (PEELER_POWER_FIELD, DEFAULT_PEELER_POWER)):
            if i not in want and str(field_at(row, i)).strip() in ("", "0"):
                want[i] = default
    changed = False
    for i, v in want.items():
        if str(field_at(row, i)) != v:
            set_field(row, i, v)
            changed = True
    return changed


def slot_position_for(fid):
    """(X, Y, angle) for a feeder slot: set up on the machine, taught, or estimated."""
    fid = str(fid).strip()
    if fid in LEARNED_SLOTS:
        try:
            p = LEARNED_SLOTS[fid]
            return (float(p[4]), float(p[5]), float(p[6] or 0))
        except (KeyError, ValueError):
            pass
    try:
        n = int(fid)
    except ValueError:
        return TAUGHT_SLOTS.get(fid, (25.0, 40.0, 0.0))
    for first, last, pitch, angle in SLOT_BANKS:
        if first <= n <= last:
            break
    else:
        first, last, pitch, angle = SLOT_BANKS[-1]
    # Neighbours: in a bank set up on this machine, only its own slots -- the
    # GPS file's positions are another machine's. Elsewhere the GPS file's.
    learned = {}
    for k, p in LEARNED_SLOTS.items():
        try:
            learned[int(k)] = (float(p[4]), float(p[5]))
        except (KeyError, ValueError):
            continue
    if any(first <= k <= last for k in learned):
        points = learned
    else:
        if fid in TAUGHT_SLOTS:
            return TAUGHT_SLOTS[fid]
        points = {int(k): (x, y) for k, (x, y, _) in TAUGHT_SLOTS.items()}
        points.update(learned)
    known = [k for k in points if first <= k <= last]
    if not known:          # bank 49+: carries on from the end of the left bank
        known = [max(k for k in points if k < first)]
    ref = min(known, key=lambda k: (abs(k - n), k))
    x, y = points[ref]
    return (x, round(y + pitch * (n - ref), 2), angle)


def apply_eight_mm_defaults(row, footprint):
    """8mm feeders: nozzles 1 and 3; feed rhythm 4 and place height 3.2
    unless already set."""
    if tape_width_for(footprint) != 8:
        return
    set_field(row, 3, EIGHT_MM_NOZZLES)
    if str(field_at(row, FEED_RHYTHM_FIELD)).strip() in ("", "0"):
        set_field(row, FEED_RHYTHM_FIELD, EIGHT_MM_FEED_RHYTHM)
    try:
        unset = float(str(field_at(row, 11)).strip() or 0) == 0
    except ValueError:
        unset = True
    if unset:
        set_field(row, 11, EIGHT_MM_PLACE_HEIGHT)


def centre_out_slots():
    """The 8mm bank in the order to fill it: 7, 8, 6, 9, 5, 10, ..."""
    return sorted(EIGHT_MM_BANK, key=lambda s: (abs(int(s) - BANK_CENTRE), int(s)))


def auto_assign_feeders(parts, start_id=None):
    """
    Gives every part a feeder:
      1. the fiducial -> feeder 49;
      2. parts on 8mm tape, busiest first, into the 8mm bank from its middle out;
      3. anything else keeps the feeder the template already had it on, if that
         feeder is outside the bank;
      4. then empty template slots outside the bank, then new numbers from 21.
    A part moved to a new slot still takes its tuned settings (heights, delays,
    speed, nozzles) from the feeder that carried it before.
    """
    if start_id is None:
        start_id = OTHER_FEEDERS_FROM
    template_feeders = machine_feeders()
    by_part = {}
    for f in template_feeders:
        part = str(field_at(f, 8)).strip()
        if part:
            by_part.setdefault(part_match_key(part), []).append(f)

    def key(e):
        return part_match_key("%s/%s" % (e["footprint"], e["value"]))

    used = set()
    reserved = set(EIGHT_MM_BANK) | {FIDUCIAL_FEEDER_ID}

    for e in parts:
        if _part_is_fiducial(e):
            e["feeder"] = FIDUCIAL_FEEDER_ID
            e["source"] = "fiducial"
            used.add(FIDUCIAL_FEEDER_ID)
            _adopt_feeder_settings(e, None)

    eight = [e for e in parts if not e["feeder"] and tape_width_for(e["footprint"]) == 8]
    eight.sort(key=lambda e: (-len(e["rows"]), e["footprint"], e["value"]))
    bank = [s for s in centre_out_slots() if s not in used]
    for e in eight:
        if not bank:
            break                        # more 8mm parts than slots: rest go on below
        sid = bank.pop(0)
        e["feeder"] = sid
        e["source"] = "8mm bank (%d pcs)" % len(e["rows"])
        used.add(sid)
        previous = next(iter(by_part.get(key(e), [])), None)
        _adopt_feeder_settings(e, previous)

    # The rest, busiest first, packed from 21 with no gaps. Only a slot whose
    # feeder carries a part this job does not use is stepped over -- that reel
    # stays loaded as it is.
    job = {key(e) for e in parts}
    template_ids = {str(field_at(f, 1)) for f in template_feeders}
    foreign = {str(field_at(f, 1)) for f in template_feeders
               if str(field_at(f, 8)).strip() and part_match_key(field_at(f, 8)) not in job}
    rest = [e for e in parts if not e["feeder"]]
    rest.sort(key=lambda e: (tape_width_for(e["footprint"]) == 8,
                             -len(e["rows"]), e["footprint"], e["value"]))
    next_id = start_id
    for e in rest:
        while str(next_id) in used | reserved | foreign:
            next_id += 1
        e["feeder"] = str(next_id)
        e["source"] = "slot %d" % next_id if e["feeder"] in template_ids \
            else "new (needs teaching)"
        used.add(e["feeder"])
        _adopt_feeder_settings(e, next(iter(by_part.get(key(e), [])), None))
    for e in parts:
        if tape_width_for(e["footprint"]) == 8 and not _part_is_fiducial(e):
            e["nozzles"] = EIGHT_MM_NOZZLES
    return parts


def enforce_fiducial_feeder():
    """
    The fiducial sits on feeder 49 with its placement skipped, in the feeder
    list and in the placement list. A real part found on 49 moves to the
    lowest free number from 21. Returns True if anything changed.
    """
    comps = machine_model["comps"]
    fid_comps = [c for c in comps if comp_is_fiducial(c)]
    if not fid_comps:
        return False
    changed = False
    feeders = machine_feeders()
    ids = {str(field_at(f, 1)) for f in feeders}

    occupant = next((f for f in feeders if str(field_at(f, 1)) == FIDUCIAL_FEEDER_ID
                     and not feeder_is_fiducial(f)), None)
    if occupant is not None:
        n = OTHER_FEEDERS_FROM
        while str(n) in ids or str(n) == FIDUCIAL_FEEDER_ID or str(n) in EIGHT_MM_BANK:
            n += 1
        set_field(occupant, 1, str(n))
        for c in comps:
            if str(field_at(c, 1)) == FIDUCIAL_FEEDER_ID and not comp_is_fiducial(c):
                set_field(c, 1, str(n))
        ids.add(str(n))
        changed = True

    fid_feeders = [f for f in machine_feeders() if feeder_is_fiducial(f)]
    if fid_feeders:
        if str(field_at(fid_feeders[0], 1)) != FIDUCIAL_FEEDER_ID:
            set_field(fid_feeders[0], 1, FIDUCIAL_FEEDER_ID)
            changed = True
    else:
        row = _blank_feeder_row()
        set_field(row, 1, FIDUCIAL_FEEDER_ID)
        set_field(row, 8, "FIDUCIAL/FIDUCIAL")
        machine_model["pre_records"].insert(0, row)
        fid_feeders = [row]
        changed = True
    # The fiducial is never picked, but its feeder row must not sit on another
    # feeder's pick position (it was landing on feeder 21's).
    if FIDUCIAL_FEEDER_ID in LEARNED_SLOTS:
        if apply_slot_profile(fid_feeders[0]):
            changed = True
    else:
        x, y, a = slot_position_for(FIDUCIAL_FEEDER_ID)
        for i, v in ((4, "%.2f" % x), (5, "%.2f" % y), (6, "%.2f" % a)):
            if str(field_at(fid_feeders[0], i)) != v:
                set_field(fid_feeders[0], i, v)
                changed = True

    for c in fid_comps:
        if str(field_at(c, 1)) != FIDUCIAL_FEEDER_ID:
            set_field(c, 1, FIDUCIAL_FEEDER_ID)
            changed = True
        if str(field_at(c, 9)).strip().lower() != "yes":
            set_field(c, 9, "Yes")
            changed = True
    if pin_fiducial_feeder_first():
        changed = True
    if pin_fiducials_first():
        changed = True
    return changed


# --------------------------------------------------------------------------
# Live link between the Pick & Place list and the machine program
# --------------------------------------------------------------------------
# The link is to one board, named by the Pick & Place file the list was read
# from. Designators cannot tell boards apart -- every board has an R1 and a
# C1 -- so loading another board's file would otherwise rewrite this program
# with the other board's positions.
pnp_source = {"path": None}
# A machine program built from the Pick & Place list stays linked to it: an
# edit on either side -- rename, delete, rotate, align, move -- shows up on the
# other at once. A program read in from a machine file is not linked: its
# coordinates are the machine's, and the list's are the design's.
def _comp_from_row(c, r):
    """Copy what the list owns into a comp. Returns True if anything changed."""
    want = {3: str(r[0]), 4: str(r[10]), 5: str(r[1]),
            6: _format_machine_coord(r[2]), 7: _format_machine_coord(r[3]),
            8: _format_machine_angle(r[9])}
    if comp_is_fiducial(c):
        # the operator sets the fiducial on the machine; the list never moves it
        for i in (6, 7, 8):
            want.pop(i)
    changed = False
    for i, v in want.items():
        if str(field_at(c, i)) != v:
            set_field(c, i, v)
            changed = True
    return changed


def _feeder_for_part(footprint, value):
    """The feeder carrying a part, or a new one placed by the layout rules."""
    key = part_match_key("%s/%s" % (footprint, value))
    for f in machine_feeders():
        if part_match_key(field_at(f, 8)) == key:
            return f
    ids = {str(field_at(f, 1)) for f in machine_feeders()}
    if tape_width_for(footprint) == 8:
        sid = next((s for s in centre_out_slots() if s not in ids), None)
    else:
        sid = None
    if sid is None:
        n = OTHER_FEEDERS_FROM
        while str(n) in ids or str(n) == FIDUCIAL_FEEDER_ID or str(n) in EIGHT_MM_BANK:
            n += 1
        sid = str(n)
    row = _blank_feeder_row()
    set_field(row, 1, sid)
    set_field(row, 8, "%s/%s" % (footprint, value))
    x, y, a = slot_position_for(sid)
    set_field(row, 4, "%.2f" % x)
    set_field(row, 5, "%.2f" % y)
    set_field(row, 6, "%.2f" % a)
    apply_eight_mm_defaults(row, footprint)
    ph = place_height_for(footprint, value)
    if ph is not None:
        set_field(row, 11, "%.2f" % ph)
    apply_slot_profile(row)
    stacks = [i for i, r in enumerate(machine_model["pre_records"]) if r and r[0] == "stack"]
    machine_model["pre_records"].insert((stacks[-1] + 1) if stacks else 0, row)
    return row


def sync_machine_from_rows():
    """
    Brings a linked machine program in line with the Pick & Place list: moved,
    turned, renamed and deleted parts follow, a part renamed onto another
    value moves to that value's feeder, and a fiducial added in the list
    joins the program on feeder 49. Placement order and feeder setup are left
    as they are. Returns True if anything changed.
    """
    comps = machine_model["comps"]
    if not machine_model.get("linked") or not comps or not rows:
        return False
    if machine_model.get("linked_to") != pnp_source["path"]:
        machine_model["linked"] = False          # another board is loaded
        return False
    by_des = {str(r[0]).strip(): r for r in rows}
    in_prog = {str(field_at(c, 3)).strip() for c in comps}
    if len(in_prog & set(by_des)) < 0.5 * len(in_prog):
        # a different board has been loaded: leave this program alone for good
        machine_model["linked"] = False
        return False

    changed = False
    keep = []
    for c in comps:
        r = by_des.get(str(field_at(c, 3)).strip())
        if r is None:
            changed = True               # deleted from the list
            continue
        old_key = part_match_key("%s/%s" % (field_at(c, 5), field_at(c, 4)))
        if _comp_from_row(c, r):
            changed = True
        new_key = part_match_key("%s/%s" % (field_at(c, 5), field_at(c, 4)))
        if new_key != old_key and not comp_is_fiducial(c):
            f = _feeder_for_part(field_at(c, 5), field_at(c, 4))
            set_field(c, 1, str(field_at(f, 1)))
            nz = parse_nozzles(field_at(f, 3)) or [1]
            if str(field_at(c, 2)) not in {str(n) for n in nz}:
                set_field(c, 2, str(nz[0]))
        keep.append(c)

    for r in rows:
        des = str(r[0]).strip()
        if des.upper().startswith("FID") and des not in in_prog:
            c = ["comp", FIDUCIAL_FEEDER_ID, "1", des, str(r[10]), str(r[1]),
                 _format_machine_coord(r[2]), _format_machine_coord(r[3]),
                 _format_machine_angle(r[9]), "Yes", ""]
            keep.insert(0, c)
            changed = True

    if changed:
        comps[:] = keep
    if enforce_fiducial_feeder():
        changed = True
    return changed


def push_comp_to_row(c):
    """
    The other direction: a comp edited in the machine tab updates its line in
    the Pick & Place list -- position (pads and reference move with it),
    rotation, value and footprint.
    """
    if not machine_model.get("linked"):
        return False
    des = str(field_at(c, 3)).strip()
    r = next((x for x in rows if str(x[0]).strip() == des), None)
    if r is None:
        return False
    try:
        nx, ny = float(field_at(c, 6)), float(field_at(c, 7))
        dx, dy = nx - float(r[2]), ny - float(r[3])
    except (TypeError, ValueError):
        dx = dy = 0.0
    if abs(dx) > 1e-9 or abs(dy) > 1e-9:
        for xi, yi in ((2, 3), (4, 5), (6, 7)):
            try:
                r[xi] = round(float(r[xi]) + dx, 3)
                r[yi] = round(float(r[yi]) + dy, 3)
            except (TypeError, ValueError):
                pass
    try:
        new_rot = float(field_at(c, 8)) % 360
        if abs(((new_rot - float(r[9])) + 180) % 360 - 180) > 1e-6:
            set_component_rotation(r, new_rot)
            angle_flags.pop(id(r), None)
    except (TypeError, ValueError):
        pass
    r[10] = str(field_at(c, 4))
    r[1] = str(field_at(c, 5))
    return True


def _part_is_fiducial(entry):
    return ("FIDUCIAL" in str(entry.get("footprint", "")).upper()
            or "FIDUCIAL" in str(entry.get("value", "")).upper())


def _adopt_feeder_settings(entry, feeder):
    """Fills blank settings from the template feeder, or from the fallback."""
    # Every tuned setting of the feeder the part was on, so a build that moves
    # the part to another slot can carry all of it, not just these four.
    entry["settings_from"] = list(feeder) if feeder is not None else None
    if feeder is not None:
        entry["nozzles"] = str(field_at(feeder, 3)) or "1"
        entry["pick"] = str(field_at(feeder, 9))
        entry["place"] = str(field_at(feeder, 11))
        entry["speed"] = str(field_at(feeder, 16))
    else:
        entry["nozzles"] = entry["nozzles"] or "1"
        entry["pick"] = entry["pick"] or FALLBACK_FEEDER[9]
        entry["place"] = entry["place"] or FALLBACK_FEEDER[11]
        entry["speed"] = entry["speed"] or FALLBACK_FEEDER[16]


def _feeder_rows_for(parts):
    """
    One feeder row per assigned part, plus the template's unused feeders (kept,
    taught positions and all, emptied if their part moved). A slot the template
    never taught gets its position from slot_position_for.
    Returns (rows sorted fiducial first then by id, {(footprint, value): entry}).
    """
    template_by_id = {}
    for f in machine_feeders():
        template_by_id[str(field_at(f, 1))] = f

    new_feeders = []
    feeder_by_part = {}
    for entry in parts:
        fid = str(entry["feeder"])
        template = template_by_id.get(fid)
        row = list(template) if template else _blank_feeder_row()
        while len(row) < FEEDER_ROW_WIDTH:
            row.append("")

        row[0] = "stack"
        prev = entry.get("settings_from")
        if prev is not None and str(field_at(prev, 1)) != fid:
            # moved to a new slot: the slot keeps its position and mounting,
            # the part brings delays, vacuum, vision and the rest with it
            for i in range(max(len(prev), len(row))):
                if i not in SLOT_OWNED_FIELDS:
                    set_field(row, i, field_at(prev, i))
        set_field(row, 1, fid)
        set_field(row, 3, entry["nozzles"] or "1")
        set_field(row, 8, "%s/%s" % (entry["footprint"], entry["value"]))
        for index, key in ((9, "pick"), (11, "place"), (16, "speed")):
            if str(entry.get(key, "")).strip():
                set_field(row, index, str(entry[key]).strip())

        if template is None:
            x, y, a = slot_position_for(fid)
            set_field(row, 4, "%.2f" % x)
            set_field(row, 5, "%.2f" % y)
            set_field(row, 6, "%.2f" % a)
        if not _part_is_fiducial(entry):
            apply_eight_mm_defaults(row, entry["footprint"])
            entry["nozzles"] = str(field_at(row, 3))
            ph = place_height_for(entry["footprint"], entry["value"])
            if ph is not None:
                set_field(row, 11, "%.2f" % ph)
                entry["place"] = "%.2f" % ph
        apply_slot_profile(row)

        new_feeders.append(row)
        feeder_by_part[(entry["footprint"], entry["value"])] = entry

    # Feeders in the template that this build did not use keep their taught
    # positions and settings, so the physical setup is never lost.
    assigned_ids = {str(field_at(f, 1)) for f in new_feeders}
    carried = {part_match_key(field_at(f, 8)) for f in new_feeders}
    for f in machine_feeders():
        if str(field_at(f, 1)) not in assigned_ids:
            # Kept only while it still holds a reel this job does not use. An
            # empty slot, or one whose part has moved, is dropped: no gaps.
            part = str(field_at(f, 8)).strip()
            if part and part_match_key(part) not in carried:
                new_feeders.append(f)
    new_feeders.sort(key=lambda f: (not feeder_is_fiducial(f), _feeder_sort_key(field_at(f, 1))))
    return new_feeders, feeder_by_part


def build_machine_from_pnp(parts, source_rows, keep_board_records=True):
    """
    Writes the assignments into machine_model: one feeder per part, one comp per
    component, in the order given. Template feeders keep their taught positions
    and every column this tool does not set.
    """
    board_records = machine_board_records() if keep_board_records else []
    smd_header = machine_model["smd_header"] or DEFAULT_SMD_HEADER
    feeder_header = machine_model["feeder_header"] or DEFAULT_FEEDER_HEADER
    new_feeders, feeder_by_part = _feeder_rows_for(parts)

    # Round-robin the nozzles a feeder allows, the way the machine's own files do
    nozzle_cursor = {}
    comps = []
    for r in source_rows:
        entry = feeder_by_part.get((str(r[1]).strip(), str(r[10]).strip()))
        if entry is None:
            continue
        fid = str(entry["feeder"])
        allowed = parse_nozzles(entry["nozzles"]) or [1]
        cursor = nozzle_cursor.get(fid, 0)
        nozzle = allowed[cursor % len(allowed)]
        nozzle_cursor[fid] = cursor + 1

        comp = ["comp", fid, str(nozzle), str(r[0]), str(r[10]), str(r[1]),
                _format_machine_coord(r[2]), _format_machine_coord(r[3]),
                _format_machine_angle(r[9]),
                "Yes" if str(r[0]).strip().upper().startswith("FID") else "No"]
        while len(comp) < COMP_ROW_WIDTH:
            comp.append("")
        comps.append(comp)

    # Fiducials first, matching what the machine expects
    fids = [c for c in comps if str(c[3]).upper().startswith("FID")]
    others = [c for c in comps if not str(c[3]).upper().startswith("FID")]

    machine_model["feeder_header"] = feeder_header
    machine_model["pre_records"] = new_feeders + board_records
    machine_model["smd_header"] = smd_header
    machine_model["comps"] = fids + others
    machine_model["post_records"] = machine_model.get("post_records", [])
    machine_model["path"] = None
    machine_model["linked"] = True
    machine_model["linked_to"] = pnp_source["path"]
    machine_model["stage1"] = []
    enforce_fiducial_feeder()

    refresh_feeder_table()
    refresh_machine_comp_table()
    refresh_board_table()
    machine_path_label.config(text="Built from Pick & Place (not saved yet)", fg="#ffcc00")


def arrange_feeders():
    """
    Re-lays out the feeders of the program in the machine tab by the layout
    rules -- fiducial on 49, busiest 8mm parts centre-out in 1-20, the rest
    from 21 -- keeping placement order, coordinates and taught slots. Moved
    parts bring their tuned settings. Returns the number of parts moved.
    """
    comps = machine_model["comps"]
    if not comps:
        return 0
    parts, by_key = [], {}
    for c in comps:
        k = (str(field_at(c, 5)).strip(), str(field_at(c, 4)).strip())
        if k not in by_key:
            by_key[k] = {"footprint": k[0], "value": k[1], "rows": [], "feeder": "",
                         "nozzles": "1", "pick": "", "place": "", "speed": "",
                         "source": "", "was": str(field_at(c, 1))}
            parts.append(by_key[k])
        by_key[k]["rows"].append(c)
    auto_assign_feeders(parts)
    moved = sum(1 for e in parts if e["feeder"] != e["was"])
    machine_model["stage1"] = []

    new_feeders, feeder_by_part = _feeder_rows_for(parts)
    board = [r for r in machine_model["pre_records"] if not (r and r[0] == "stack")]
    machine_model["pre_records"] = new_feeders + board

    cursor = {}
    for c in comps:
        e = feeder_by_part.get((str(field_at(c, 5)).strip(), str(field_at(c, 4)).strip()))
        if e is None:
            continue
        set_field(c, 1, str(e["feeder"]))
        allowed = parse_nozzles(e["nozzles"]) or [1]
        if int(str(field_at(c, 2)) or 0) not in allowed:
            i = cursor.get(e["feeder"], 0)
            set_field(c, 2, str(allowed[i % len(allowed)]))
            cursor[e["feeder"]] = i + 1
    enforce_fiducial_feeder()
    return moved


def adopt_layout_from_machine_file(path):
    """
    Makes the program match what is loaded on the machine: each part found in
    the machine file goes to the feeder it is on there, with that feeder row
    exactly as the machine wrote it, and becomes stage 1. Parts the file does
    not carry move to free numbers clear of its feeders (they go in later
    stages). Placement order and coordinates are untouched.
    Returns (parts matched, parts not in the file).
    """
    file_rows, file_board, file_comps = [], [], []
    in_smd = False
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        for r in csv.reader(fh):
            if not r:
                continue
            tag = r[0].strip()
            if tag == "#SMD":
                in_smd = True
            elif tag == "stack":
                while len(r) < FEEDER_ROW_WIDTH:
                    r.append("")
                file_rows.append(r)
            elif tag == "comp":
                while len(r) < COMP_ROW_WIDTH:
                    r.append("")
                file_comps.append(r)
            elif not in_smd and not tag.startswith("#"):
                file_board.append(r)          # pcb, mark, markext, test, mirror ...
    by_key = {}
    for r in file_rows:
        if str(r[8]).strip() and not feeder_is_fiducial(r):
            by_key.setdefault(part_match_key(r[8]), r)
    file_ids = {str(r[1]).strip() for r in file_rows}

    comps = machine_model["comps"]
    old = {str(field_at(f, 1)): f for f in machine_feeders()}
    keys = []
    for c in comps:
        if not comp_is_fiducial(c):
            k = part_match_key("%s/%s" % (field_at(c, 5), field_at(c, 4)))
            if k not in keys:
                keys.append(k)

    new_rows, fid_for, matched, missing = [], {}, [], []
    for k in keys:
        if k in by_key:
            row = list(by_key[k])
            new_rows.append(row)
            fid_for[k] = str(row[1]).strip()
            matched.append(k)
    # slots loaded with nothing are left out: no empty feeders in the program
    taken = set(file_ids) | set(EIGHT_MM_BANK) | {FIDUCIAL_FEEDER_ID}
    n = OTHER_FEEDERS_FROM
    for k in keys:
        if k in fid_for:
            continue
        src = next((f for c in comps if not comp_is_fiducial(c)
                    and part_match_key("%s/%s" % (field_at(c, 5), field_at(c, 4))) == k
                    for f in [old.get(str(field_at(c, 1)))] if f is not None), None)
        while str(n) in taken:
            n += 1
        row = list(src) if src is not None else _blank_feeder_row()
        set_field(row, 1, str(n))
        x, y, a = slot_position_for(str(n))
        for j, v in ((4, x), (5, y), (6, a)):
            set_field(row, j, "%.2f" % v)
        taken.add(str(n))
        new_rows.append(row)
        fid_for[k] = str(n)
        missing.append(k)

    fid_row = next((r for r in file_rows if feeder_is_fiducial(r)), None) \
        or next((f for f in machine_feeders() if feeder_is_fiducial(f)), None)
    new_rows.sort(key=lambda f: _feeder_sort_key(field_at(f, 1)))
    if fid_row is not None:
        new_rows.insert(0, list(fid_row))
    board = [r for r in machine_model["pre_records"] if not (r and r[0] == "stack")]
    machine_model["pre_records"] = new_rows + board

    by_id = {str(field_at(f, 1)): f for f in new_rows}
    for c in comps:
        if comp_is_fiducial(c):
            continue
        fid = fid_for[part_match_key("%s/%s" % (field_at(c, 5), field_at(c, 4)))]
        set_field(c, 1, fid)
        allowed = parse_nozzles(field_at(by_id[fid], 3)) or [1]
        try:
            ok = int(str(field_at(c, 2))) in allowed
        except ValueError:
            ok = False
        if not ok:
            set_field(c, 2, str(allowed[0]))
    machine_model["stage1"] = matched

    # What else the operator set on the machine stays as the machine has it:
    # board, mark and panel (mirror) records, the fiducial's position, and
    # each placement's nozzle and skip. A placement moved on the machine
    # moves in the list too, so the live link keeps it there.
    if file_board:
        machine_model["pre_records"] = [r for r in machine_model["pre_records"]
                                        if r and r[0] == "stack"] + [list(r) for r in file_board]
    file_comp = {str(r[3]).strip(): r for r in file_comps}
    for c in comps:
        fc = file_comp.get(str(field_at(c, 3)).strip())
        if fc is None:
            continue
        if comp_is_fiducial(c):
            for i in (2, 6, 7, 8, 9):
                set_field(c, i, str(fc[i]))
            continue
        if str(field_at(c, 1)) == str(fc[1]).strip():
            set_field(c, 2, str(fc[2]))
        set_field(c, 9, str(fc[9]))
        if any(str(field_at(c, i)) != str(fc[i]) for i in (6, 7, 8)):
            for i in (6, 7, 8):
                set_field(c, i, str(fc[i]))
            push_comp_to_row(c)
    enforce_fiducial_feeder()
    return matched, missing


def adopt_layout_clicked():
    if not machine_model["comps"]:
        messagebox.showinfo("Take Layout From Machine File", "There is no machine program.")
        return
    path = filedialog.askopenfilename(
        title="Machine file written by the machine",
        filetypes=(("CSV Files", "*.csv"), ("All Files", "*.*")))
    if not path:
        return
    matched, missing = adopt_layout_from_machine_file(path)
    refresh_feeder_table()
    refresh_machine_comp_table()
    messagebox.showinfo(
        "Take Layout From Machine File",
        "%d part(s) now on the feeders they are on in the machine file (stage 1).\n"
        "%d part(s) not in it moved clear of those feeders; Export Stages puts them "
        "in the later stages." % (len(matched), len(missing)))


def plan_stages():
    """
    Splits the program into assembly stages for a machine with only
    len(EIGHT_MM_BANK) 8mm feeders: 8mm parts busiest first, one bank-full per
    stage, each stage loading its parts centre-out (7, 8, 6, 9 ...). Stage 1
    also carries every big-feeder part, so the most placing is done first.
    Returns a list of (feeder rows, comps), fiducial first in each.
    """
    comps = machine_model["comps"]
    feeders = machine_feeders()
    by_id = {str(field_at(f, 1)): f for f in feeders}
    fid_feeder = next((f for f in feeders if feeder_is_fiducial(f)), None)
    fid_comps = [c for c in comps if comp_is_fiducial(c)]

    parts = {}
    for c in comps:
        if comp_is_fiducial(c):
            continue
        k = part_match_key("%s/%s" % (field_at(c, 5), field_at(c, 4)))
        parts.setdefault(k, {"footprint": str(field_at(c, 5)), "comps": [],
                             "feeder": by_id.get(str(field_at(c, 1)))})["comps"].append(c)
    eight = [p for p in parts.values() if tape_width_for(p["footprint"]) == 8]
    eight.sort(key=lambda p: (-len(p["comps"]), p["footprint"]))
    big = [p for p in parts.values() if tape_width_for(p["footprint"]) != 8]
    n = len(EIGHT_MM_BANK)

    # Stage 1 as loaded on the machine (Take Layout From Machine File): those
    # parts stay on the feeders they are on; the rest follow, centre-out.
    pinned = set(machine_model.get("stage1") or [])
    kept = [p for k, p in parts.items() if k in pinned]
    if kept:
        kept_ids = {id(p) for p in kept}
        eight = [p for p in eight if id(p) not in kept_ids]
        kept += [p for p in big if id(p) not in kept_ids]
        chunks = [[]] + [eight[i:i + n] for i in range(0, len(eight), n)]
    else:
        kept = list(big)
        chunks = [eight[i:i + n] for i in range(0, len(eight), n)] or [[]]

    stages = []
    for i, chunk in enumerate(chunks):
        rows, new_id = [], {}
        if fid_feeder is not None:
            rows.append(list(fid_feeder))
        for p, sid in zip(chunk, centre_out_slots()):
            row = list(p["feeder"]) if p["feeder"] else _blank_feeder_row()
            slot = by_id.get(sid)
            pos = ([field_at(slot, j) for j in (4, 5, 6)] if slot is not None
                   else ["%.2f" % v for v in slot_position_for(sid)])
            set_field(row, 1, sid)
            for j, v in zip((4, 5, 6), pos):
                set_field(row, j, v)
            rows.append(row)
            new_id[id(p)] = sid
        stage_parts = chunk + (kept if i == 0 else [])
        if i == 0:
            seen = set()
            for p in kept:
                if p["feeder"] is not None and id(p["feeder"]) not in seen:
                    seen.add(id(p["feeder"]))
                    rows.append(list(p["feeder"]))
        rows[1:] = sorted(rows[1:], key=lambda f: _feeder_sort_key(field_at(f, 1)))
        for r in rows:
            apply_slot_profile(r)
        wanted = {id(c): new_id.get(id(p)) for p in stage_parts for c in p["comps"]}
        out = [list(c) for c in fid_comps]
        for c in comps:
            if id(c) in wanted:
                c2 = list(c)
                if wanted[id(c)]:
                    set_field(c2, 1, wanted[id(c)])
                out.append(c2)
        stages.append((rows, out))
    return stages


def write_machine_file(path, feeders, comps):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(machine_model["feeder_header"] or DEFAULT_FEEDER_HEADER)
        for r in feeders:
            w.writerow(r)
        for r in machine_board_records():
            w.writerow(r)
        w.writerow(machine_model["smd_header"] or DEFAULT_SMD_HEADER)
        for r in comps:
            w.writerow(r)
        for r in machine_model["post_records"]:
            w.writerow(r)


def export_stages(base_path):
    """Writes <base>-1.csv, <base>-2.csv ... Returns [(path, parts, comps)]."""
    base = re.sub(r"(-\d+)?(\.csv)?$", "", base_path, flags=re.I)
    done = []
    for i, (feeders, comps) in enumerate(plan_stages(), 1):
        path = "%s-%d.csv" % (base, i)
        write_machine_file(path, feeders, comps)
        done.append((path, len(feeders) - 1, sum(1 for c in comps if not comp_is_fiducial(c))))
    return done


def export_stages_clicked():
    if not machine_model["comps"]:
        messagebox.showerror("Export Stages", "There is no machine program to split.")
        return
    path = filedialog.asksaveasfilename(
        title="Export Stages -- base name, e.g. BOARD-A (writes BOARD-A-1, BOARD-A-2 ...)",
        defaultextension=".csv", filetypes=(("CSV Files", "*.csv"), ("All Files", "*.*")))
    if not path:
        return
    try:
        done = export_stages(path)
    except Exception as e:
        messagebox.showerror("Export Stages", "Error writing the stage files:\n\n%s" % e)
        return
    messagebox.showinfo("Export Stages", "\n".join(
        "%s   %d feeders, %d parts placed" % (os.path.basename(p), nf, nc)
        for p, nf, nc in done))


# --------------------------------------------------------------------------
# Options: every machine constant, editable, saved for the next start
# --------------------------------------------------------------------------
def settings_path():
    """Where Options are kept. KFG_SETTINGS overrides it; empty means none."""
    env = os.environ.get("KFG_SETTINGS")
    if env is not None:
        return env or None
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "KFG Pick and Place", "settings.json")


def current_settings():
    return {
        "fallback_feeder": {str(k): v for k, v in FALLBACK_FEEDER.items()},
        "eight_mm_count": len(EIGHT_MM_BANK),
        "bank_centre": BANK_CENTRE,
        "big_feeders_from": OTHER_FEEDERS_FROM,
        "fiducial_feeder": FIDUCIAL_FEEDER_ID,
        "eight_mm_nozzles": EIGHT_MM_NOZZLES,
        "eight_mm_feed_rhythm": EIGHT_MM_FEED_RHYTHM,
        "eight_mm_place_height": EIGHT_MM_PLACE_HEIGHT,
        "feeder_power": DEFAULT_FEEDER_POWER,
        "peeler_power": DEFAULT_PEELER_POWER,
        "board_surface_mm": BOARD_SURFACE_MM,
        "press_percent": round(PRESS_FRACTION * 100, 3),
        "slots": {fid: {str(i): v for i, v in p.items()} for fid, p in LEARNED_SLOTS.items()},
    }


def apply_settings(d):
    global EIGHT_MM_BANK, BANK_CENTRE, OTHER_FEEDERS_FROM, FIDUCIAL_FEEDER_ID
    global EIGHT_MM_NOZZLES, EIGHT_MM_FEED_RHYTHM, EIGHT_MM_PLACE_HEIGHT
    global DEFAULT_FEEDER_POWER, DEFAULT_PEELER_POWER, BOARD_SURFACE_MM, PRESS_FRACTION
    for k, v in (d.get("fallback_feeder") or {}).items():
        FALLBACK_FEEDER[int(k)] = str(v)
    if "eight_mm_count" in d:
        EIGHT_MM_BANK = [str(i) for i in range(1, int(d["eight_mm_count"]) + 1)]
    BANK_CENTRE = float(d.get("bank_centre", BANK_CENTRE))
    OTHER_FEEDERS_FROM = int(d.get("big_feeders_from", OTHER_FEEDERS_FROM))
    FIDUCIAL_FEEDER_ID = str(d.get("fiducial_feeder", FIDUCIAL_FEEDER_ID))
    EIGHT_MM_NOZZLES = str(d.get("eight_mm_nozzles", EIGHT_MM_NOZZLES))
    EIGHT_MM_FEED_RHYTHM = str(d.get("eight_mm_feed_rhythm", EIGHT_MM_FEED_RHYTHM))
    EIGHT_MM_PLACE_HEIGHT = str(d.get("eight_mm_place_height", EIGHT_MM_PLACE_HEIGHT))
    DEFAULT_FEEDER_POWER = str(d.get("feeder_power", DEFAULT_FEEDER_POWER))
    DEFAULT_PEELER_POWER = str(d.get("peeler_power", DEFAULT_PEELER_POWER))
    BOARD_SURFACE_MM = float(d.get("board_surface_mm", BOARD_SURFACE_MM))
    PRESS_FRACTION = float(d.get("press_percent", PRESS_FRACTION * 100)) / 100.0
    if "slots" in d:
        LEARNED_SLOTS.clear()
        for fid, p in d["slots"].items():
            LEARNED_SLOTS[str(fid)] = {int(i): str(v) for i, v in p.items()}


def bundled_settings_path():
    """settings.json next to the program -- how Options travel to another PC."""
    base = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "settings.json")


def load_settings():
    path = settings_path()
    if path and not os.path.exists(path) and os.environ.get("KFG_SETTINGS") is None:
        # first start on this PC: take the Options shipped beside the program,
        # and keep them as this PC's own from now on
        shipped = bundled_settings_path()
        if os.path.exists(shipped):
            try:
                with open(shipped, encoding="utf-8") as fh:
                    apply_settings(json.load(fh))
                save_settings()
                return True
            except (OSError, ValueError, TypeError, KeyError):
                pass
    if not path or not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as fh:
            apply_settings(json.load(fh))
        return True
    except (OSError, ValueError, TypeError, KeyError):
        return False


def save_settings():
    path = settings_path()
    if not path:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(current_settings(), fh, indent=2)
    return True


def apply_slots_to_program():
    """Puts the set-up slot values into every feeder of the current program."""
    n = sum(1 for f in machine_feeders() if apply_slot_profile(f))
    refresh_feeder_table()
    return n


OPTION_FIELDS = [
    # (label, getter, setter-key) -- shown in Options, in this order
    ("Speed (%)", "fb16"), ("Pick height (mm)", "fb9"), ("Pick delay (ms)", "fb10"),
    ("Place height (mm)", "fb11"), ("Place delay (ms)", "fb12"),
    ("Feeder power", "feeder_power"), ("Peeler power", "peeler_power"),
    ("8mm feeders (slots 1..N)", "eight_mm_count"), ("8mm fill from slot (centre)", "bank_centre"),
    ("8mm nozzles", "eight_mm_nozzles"), ("8mm feed rhythm", "eight_mm_feed_rhythm"),
    ("8mm place height (mm)", "eight_mm_place_height"),
    ("Big feeders from", "big_feeders_from"), ("Fiducial feeder", "fiducial_feeder"),
    ("Nozzle touches PCB at (mm)", "board_surface_mm"),
    ("Press into paste (% of height)", "press_percent"),
]


def open_options():
    popup = tk.Toplevel(root)
    popup.title("Options")
    popup.configure(bg=bg_color)
    popup.transient(root)
    cur = current_settings()
    values = {k: cur.get(k) for _, k in OPTION_FIELDS if not k.startswith("fb")}
    for _, k in OPTION_FIELDS:
        if k.startswith("fb"):
            values[k] = cur["fallback_feeder"].get(k[2:], "")
    slots = {fid: dict(p) for fid, p in LEARNED_SLOTS.items()}

    left = tk.LabelFrame(popup, text=" Defaults for new feeders and layout ", bg=bg_color,
                         fg=frame_title_color, font=("Segoe UI", 9, "bold"), padx=10, pady=8)
    left.pack(side="left", fill="y", padx=10, pady=10)
    vars_ = {}
    for i, (label, k) in enumerate(OPTION_FIELDS):
        create_label(left, label).grid(row=i, column=0, sticky="e", padx=5, pady=3)
        v = tk.StringVar(value=str(values[k]))
        create_entry(left, width=10, var=v).grid(row=i, column=1, sticky="w", padx=5, pady=3)
        vars_[k] = v

    right = tk.LabelFrame(popup, text=" Slots set up on the machine ", bg=bg_color,
                          fg=frame_title_color, font=("Segoe UI", 9, "bold"), padx=10, pady=8)
    right.pack(side="left", fill="both", expand=True, padx=(0, 10), pady=10)
    tk.Label(right, text="Pick X / Y, feeder angle, feeder and peeler power per slot. "
                         "Every output uses these for whatever part sits in the slot.",
             bg=bg_color, fg=fg_color, font=("Segoe UI", 9, "italic"),
             wraplength=520, justify="left").pack(anchor="w")
    cols = ("Slot", "Pick X", "Pick Y", "Angle", "Feeder power", "Peeler power")
    st = ttk.Treeview(right, columns=cols, show="headings", height=16)
    for c in cols:
        st.heading(c, text=c)
        st.column(c, width=85, anchor="w")
    st.pack(fill="both", expand=True, pady=6)

    def load_slots():
        st.delete(*st.get_children())
        for fid in sorted(slots, key=_feeder_sort_key):
            p = slots[fid]
            st.insert("", "end", iid=fid, values=(fid,) + tuple(p.get(i, "") for i in SLOT_PROFILE_FIELDS))

    def learn():
        path = filedialog.askopenfilename(
            title="Machine file written by the machine",
            filetypes=(("CSV Files", "*.csv"), ("All Files", "*.*")))
        if not path:
            return
        saved = dict(LEARNED_SLOTS)
        LEARNED_SLOTS.clear()
        LEARNED_SLOTS.update(slots)
        try:
            got = learn_slots_from_machine_file(path)
        except OSError as e:
            messagebox.showerror("Options", str(e))
            got = []
        slots.clear()
        slots.update(LEARNED_SLOTS)
        LEARNED_SLOTS.clear()
        LEARNED_SLOTS.update(saved)
        load_slots()
        messagebox.showinfo("Options", "%d slot(s) read from\n%s" % (len(got), path))

    def edit_slot(event=None):
        sel = st.selection()
        if not sel:
            return
        fid = sel[0]
        sub = tk.Toplevel(popup)
        sub.title("Slot %s" % fid)
        sub.configure(bg=bg_color)
        sub.transient(popup)
        evars = {}
        for j, (i, label) in enumerate(zip(SLOT_PROFILE_FIELDS, cols[1:])):
            create_label(sub, label).grid(row=j, column=0, sticky="e", padx=8, pady=4)
            v = tk.StringVar(value=slots[fid].get(i, ""))
            create_entry(sub, width=12, var=v).grid(row=j, column=1, padx=8, pady=4)
            evars[i] = v

        def ok():
            slots[fid] = {i: v.get().strip() for i, v in evars.items()}
            sub.destroy()
            load_slots()
        tk.Button(sub, text="OK", command=ok, bg=btn_bg, fg=btn_fg, width=10).grid(
            row=len(evars), column=1, sticky="e", padx=8, pady=8)

    def remove():
        for fid in st.selection():
            slots.pop(fid, None)
        load_slots()

    st.bind("<Double-1>", edit_slot)
    bar = tk.Frame(right, bg=bg_color)
    bar.pack(fill="x")
    create_btn(bar, "Learn from machine file...", learn).pack(side="left")
    create_btn(bar, "Edit", edit_slot).pack(side="left", padx=6)
    create_btn(bar, "Remove", remove).pack(side="left")
    apply_now = tk.BooleanVar(value=True)
    tk.Checkbutton(bar, text="Apply slots to the open program", variable=apply_now,
                   bg=bg_color, fg=fg_color, selectcolor=tree_bg, activebackground=bg_color,
                   activeforeground=fg_color).pack(side="left", padx=10)
    load_slots()

    def save():
        d = {"fallback_feeder": {}, "slots": {f: {str(i): v for i, v in p.items()}
                                              for f, p in slots.items()}}
        try:
            for _, k in OPTION_FIELDS:
                v = vars_[k].get().strip()
                if k.startswith("fb"):
                    d["fallback_feeder"][k[2:]] = v
                elif k in ("eight_mm_count", "big_feeders_from"):
                    d[k] = int(v)
                elif k in ("bank_centre", "board_surface_mm", "press_percent"):
                    d[k] = float(v)
                else:
                    d[k] = v
        except ValueError:
            messagebox.showerror("Options", "8mm feeders, centre and big-feeder start "
                                            "must be numbers.")
            return
        apply_settings(d)
        try:
            save_settings()
        except OSError as e:
            messagebox.showerror("Options", "Could not save the options:\n%s" % e)
        if apply_now.get() and machine_model["comps"]:
            apply_slots_to_program()
        popup.destroy()

    btns = tk.Frame(popup, bg=bg_color)
    btns.place(relx=1.0, rely=1.0, anchor="se", x=-12, y=-12)
    tk.Button(btns, text="Save", command=save, bg=btn_bg, fg=btn_fg, width=12,
              font=("Segoe UI", 9, "bold")).pack(side="right")
    tk.Button(btns, text="Cancel", command=popup.destroy, bg=entry_bg, fg=fg_color,
              width=10).pack(side="right", padx=6)
    popup.geometry("1000x600")


def confirm_drop_machine_layout(action):
    """True to go on. Warns when the feeders are the layout taken from the machine."""
    if not machine_model.get("stage1"):
        return True
    return messagebox.askyesno(
        action,
        "The feeders are the layout taken from the machine (stage 1).\n\n"
        "%s lays them out again by the rules, so the reels on the machine will no "
        "longer match the program.\n\nGo on anyway?" % action, icon="warning")


def arrange_feeders_clicked():
    if not machine_model["comps"]:
        messagebox.showinfo("Arrange Feeders", "There is no machine program to arrange.")
        return
    if not confirm_drop_machine_layout("Arrange Feeders"):
        return
    if not messagebox.askyesno(
            "Arrange Feeders",
            "Lay the feeders out again by the rules?\n\n"
            "  - fiducial on feeder 49, placement skipped\n"
            "  - busiest 8mm parts in the middle of 1-17: 7, 8, 6, 9, 5, 10 ...\n"
            "  - 8mm feeders: nozzles 1 and 3, feed rhythm 4\n"
            "  - big feeders (M7, SO-16, 1210 ...) from 20 up\n\n"
            "Placement order and coordinates stay as they are."):
        return
    moved = arrange_feeders()
    refresh_feeder_table()
    refresh_machine_comp_table()
    messagebox.showinfo("Arrange Feeders",
                        "%d part(s) moved to a new feeder. Teach any slot that has "
                        "no taught position before running." % moved)


def _feeder_sort_key(fid):
    try:
        return (0, int(fid), "")
    except (ValueError, TypeError):
        return (1, 0, str(fid))


def open_build_machine_dialog():
    """Maps every distinct part in the Pick & Place table onto a feeder, then builds."""
    if not rows:
        messagebox.showerror("Build Machine File",
                             "Load a Pick & Place file in the first tab.")
        return

    source_rows = pnp_rows_in_table_order()
    has_template = bool(machine_feeders())

    popup = tk.Toplevel(root)
    popup.title("Build Machine File from Pick & Place")
    popup.geometry("980x620")
    popup.configure(bg=bg_color)
    popup.transient(root)

    top = tk.Frame(popup, bg=bg_color, padx=15, pady=12)
    top.pack(fill="x")

    top_only_var = tk.BooleanVar(value=True)
    keep_board_var = tk.BooleanVar(value=has_template)

    info = tk.Label(top, text="", bg=bg_color, fg=fg_color, font=("Segoe UI", 9),
                    justify="left", wraplength=930)
    info.pack(anchor="w")

    template_note = tk.Label(
        top,
        text=("Using the loaded machine file as a template: taught feeder positions, "
              "board/mark/mirror rows and every column this dialog does not set are kept."
              if has_template else
              "No machine file is loaded, so there are no taught feeder positions. "
              "Each slot gets its estimated position from its feeder number and MUST be "
              "taught on the machine before running the program."),
        bg=bg_color, fg="#5ce65c" if has_template else "#ff9900",
        font=("Segoe UI", 9, "italic"), justify="left", wraplength=930)
    template_note.pack(anchor="w", pady=(6, 0))

    table_frame = tk.Frame(popup, bg=bg_color)
    table_frame.pack(fill="both", expand=True, padx=15)

    cols = ("Footprint", "Value", "Qty", "Feeder", "Nozzles", "Pick H", "Place H", "Speed", "Slot")
    scroll = ttk.Scrollbar(table_frame, orient="vertical")
    scroll.pack(side="right", fill="y")
    part_tree = ttk.Treeview(table_frame, columns=cols, show="headings",
                             yscrollcommand=scroll.set)
    scroll.config(command=part_tree.yview)
    for c, w in zip(cols, (230, 150, 45, 60, 70, 65, 70, 60, 130)):
        part_tree.heading(c, text=c)
        part_tree.column(c, width=w, anchor="w")
    part_tree.pack(side="left", fill="both", expand=True)

    state = {"parts": []}

    def rebuild_parts():
        chosen = [r for r in source_rows
                  if not top_only_var.get()
                  or str(r[8]).strip().lower() in ("toplayer", "top", "t", "")]
        state["rows"] = chosen
        state["parts"] = auto_assign_feeders(collect_pnp_parts(chosen))
        load_table()
        skipped = len(source_rows) - len(chosen)
        info.config(text="%d components  |  %d distinct parts  |  %s"
                         % (len(chosen), len(state["parts"]),
                            "%d bottom-layer component(s) excluded" % skipped if skipped
                            else "all components included"))

    def load_table():
        part_tree.delete(*part_tree.get_children())
        for i, e in enumerate(state["parts"]):
            part_tree.insert("", "end", iid="p%d" % i, values=(
                e["footprint"], e["value"], len(e["rows"]), e["feeder"],
                e["nozzles"], e["pick"], e["place"], e["speed"], e["source"]))

    def selected_entry():
        sel = part_tree.selection()
        if not sel:
            return None
        return state["parts"][int(sel[0][1:])]

    def edit_entry(event=None):
        entry = selected_entry()
        if entry is None:
            messagebox.showinfo("Edit Assignment", "Select a part first.")
            return

        sub = tk.Toplevel(popup)
        sub.title("%s / %s" % (entry["footprint"], entry["value"]))
        sub.configure(bg=bg_color)
        sub.transient(popup)
        frame = tk.Frame(sub, bg=bg_color, padx=15, pady=15)
        frame.pack(fill="both", expand=True)

        tk.Label(frame, text="%s  /  %s   (%d components)"
                             % (entry["footprint"], entry["value"], len(entry["rows"])),
                 bg=bg_color, fg=frame_title_color,
                 font=("Segoe UI", 10, "bold")).grid(row=0, column=0, columnspan=4,
                                                     sticky="w", pady=(0, 10))

        vars_ = {}
        for i, (key, label) in enumerate((("feeder", "Feeder ID:"), ("pick", "Pick Height (mm):"),
                                          ("place", "Place Height (mm):"), ("speed", "Speed (%):"))):
            create_label(frame, label).grid(row=1 + i, column=0, sticky="e", padx=5, pady=4)
            v = tk.StringVar(value=str(entry[key]))
            create_entry(frame, width=14, var=v).grid(row=1 + i, column=1, sticky="w", padx=5, pady=4)
            vars_[key] = v

        create_label(frame, "Nozzles:").grid(row=1, column=2, sticky="e", padx=5, pady=4)
        holder = tk.Frame(frame, bg=bg_color)
        holder.grid(row=1, column=3, sticky="w", padx=5, pady=4)
        selected = parse_nozzles(entry["nozzles"]) or [1]
        nozzle_vars = {}
        for n in (1, 2, 3, 4):
            v = tk.BooleanVar(value=n in selected)
            nozzle_vars[n] = v
            tk.Checkbutton(holder, text=str(n), variable=v, bg=bg_color, fg=fg_color,
                           selectcolor=tree_bg, activebackground=bg_color,
                           activeforeground=fg_color).pack(side="left")

        def save():
            chosen = [n for n, v in nozzle_vars.items() if v.get()]
            if not chosen:
                messagebox.showerror("Invalid Nozzles", "Select at least one nozzle.")
                return
            fid = vars_["feeder"].get().strip()
            if not fid:
                messagebox.showerror("Invalid Feeder", "Feeder ID cannot be empty.")
                return
            clash = [e for e in state["parts"]
                     if e is not entry and str(e["feeder"]) == fid]
            if clash and not messagebox.askyesno(
                    "Feeder Already Used",
                    "Feeder %s is already assigned to %s / %s.\n\nUse it for both?"
                    % (fid, clash[0]["footprint"], clash[0]["value"])):
                return
            for key in ("pick", "place", "speed"):
                text = vars_[key].get().strip()
                if text:
                    try:
                        float(text)
                    except ValueError:
                        messagebox.showerror("Invalid Value", "'%s' is not a number." % text)
                        return
                entry[key] = text
            entry["feeder"] = fid
            entry["nozzles"] = format_nozzles(chosen)
            sub.destroy()
            load_table()

        tk.Button(frame, text="Save", command=save, bg=btn_bg, fg=btn_fg, width=12,
                  font=("Segoe UI", 9, "bold")).grid(row=6, column=3, sticky="e", pady=(12, 0))

    part_tree.bind("<Double-1>", edit_entry)

    opts = tk.Frame(popup, bg=bg_color)
    opts.pack(fill="x", padx=15)
    tk.Checkbutton(opts, text="Top layer only", variable=top_only_var, bg=bg_color,
                   fg=fg_color, selectcolor=tree_bg, activebackground=bg_color,
                   activeforeground=fg_color,
                   command=lambda: rebuild_parts()).pack(side="left")
    tk.Checkbutton(opts, text="Keep board / mark / mirror rows from the loaded machine file",
                   variable=keep_board_var, bg=bg_color, fg=fg_color, selectcolor=tree_bg,
                   activebackground=bg_color, activeforeground=fg_color,
                   state="normal" if has_template else "disabled").pack(side="left", padx=20)

    btns = tk.Frame(popup, bg=bg_color, padx=15, pady=12)
    btns.pack(fill="x")
    create_btn(btns, "Re-assign Feeders", lambda: (rebuild_parts())).pack(side="left")
    create_btn(btns, "Edit Selected", edit_entry).pack(side="left", padx=8)

    def do_build():
        if not state["parts"]:
            messagebox.showerror("Build Machine File", "Nothing to build.")
            return
        if not confirm_drop_machine_layout("Build"):
            return
        untaught = [e for e in state["parts"] if e["source"] == "new (needs teaching)"]
        message = ("Build a machine program with %d feeders and %d components?"
                   % (len(state["parts"]), len(state["rows"])))
        if untaught:
            message += ("\n\n%d feeder(s) have no taught position and will be given "
                        "their slot's estimated position. Teach them on the machine "
                        "before running." % len(untaught))
        if not messagebox.askyesno("Build Machine File", message):
            return

        build_machine_from_pnp(state["parts"], state["rows"],
                               keep_board_records=keep_board_var.get())
        popup.destroy()
        machine_notebook.select(0)
        messagebox.showinfo(
            "Build Machine File",
            "Built %d feeders and %d components.\n\nReview the Feeder Setup tab, then "
            "use Export Machine File." % (len(machine_feeders()),
                                          len(machine_model["comps"])))

    tk.Button(btns, text="Build", command=do_build, bg=btn_bg, fg=btn_fg, width=14,
              font=("Segoe UI", 9, "bold")).pack(side="right")
    tk.Button(btns, text="Cancel", command=popup.destroy, bg=entry_bg, fg=fg_color,
              width=12, font=("Segoe UI", 9)).pack(side="right", padx=8)

    rebuild_parts()


# =========================================================
# Speed: feeder placement and placement sequence
# =========================================================
#
# Cost model. A gantry drives X and Y on separate motors at the same time, so
# a move takes about as long as its longer axis: max(|dx|, |dy|), not the
# straight-line distance. All travel below is measured that way.
#
# Two decisions move the number:
#
#   1. Which slot each part's reel goes in. The head shuttles between the reel
#      and the board for every pick, so a busy part far from the board pays that
#      distance on every placement. This is an assignment problem -- each part
#      to one slot, each slot to one part, minimum total travel -- solved
#      exactly with the Hungarian method, subject to the tape fitting the slot.
#
#   2. The order of placements. Shortest bodies first is a hard rule. Inside
#      one height, parts are taken slot by slot along the bank, each part's
#      placements routed nearest-neighbour from its reel, and consecutive
#      placements rotate through the nozzles the feeder allows so the head can
#      pick several before travelling.
#
# No absolute time is claimed: that needs the machine's real speeds and
# accelerations. Travel is reported in millimetres and as a percentage, which
# carries over to cycle time in proportion.

# Slot id -> tape width the slot takes (mm), or None when not stated.
slot_widths = {}
# The ones worked out from the reel in the slot rather than set by hand: worked
# out again every time (reels move) and never saved with the project.
slot_width_inferred = set()

# The fields of a stack row that belong to the slot -- where it is and how it is
# mounted -- as opposed to the part loaded in it. When a reel moves, these stay.
SLOT_OWNED_FIELDS = (0, 1, 2, 4, 5, 6, 18, 19)   # ... and feeder / peeler power

TAPE_WIDTH_RULES = [
    # (pattern, tape mm) -- first match wins. Only claim 8mm where it is sure;
    # an over-wide reel offered to an 8mm slot is worse than no suggestion.
    (r"(?<![0-9])(?:0201|0402|0603|0805|1206)(?![0-9])", 8),
    (r"SOT[_\-]?(?:23|323|363|523)|SOT23",                8),
    (r"SOD[_\-]?(?:123|323|523)",                         8),
    (r"SMD[_\- ]?4\b|GULL",                               16),
    (r"(?<![0-9])3216(?![0-9])|CASE[_\- ]?A\b",          8),
    (r"(?<![0-9])3528(?![0-9])|CASE[_\- ]?B\b",         12),  # M7 needs a big feeder
    (r"(?<![0-9])(?:1210|1812|2010|2512)(?![0-9])",      12),
    (r"(?<![0-9])(?:6032|7343)(?![0-9])|CASE[_\- ]?[CD]\b", 12),
    (r"SOT[_\-]?(?:89|223)",                             12),
    (r"\bSM[ABC]\b",                                     12),
    (r"MSOP|TSSOP|\bSO[_\-]?8\b|SOIC[_\-]?8|SO_8",        12),
    (r"\bSO[_\-]?1[46]\b|SOIC[_\-]?1[46]",                16),
    (r"\bSO[_\-]?(?:18|20|24|28)\b|SOIC[_\-]?(?:18|20|24|28)", 24),
]


def tape_width_for(footprint):
    """Carrier tape width a footprint usually ships on, or None if unsure."""
    name = str(footprint).upper()
    for pattern, mm in TAPE_WIDTH_RULES:
        if re.search(pattern, name):
            return mm
    return None


def cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def comp_is_placed(c):
    return (not str(field_at(c, 3)).strip().upper().startswith("FID")
            and str(field_at(c, 9)).strip().lower() != "yes")


def comp_xy(c):
    try:
        return float(field_at(c, 6)), float(field_at(c, 7))
    except ValueError:
        return None


def comp_part_key(c):
    return part_match_key("%s/%s" % (field_at(c, 5), field_at(c, 4)))


def board_bounds():
    pts = [comp_xy(c) for c in machine_model["comps"] if comp_is_placed(c)]
    pts = [p for p in pts if p]
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), max(xs), min(ys), max(ys)


def slot_side(x, y, bounds):
    x0, x1, y0, y1 = bounds
    if x > x1:
        return "RIGHT"
    if x < x0:
        return "LEFT"
    return "FRONT" if y > y1 else ("BACK" if y < y0 else "INSIDE")


def feeder_slots(include_empty=False):
    """
    Every slot the plan may use: those in the program, plus (include_empty) the 8mm bank and
    the slots set up on the machine (Options) that the program has no row
    for -- an empty slot is still a place to put a reel. The fiducial
    pseudo-feeder is reserved.
    """
    bounds = board_bounds()
    out = []
    for f in machine_feeders():
        part = str(field_at(f, 8)).strip().upper()
        try:
            x, y = float(field_at(f, 4)), float(field_at(f, 5))
        except ValueError:
            continue
        out.append({
            "id": str(field_at(f, 1)),
            "x": x, "y": y,
            "side": slot_side(x, y, bounds) if bounds else "?",
            "reserved": part.startswith("FIDUCIAL"),
            "row": f,
        })
    if not include_empty:
        return out
    have = {s["id"] for s in out}
    extra = [i for i in list(EIGHT_MM_BANK) + sorted(LEARNED_SLOTS, key=_feeder_sort_key)
             if i not in have and i != FIDUCIAL_FEEDER_ID]
    for sid in dict.fromkeys(extra):
        x, y, _ = slot_position_for(sid)
        if any(cheb((x, y), (s["x"], s["y"])) < 3.0 for s in out):
            continue                     # would sit on a feeder already there
        out.append({"id": sid, "x": x, "y": y,
                    "side": slot_side(x, y, bounds) if bounds else "?",
                    "reserved": False, "row": None})
    return out


def default_slot_widths(slots):
    """
    The right-hand bank holds 8mm feeders (0402 to 1206). Elsewhere, a slot
    already carrying a reel of known tape width is a feeder of that width --
    feeders are made per width. Empty slots elsewhere stay unstated.
    """
    for s in slots:
        if s["id"] in slot_widths and s["id"] not in slot_width_inferred:
            continue                     # set by hand: kept
        slot_width_inferred.add(s["id"])
        if s["side"] == "RIGHT" or s["id"] in EIGHT_MM_BANK:
            slot_widths[s["id"]] = 8
            continue
        if s["row"] is None:
            slot_widths[s["id"]] = None
            continue
        footprint = str(field_at(s["row"], 8)).split("/", 1)[0]
        slot_widths[s["id"]] = tape_width_for(footprint) if footprint else None


def suspicious_slots(slots, tolerance=3.0):
    """Pairs of slots taught almost on top of each other -- likely never taught."""
    pairs = []
    for i in range(len(slots)):
        for j in range(i + 1, len(slots)):
            a, b = slots[i], slots[j]
            if cheb((a["x"], a["y"]), (b["x"], b["y"])) < tolerance:
                pairs.append((a["id"], b["id"]))
    return pairs


def hungarian(cost):
    """
    Minimum-cost assignment of every row to a distinct column (rows <= cols).
    Costs must be finite. Returns the column chosen for each row.
    """
    n, m = len(cost), len(cost[0])
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [float("inf")] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = float("inf")
            j1 = 0
            row = cost[i0 - 1]
            for j in range(1, m + 1):
                if not used[j]:
                    cur = row[j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    assign = [-1] * n
    for j in range(1, m + 1):
        if p[j]:
            assign[p[j] - 1] = j - 1
    return assign


def current_parts():
    """
    The parts in the program, with where they are now and what they need.
    Nozzles and tuned settings travel with a part, so they come from its feeder.
    """
    feeders = {str(field_at(f, 1)): f for f in machine_feeders()}
    parts = {}
    for c in machine_model["comps"]:
        if not comp_is_placed(c):
            continue
        key = comp_part_key(c)
        xy = comp_xy(c)
        if xy is None:
            continue
        entry = parts.get(key)
        if entry is None:
            fid = str(field_at(c, 1))
            feeder = feeders.get(fid)
            entry = parts[key] = {
                "key": key,
                "footprint": str(field_at(c, 5)),
                "value": str(field_at(c, 4)),
                "slot": fid if feeder is not None else None,
                "nozzles": (parse_nozzles(field_at(feeder, 3)) if feeder is not None
                            else None) or [1],
                "points": [],
                "comps": [],
            }
        entry["points"].append(xy)
        entry["comps"].append(c)
    suspects = suspect_part_keys()
    for e in parts.values():
        e["qty"] = len(e["points"])
        e["height"] = component_height(e["footprint"], e["value"], suspects)
        e["tape"] = tape_width_for(e["footprint"])
    return list(parts.values())


def slot_fits(part, slot):
    if slot["reserved"]:
        return False
    if part["tape"] is None:
        # Nothing says what this reel needs, only that it fits where it is now.
        return slot["id"] == part["slot"]
    width = slot_widths.get(slot["id"])
    if width is None:
        return True                    # unstated slot: allowed, and flagged
    # feeders are made per tape width: an 8mm reel does not run in a 16mm feeder
    return part["tape"] == width


def suggest_feeder_layout(parts, slots, keep_bonus=0.5):
    """
    Assigns parts to slots for the least head travel, with every part's travel
    weighted by how many picks it costs (fewer when it can use more nozzles per
    trip). Parts that do not fit anywhere come back unassigned -- the solver
    gives up the low-volume parts first, because those cost least to feed by
    hand or from a spare feeder.
    """
    if not parts or not slots:
        return {}, list(parts)

    far = 2 * max(cheb((s["x"], s["y"]), pt)
                  for s in slots for p in parts for pt in p["points"]) + 1.0
    BIG = far * 1e6

    cost = []
    for p in parts:
        weight = 1.0 / max(1, len(p["nozzles"]))
        row = []
        for s in slots:
            if not slot_fits(p, s):
                row.append(BIG)
                continue
            travel = sum(cheb((s["x"], s["y"]), pt) for pt in p["points"])
            # keeping a reel where it is breaks ties, so equal options do not
            # make anyone walk to the machine for nothing
            bonus = keep_bonus if s["id"] == p["slot"] else 0.0
            row.append(weight * travel - bonus)
        # one "no slot" column per part, so the problem is always solvable
        row.extend([weight * p["qty"] * far] * len(parts))
        cost.append(row)

    picks = hungarian(cost)
    layout = {}
    unassigned = []
    for p, j in zip(parts, picks):
        if j < len(slots) and cost[parts.index(p)][j] < BIG:
            layout[p["key"]] = slots[j]["id"]
        else:
            unassigned.append(p)
    return layout, unassigned


def plan_sequence(parts, layout, slot_xy, slot_side_of=None):
    """
    Placement order. Shortest bodies first is a hard rule. Inside one height
    the next part is the one whose reel is nearest the head, and each part is
    placed in trips: the head picks as many as the feeder allows nozzles
    (1 and 3 for the 8mm bank), starting with the placement nearest the reel
    and chaining nearest-neighbour, places them, and goes back to the reel.
    Returns (comp row, slot id, nozzle).
    """
    slot_side_of = slot_side_of or {}
    groups = []
    for p in parts:
        sid = layout.get(p["key"], p["slot"])
        if sid is None or sid not in slot_xy:
            continue
        groups.append((p, sid))

    plan = []
    here = None
    for h in sorted({p["height"] for p, _ in groups}):
        pending = [g for g in groups if g[0]["height"] == h]
        while pending:
            if here is None:
                pick = min(pending, key=lambda g: (slot_side_of.get(g[1], ""),
                                                   slot_xy[g[1]][1], g[0]["key"]))
            else:
                pick = min(pending, key=lambda g: (cheb(here, slot_xy[g[1]]), g[0]["key"]))
            pending.remove(pick)
            p, sid = pick
            reel = slot_xy[sid]
            trip = max(1, len(p["nozzles"]))
            todo = list(zip(p["comps"], p["points"]))
            cursor = 0
            while todo:
                pos = reel
                for _ in range(trip):
                    if not todo:
                        break
                    k = min(range(len(todo)), key=lambda i: cheb(pos, todo[i][1]))
                    comp, pt = todo.pop(k)
                    plan.append((comp, sid, p["nozzles"][cursor % len(p["nozzles"])]))
                    cursor += 1
                    pos = pt
                here = pos
    return plan


def estimate_travel(sequence, slot_xy, nozzles_for):
    """
    Head travel for a sequence of (comp, slot id, nozzle). Consecutive
    placements from the same reel are picked together, up to as many as the
    feeder allows nozzles, then placed in order before the next trip.
    """
    total = 0.0
    here = None
    i = 0
    while i < len(sequence):
        comp, sid, _ = sequence[i]
        batch = nozzles_for.get(sid, 1)
        group = [sequence[i]]
        while (len(group) < batch and i + len(group) < len(sequence)
               and sequence[i + len(group)][1] == sid):
            group.append(sequence[i + len(group)])
        reel = slot_xy[sid]
        if here is not None:
            total += cheb(here, reel)
        here = reel
        for c, _, _ in group:
            pt = comp_xy(c)
            total += cheb(here, pt)
            here = pt
        i += len(group)
    return total


def current_sequence(slot_xy):
    seq = []
    for c in machine_model["comps"]:
        if comp_is_placed(c) and str(field_at(c, 1)) in slot_xy and comp_xy(c):
            seq.append((c, str(field_at(c, 1)), str(field_at(c, 2))))
    return seq


def apply_speed_plan(parts, layout, plan, reorder=True):
    """
    Moves each part's reel and tuned settings into its new slot, and rewrites
    the placement order. The slot keeps its own position and mounting angle.
    Returns the parts whose reel changed bank, whose angle should be checked.
    """
    feeders = {str(field_at(f, 1)): f for f in machine_feeders()}
    slots = {s["id"]: s for s in feeder_slots()}

    # snapshot the part-owned fields before any slot is overwritten
    settings = {}
    for p in parts:
        src = feeders.get(p["slot"]) if p["slot"] else None
        settings[p["key"]] = list(src) if src is not None else None

    moved_bank = []
    # only reels that actually move leave a slot behind
    vacated = {p["slot"] for p in parts
               if p["slot"] and layout.get(p["key"], p["slot"]) != p["slot"]}
    filled = set(layout.values())

    for p in parts:
        sid = layout.get(p["key"])
        if sid is None:
            continue
        if sid not in feeders:
            # an empty slot the program had no row for gets one, at its place
            dest = _blank_feeder_row()
            set_field(dest, 1, sid)
            x, y, a = slot_position_for(sid)
            for i, v in ((4, x), (5, y), (6, a)):
                set_field(dest, i, "%.2f" % v)
            stacks = [i for i, r in enumerate(machine_model["pre_records"])
                      if r and r[0] == "stack"]
            machine_model["pre_records"].insert((stacks[-1] + 1) if stacks else 0, dest)
            feeders[sid] = dest
            slots[sid] = {"id": sid, "x": x, "y": y, "side": slot_side(x, y, board_bounds())
                          if board_bounds() else "?"}
        dest = feeders[sid]
        src = settings[p["key"]]
        if src is not None:
            for i in range(max(len(src), len(dest))):
                if i in SLOT_OWNED_FIELDS:
                    continue
                set_field(dest, i, field_at(src, i))
        set_field(dest, 3, format_nozzles(p["nozzles"]))
        set_field(dest, 8, "%s/%s" % (p["footprint"], p["value"]))
        apply_slot_profile(dest)
        if p["slot"] in slots and slots[p["slot"]]["side"] != slots[sid]["side"]:
            moved_bank.append(p)

    # a slot a part moved out of, and nothing moved into, is dropped: no
    # empty feeders in the program
    for sid in vacated - filled:
        if sid in feeders and feeders[sid] in machine_model["pre_records"]:
            machine_model["pre_records"].remove(feeders[sid])

    if not reorder:
        # feeders only: each placement follows its reel, the order stays
        for p in parts:
            sid = layout.get(p["key"])
            if sid is None:
                continue
            for c in p["comps"]:
                set_field(c, 1, sid)
                if int(str(field_at(c, 2)) or 0) not in p["nozzles"]:
                    set_field(c, 2, str(p["nozzles"][0]))
        machine_feeders_sorted = sorted(
            [r for r in machine_model["pre_records"] if r and r[0] == "stack"],
            key=lambda f: (not feeder_is_fiducial(f), _feeder_sort_key(field_at(f, 1))))
        machine_model["pre_records"] = machine_feeders_sorted + machine_board_records()
        return moved_bank

    planned = {id(c): (sid, nz) for c, sid, nz in plan}
    ordered = [c for c, _, _ in plan]
    for c, sid, nz in plan:
        set_field(c, 1, sid)
        set_field(c, 2, str(nz))

    fids = [c for c in machine_model["comps"]
            if str(field_at(c, 3)).strip().upper().startswith("FID")]
    rest = [c for c in machine_model["comps"]
            if id(c) not in planned and c not in fids]
    machine_model["comps"][:] = fids + ordered + rest
    machine_model["pre_records"] = sorted(
        [r for r in machine_model["pre_records"] if r and r[0] == "stack"],
        key=lambda f: (not feeder_is_fiducial(f), _feeder_sort_key(field_at(f, 1)))) \
        + machine_board_records()
    return moved_bank


def open_speed_optimizer():
    if not machine_is_loaded() or not machine_model["comps"]:
        messagebox.showerror("Optimize for Speed",
                             "Import a machine file, or build one from Pick & Place, first.")
        return

    slots = feeder_slots(include_empty=True)
    if not slots:
        messagebox.showerror("Optimize for Speed",
                             "The machine file has no taught feeder slots to plan with.")
        return
    default_slot_widths(slots)

    popup = tk.Toplevel(root)
    popup.title("Optimize for Speed")
    popup.geometry("1080x760")
    popup.configure(bg=bg_color)
    popup.transient(root)

    tk.Label(popup,
             text="Suggests which slot each reel goes in and re-orders the placements for "
                  "the least head travel, keeping shortest parts first. Travel is measured "
                  "as the longer of the X and Y moves, since a gantry drives both at once.",
             bg=bg_color, fg=fg_color, font=("Segoe UI", 9), wraplength=1040,
             justify="left").pack(anchor="w", padx=15, pady=(12, 6))

    warn = suspicious_slots(slots)
    if warn:
        tk.Label(popup,
                 text="Slots taught within 3mm of each other: %s. Two feeders cannot sit "
                      "in the same place, so these were probably never taught. Teach them "
                      "before trusting a plan that uses them."
                      % ",  ".join("%s & %s" % pr for pr in warn),
                 bg=bg_color, fg="#ff6666", font=("Segoe UI", 9, "bold"),
                 wraplength=1040, justify="left").pack(anchor="w", padx=15, pady=(0, 6))

    body = tk.Frame(popup, bg=bg_color)
    body.pack(fill="both", expand=True, padx=15)

    # ---- slots ----
    left = tk.LabelFrame(body, text=" Feeder slots ", bg=bg_color, fg=frame_title_color,
                         font=("Segoe UI", 9, "bold"), padx=8, pady=6)
    left.pack(side="left", fill="y")
    s_cols = ("Slot", "Side", "Tape", "Now holds")
    s_tree = ttk.Treeview(left, columns=s_cols, show="headings", height=18)
    for c, w in zip(s_cols, (45, 60, 55, 190)):
        s_tree.heading(c, text=c)
        s_tree.column(c, width=w, anchor="w")
    s_tree.pack(fill="y", expand=True)

    def load_slots():
        s_tree.delete(*s_tree.get_children())
        for s in sorted(slots, key=lambda s: (s["side"], s["y"])):
            w = slot_widths.get(s["id"])
            s_tree.insert("", "end", iid=tv_key(s["id"]), values=(
                s["id"], s["side"],
                "fiducial" if s["reserved"] else ("%d mm" % w if w else "any"),
                (str(field_at(s["row"], 8)) if s["row"] is not None else "") or "-"))

    load_slots()

    wrow = tk.Frame(left, bg=bg_color)
    wrow.pack(fill="x", pady=(6, 0))
    create_label(wrow, "Tape width:").pack(side="left")
    width_var = tk.StringVar(value="8")
    ttk.Combobox(wrow, textvariable=width_var, values=["8", "12", "16", "24", "any"],
                 state="readonly", width=6).pack(side="left", padx=6)

    def set_width():
        chosen = [k[len(TV_KEY_PREFIX):] for k in s_tree.selection()]
        if not chosen:
            return
        for sid in chosen:
            slot_widths[sid] = None if width_var.get() == "any" else int(width_var.get())
            slot_width_inferred.discard(sid)
        load_slots()

    create_btn(wrow, "Set", set_width).pack(side="left")

    # ---- results ----
    right = tk.Frame(body, bg=bg_color)
    right.pack(side="left", fill="both", expand=True, padx=(12, 0))

    summary = tk.Label(right, text="Press Compute Plan.", bg=bg_color, fg=fg_color,
                       font=("Consolas", 10), justify="left", anchor="w")
    summary.pack(fill="x")

    r_cols = ("Part", "Qty", "Height", "Tape", "Now", "Suggested")
    r_tree = ttk.Treeview(right, columns=r_cols, show="headings", height=14)
    for c, w in zip(r_cols, (300, 45, 65, 50, 55, 80)):
        r_tree.heading(c, text=c)
        r_tree.column(c, width=w, anchor="w")
    r_tree.pack(fill="both", expand=True, pady=(8, 0))
    r_tree.tag_configure("moved", foreground="#5ce65c")
    r_tree.tag_configure("none", foreground="#ff6666")

    notes = tk.Text(right, height=7, bg=tree_bg, fg=fg_color, font=("Consolas", 9),
                    relief="flat", wrap="word")
    notes.pack(fill="x", pady=(8, 0))

    state = {}
    use_doubtful = tk.BooleanVar(value=False)
    mode_var = tk.StringVar(value="both")

    def compute():
        parts = current_parts()
        # With a stage layout taken from the machine only stage 1 is on the
        # machine now; the later stages' parts and their slots are left alone.
        pinned = set(machine_model.get("stage1") or [])
        if pinned:
            parts = [p for p in parts if p["key"] in pinned or p["tape"] != 8]
        keys = {p["key"] for p in parts}
        foreign = {s["id"] for s in slots if s["row"] is not None
                   and str(field_at(s["row"], 8)).strip()
                   and not s["reserved"]
                   and part_match_key(field_at(s["row"], 8)) not in keys}
        doubtful = {sid for pr in warn for sid in pr}
        # A part already sitting in a doubtful slot keeps it -- the plan simply
        # will not send anything new there.
        occupied = {p["slot"] for p in parts}
        live = [s for s in slots
                if (use_doubtful.get() or s["id"] not in doubtful or s["id"] in occupied)
                and s["id"] not in foreign]
        slot_xy = {s["id"]: (s["x"], s["y"]) for s in slots}
        side_of = {s["id"]: s["side"] for s in slots}

        layout, unassigned = suggest_feeder_layout(parts, live)
        nozzles_now = {}
        for f in machine_feeders():
            nz = parse_nozzles(field_at(f, 3)) or [1]
            nozzles_now[str(field_at(f, 1))] = len(nz)
        nozzles_new = dict(nozzles_now)
        for p in parts:
            if p["key"] in layout:
                nozzles_new[layout[p["key"]]] = len(p["nozzles"])

        before = estimate_travel(current_sequence(slot_xy), slot_xy, nozzles_now)
        resequenced = estimate_travel(
            plan_sequence(parts, {}, slot_xy, side_of), slot_xy, nozzles_now)
        plan = plan_sequence(parts, layout, slot_xy, side_of)
        after = estimate_travel(plan, slot_xy, nozzles_new)

        state.update(parts=parts, layout=layout, plan=plan,
                     plan_same=plan_sequence(parts, {}, slot_xy, side_of))

        def pct(x):
            return 100.0 * (before - x) / before if before else 0.0

        summary.config(text=(
            "Head travel, as the program stands now   %9.0f mm\n"
            "Same reels, placements re-ordered        %9.0f mm   %5.1f%% less\n"
            "Reels moved as suggested, re-ordered     %9.0f mm   %5.1f%% less"
            % (before, resequenced, pct(resequenced), after, pct(after))))

        r_tree.delete(*r_tree.get_children())
        for p in sorted(parts, key=lambda p: (p["height"], -p["qty"])):
            sid = layout.get(p["key"])
            r_tree.insert("", "end", values=(
                "%s / %s" % (p["footprint"], p["value"]), p["qty"],
                "?" if p["height"] == float("inf") else "%.2f" % p["height"],
                "%d" % p["tape"] if p["tape"] else "?",
                p["slot"] or "-", sid or "none"),
                tags=("none",) if sid is None else
                     (("moved",) if sid != p["slot"] else ()))

        lines = []
        by_slot = {}
        for p in parts:
            if p["key"] in layout:
                by_slot[layout[p["key"]]] = p
        lines.append("LOADING LIST")
        for sid in sorted(by_slot, key=_feeder_sort_key):
            p = by_slot[sid]
            lines.append("  slot %-3s  %-28s %-16s x%d"
                         % (sid, p["footprint"][:28], p["value"][:16], p["qty"]))
        if unassigned:
            lines.append("")
            lines.append("NO SLOT (feed by hand or add a feeder):")
            for p in unassigned:
                why = ("tape width unknown" if p["tape"] is None
                       else "needs %dmm tape" % p["tape"])
                lines.append("  %-28s %-16s x%d  (%s)"
                             % (p["footprint"][:28], p["value"][:16], p["qty"], why))
        unstated = sorted({sid for sid in by_slot if slot_widths.get(sid) is None},
                          key=_feeder_sort_key)
        if unstated:
            lines.append("")
            lines.append("Slots with no tape width set, check the reel fits: %s"
                         % ", ".join(unstated))
        notes.delete("1.0", "end")
        notes.insert("1.0", "\n".join(lines))

    def apply_plan():
        if not state:
            compute()
        mode = mode_var.get()
        question = {
            "order": "Re-order the placements for speed? The feeders stay as they are.",
            "feeders": "Move the reels to the suggested slots? The placement order stays.",
            "both": "Move the reels to the suggested slots and re-order the placements?",
        }[mode]
        if not messagebox.askyesno(
                "Apply Plan", question + "\n\nEach part's tuned settings move with its "
                "reel; each slot keeps its own position, angle and feeder / peeler power."):
            return
        if mode == "order":
            moved_bank = apply_speed_plan(state["parts"], {}, state["plan_same"])
        else:
            moved_bank = apply_speed_plan(state["parts"], state["layout"], state["plan"],
                                          reorder=(mode == "both"))
        refresh_feeder_table()
        refresh_machine_comp_table()
        popup.destroy()
        msg = "Plan applied. Load the reels as in the loading list, then export."
        if moved_bank:
            msg += ("\n\nThese moved to the other side of the machine, where feeders are "
                    "mounted the other way round. The angle now comes from the new slot -- "
                    "check orientation on the first board:\n  " +
                    "\n  ".join("%s / %s" % (p["footprint"], p["value"])
                                for p in moved_bank))
        messagebox.showinfo("Optimize for Speed", msg)

    btns = tk.Frame(popup, bg=bg_color)
    btns.pack(fill="x", padx=15, pady=12)
    for text, val in (("Order only (feeders stay)", "order"),
                      ("Feeders only (order stays)", "feeders"),
                      ("Both", "both")):
        tk.Radiobutton(btns, text=text, variable=mode_var, value=val, bg=bg_color,
                       fg=fg_color, selectcolor=tree_bg, activebackground=bg_color,
                       activeforeground=fg_color, font=("Segoe UI", 9)).pack(side="left", padx=4)
    if warn:
        tk.Checkbutton(btns, text="Use the slots that look untaught",
                       variable=use_doubtful, command=compute,
                       bg=bg_color, fg=fg_color, selectcolor=tree_bg,
                       activebackground=bg_color, activeforeground=fg_color,
                       font=("Segoe UI", 9)).pack(side="left")
    tk.Button(btns, text="Apply Plan", command=apply_plan, bg=btn_bg, fg=btn_fg,
              width=16, font=("Segoe UI", 9, "bold")).pack(side="right")
    tk.Button(btns, text="Compute Plan", command=compute, bg=entry_bg, fg=fg_color,
              width=16, font=("Segoe UI", 9)).pack(side="right", padx=8)

    compute()


# ==========================================
# Machine File Tab
# ==========================================
tab_machine = tk.Frame(notebook, bg=bg_color)
notebook.add(tab_machine, text="Machine File")

machine_btn_frame = tk.Frame(tab_machine, bg=bg_color)
machine_btn_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(6, 4))


def _tool_group(title):
    box = tk.LabelFrame(machine_btn_frame, text=" %s " % title, bg=bg_color,
                        fg=frame_title_color, font=("Segoe UI", 9, "bold"), padx=6, pady=4)
    box.pack(side="left", padx=(0, 10), fill="y")
    return box


# in the order the work is done: get a program, tune it, send it
_g1 = _tool_group("1  Get the program")
create_btn(_g1, "Build From Pick & Place", open_build_machine_dialog).pack(side="left", padx=3)
create_btn(_g1, "Import Machine File", import_machine_file).pack(side="left", padx=3)
create_btn(_g1, "Take Layout From Machine File", adopt_layout_clicked).pack(side="left", padx=3)
_g2 = _tool_group("2  Tune")
create_btn(_g2, "Package Heights", open_package_library).pack(side="left", padx=3)
create_btn(_g2, "Optimize for Speed", open_speed_optimizer).pack(side="left", padx=3)
_g3 = _tool_group("3  To the machine")
_export_stages_btn = create_btn(_g3, "Export Stages", export_stages_clicked)
_export_stages_btn.config(bg="#2e7d32", activebackground="#388e3c",
                          font=("Segoe UI", 9, "bold"))
_export_stages_btn.pack(side="left", padx=3)
create_btn(_g3, "Export Machine File", export_machine_table).pack(side="left", padx=3)

machine_path_label = tk.Label(machine_btn_frame, text="No machine file loaded",
                              bg=bg_color, fg="#ff9900", font=("Segoe UI", 9, "italic"))
# (kept for the code that updates it; the status bar shows the state now)

machine_notebook = ttk.Notebook(tab_machine)
machine_notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))

# ---------- Feeders sub-tab ----------
tab_feeders = tk.Frame(machine_notebook, bg=bg_color)
machine_notebook.add(tab_feeders, text="Feeder Setup")

feeder_btn_frame = tk.Frame(tab_feeders, bg=bg_color)
feeder_btn_frame.pack(fill="x", pady=8, padx=8)
create_btn(feeder_btn_frame, "Edit Feeder", edit_selected_feeder).pack(side="left", padx=4)
create_btn(feeder_btn_frame, "Add Feeder", add_feeder).pack(side="left", padx=4)
create_btn(feeder_btn_frame, "Group Edit", open_group_edit).pack(side="left", padx=4)
create_btn(feeder_btn_frame, "Arrange Feeders", arrange_feeders_clicked).pack(side="left", padx=4)
legend = tk.Frame(feeder_btn_frame, bg=bg_color)
legend.pack(side="left", padx=18)
for text, tag in (("fiducial", "fid"), ("8mm bank", "bank"), ("big feeder", "big"),
                  ("later stage", "later"),
                  ("pick point on another feeder - set it up", "warn")):
    tk.Label(legend, text="\u25a0 " + text, bg=bg_color, fg=FEEDER_TAG_COLOURS[tag],
             font=("Segoe UI", 9)).pack(side="left", padx=6)
feeder_count_label = tk.Label(feeder_btn_frame, text="Feeders: 0", bg=bg_color,
                              fg="#5ce65c", font=("Segoe UI", 10, "bold"))
feeder_count_label.pack(side="right", padx=5)

feeder_table_frame = tk.Frame(tab_feeders, bg=bg_color)
feeder_table_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

feeder_scroll_y = ttk.Scrollbar(feeder_table_frame, orient="vertical")
feeder_scroll_y.pack(side="right", fill="y")
feeder_scroll_x = ttk.Scrollbar(feeder_table_frame, orient="horizontal")
feeder_scroll_x.pack(side="bottom", fill="x")

feeder_columns = ["c%d" % i for i in FEEDER_TABLE_COLS]
feeder_tree = ttk.Treeview(feeder_table_frame, columns=feeder_columns, show="headings",
                           selectmode="extended",
                           yscrollcommand=feeder_scroll_y.set,
                           xscrollcommand=feeder_scroll_x.set)
feeder_scroll_y.config(command=feeder_tree.yview)
feeder_scroll_x.config(command=feeder_tree.xview)
for i in FEEDER_TABLE_COLS:
    label, width = FEEDER_TABLE_HEADINGS[i]
    feeder_tree.heading("c%d" % i, text=label)
    feeder_tree.column("c%d" % i, width=width, minwidth=40, anchor="w",
                       stretch=False)
for tag, colour in FEEDER_TAG_COLOURS.items():
    feeder_tree.tag_configure(tag, foreground=colour)
feeder_tree.pack(fill="both", expand=True)
feeder_tree.bind("<Double-1>", edit_selected_feeder)

# ---------- Components sub-tab ----------
tab_comps = tk.Frame(machine_notebook, bg=bg_color)
machine_notebook.add(tab_comps, text="Placement Order")

comp_btn_frame = tk.Frame(tab_comps, bg=bg_color)
comp_btn_frame.pack(fill="x", pady=8, padx=8)
create_btn(comp_btn_frame, "Auto Sort", auto_sort_machine_table).pack(side="left", padx=4)
create_btn(comp_btn_frame, "Top", lambda: move_comps("top"), width=6).pack(side="left", padx=4)
create_btn(comp_btn_frame, "Up", lambda: move_comps(-1), width=6).pack(side="left", padx=2)
create_btn(comp_btn_frame, "Down", lambda: move_comps(1), width=6).pack(side="left", padx=2)
create_btn(comp_btn_frame, "Bottom", lambda: move_comps("bottom"), width=6).pack(side="left", padx=4)
create_btn(comp_btn_frame, "Assign Feeder", assign_feeder_to_selection).pack(side="left", padx=8)
create_btn(comp_btn_frame, "Toggle Skip", toggle_comp_skip).pack(side="left", padx=4)
machine_comp_count_label = tk.Label(comp_btn_frame, text="Components: 0", bg=bg_color,
                                    fg="#5ce65c", font=("Segoe UI", 10, "bold"))
machine_comp_count_label.pack(side="right", padx=5)

comp_table_frame = tk.Frame(tab_comps, bg=bg_color)
comp_table_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

comp_scroll_y = ttk.Scrollbar(comp_table_frame, orient="vertical")
comp_scroll_y.pack(side="right", fill="y")
comp_scroll_x = ttk.Scrollbar(comp_table_frame, orient="horizontal")
comp_scroll_x.pack(side="bottom", fill="x")

comp_columns = [label for index, label, kind in COMP_COLS]
machine_comp_tree = ttk.Treeview(comp_table_frame, columns=comp_columns, show="headings",
                                 selectmode="extended",
                                 yscrollcommand=comp_scroll_y.set,
                                 xscrollcommand=comp_scroll_x.set)
comp_scroll_y.config(command=machine_comp_tree.yview)
comp_scroll_x.config(command=machine_comp_tree.xview)
for label in comp_columns:
    machine_comp_tree.heading(label, text=label)
    machine_comp_tree.column(label, width=150 if label == "Footprint" else 95, anchor="w")
machine_comp_tree.pack(fill="both", expand=True)
machine_comp_tree.bind("<Double-1>", edit_selected_comp)


def _machine_comp_drag_start(event):
    machine_comp_tree._drag_from = machine_comp_tree.identify_row(event.y)


def _machine_comp_drag_drop(event):
    source = getattr(machine_comp_tree, "_drag_from", None)
    target = machine_comp_tree.identify_row(event.y)
    machine_comp_tree._drag_from = None
    if not source or not target or source == target:
        return
    comps = machine_model["comps"]
    src_row = machine_items.get(source)
    dst_row = machine_items.get(target)
    if src_row is None or dst_row is None:
        return
    comps.remove(src_row)
    comps.insert(comps.index(dst_row), src_row)
    refresh_machine_comp_table(select=[machine_item_id(src_row)])


machine_comp_tree.bind("<ButtonPress-1>", _machine_comp_drag_start, add="+")
machine_comp_tree.bind("<ButtonRelease-1>", _machine_comp_drag_drop, add="+")

# ---------- Board & vision sub-tab ----------
tab_board = tk.Frame(machine_notebook, bg=bg_color)
machine_notebook.add(tab_board, text="Board & Vision")

board_btn_frame = tk.Frame(tab_board, bg=bg_color)
board_btn_frame.pack(fill="x", pady=8, padx=8)
create_btn(board_btn_frame, "Edit Record", edit_selected_board_record).pack(side="left", padx=4)
tk.Label(board_btn_frame,
         text="Board, fiducial mark and mirror rows from the file. The file has no header "
              "for these, so fields are shown by column number and exported unchanged "
              "unless you edit them.",
         bg=bg_color, fg=fg_color, font=("Segoe UI", 9, "italic"),
         wraplength=760, justify="left").pack(side="left", padx=15)

board_table_frame = tk.Frame(tab_board, bg=bg_color)
board_table_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

board_scroll_y = ttk.Scrollbar(board_table_frame, orient="vertical")
board_scroll_y.pack(side="right", fill="y")
board_tree = ttk.Treeview(board_table_frame, columns=("Record", "Fields"), show="headings",
                          yscrollcommand=board_scroll_y.set)
board_scroll_y.config(command=board_tree.yview)
board_tree.heading("Record", text="Record")
board_tree.heading("Fields", text="Fields")
board_tree.column("Record", width=140, anchor="w")
board_tree.column("Fields", width=700, anchor="w")
board_tree.pack(fill="both", expand=True)
board_tree.bind("<Double-1>", edit_selected_board_record)


# =====================================
# Edit Row Popup Feature
# =====================================
def on_row_double_click(event):
    """
    Triggered when a user double-clicks a row in the Treeview.
    Opens a nicely formatted popup window to edit Comment, Center-X, Center-Y, Rotation, or Delete the component.
    """
    # Dynamically identify which tree triggered the event (main tree or search tree)
    current_tree = event.widget
    sel = current_tree.selection()

    if not sel:
        return

    target_row = None

    # In the main tree the item id is the row's index in `rows`, which pins the
    # edit to the exact row that was clicked even when designators repeat, and
    # avoids reading the designator back through Tcl's numeric re-typing.
    if current_tree is tree:
        try:
            idx = int(sel[0])
            if 0 <= idx < len(rows):
                target_row = rows[idx]
        except ValueError:
            pass

    if target_row is not None:
        selected_designator = str(target_row[0])
    else:
        selected_designator = tv_text(current_tree.item(sel[0])["values"][0])

    # Count the total number of components sharing this Designator
    same_designator_count = 0
    for r in rows:
        if str(r[0]) == selected_designator:
            same_designator_count += 1
            if target_row is None:
                target_row = r

    if not target_row:
        return

    # Extract all unique comments from the global rows list for the searchable combobox
    unique_comments = sorted(list(set(str(r[10]) for r in rows if r[10])))

    # Create the popup window
    popup = tk.Toplevel(root)
    popup.title(f"Edit Component: {selected_designator}")
    popup.geometry("450x420")
    popup.configure(bg=bg_color)
    popup.resizable(False, False)

    # Variables for current (Absolute) values
    comment_var = tk.StringVar(value=str(target_row[10]))
    cx_var = tk.StringVar(value=str(target_row[2]))
    cy_var = tk.StringVar(value=str(target_row[3]))
    rot_var = tk.StringVar(value=str(target_row[9]))

    # Variables for offset values
    off_cx_var = tk.StringVar(value="0.0")
    off_cy_var = tk.StringVar(value="0.0")
    off_rot_var = tk.StringVar(value="0.0")

    # Checkbox variable for "Apply to all"
    apply_all_var = tk.BooleanVar(value=False)

    # --- UI Layout (Using Frames for better organization) ---
    main_frame = tk.Frame(popup, bg=bg_color, padx=15, pady=15)
    main_frame.pack(fill="both", expand=True)

    # 1. Component Info & Comment section
    info_frame = tk.LabelFrame(main_frame, text=" Component Info ", bg=bg_color, fg=fg_color, font=("Segoe UI", 9, "bold"), padx=10, pady=10)
    info_frame.pack(fill="x", pady=(0, 15))

    tk.Label(info_frame, text="Designator:", bg=bg_color, fg=fg_color).grid(row=0, column=0, sticky="w")
    tk.Label(info_frame, text=selected_designator, bg=bg_color, fg=fg_color, font=("Segoe UI", 9, "bold")).grid(row=0, column=1, sticky="w", padx=10)

    tk.Label(info_frame, text="Comment:", bg=bg_color, fg=fg_color).grid(row=1, column=0, sticky="w", pady=(5,0))

    # --- Searchable Combobox for Comments ---
    from tkinter import ttk
    comment_combo = ttk.Combobox(info_frame, textvariable=comment_var, width=33)
    comment_combo['values'] = unique_comments
    comment_combo.grid(row=1, column=1, sticky="w", padx=10, pady=(5,0))

    def filter_comments(combo_event):
        """Filters the combobox dropdown list based on user input (Search)"""
        typed_text = comment_var.get()
        if typed_text == "":
            comment_combo['values'] = unique_comments
        else:
            # Case-insensitive search
            filtered = [c for c in unique_comments if typed_text.lower() in c.lower()]
            comment_combo['values'] = filtered

    # Bind KeyRelease event to trigger the search/filter
    comment_combo.bind('<KeyRelease>', filter_comments)
    # ----------------------------------------

    # 2. Position & Rotation section
    coord_frame = tk.LabelFrame(main_frame, text=" Position & Rotation ", bg=bg_color, fg=fg_color, font=("Segoe UI", 9, "bold"), padx=10, pady=10)
    coord_frame.pack(fill="x", pady=(0, 15))

    # Column Headers
    tk.Label(coord_frame, text="Parameter", bg=bg_color, fg=fg_color).grid(row=0, column=0, padx=5, pady=(0,5), sticky="w")
    tk.Label(coord_frame, text="Absolute Value", bg=bg_color, fg=fg_color).grid(row=0, column=1, padx=5, pady=(0,5))
    tk.Label(coord_frame, text="Offset (+/-)", bg=bg_color, fg=fg_color).grid(row=0, column=2, padx=5, pady=(0,5))

    # Center-X
    tk.Label(coord_frame, text="Center-X (mm):", bg=bg_color, fg=fg_color).grid(row=1, column=0, sticky="w", pady=5)
    tk.Entry(coord_frame, textvariable=cx_var, bg=entry_bg, fg=entry_fg, width=15).grid(row=1, column=1, padx=5)
    tk.Entry(coord_frame, textvariable=off_cx_var, bg=entry_bg, fg=entry_fg, width=15).grid(row=1, column=2, padx=5)

    # Center-Y
    tk.Label(coord_frame, text="Center-Y (mm):", bg=bg_color, fg=fg_color).grid(row=2, column=0, sticky="w", pady=5)
    tk.Entry(coord_frame, textvariable=cy_var, bg=entry_bg, fg=entry_fg, width=15).grid(row=2, column=1, padx=5)
    tk.Entry(coord_frame, textvariable=off_cy_var, bg=entry_bg, fg=entry_fg, width=15).grid(row=2, column=2, padx=5)

    # Rotation
    tk.Label(coord_frame, text="Rotation (deg):", bg=bg_color, fg=fg_color).grid(row=3, column=0, sticky="w", pady=5)
    tk.Entry(coord_frame, textvariable=rot_var, bg=entry_bg, fg=entry_fg, width=15).grid(row=3, column=1, padx=5)
    tk.Entry(coord_frame, textvariable=off_rot_var, bg=entry_bg, fg=entry_fg, width=15).grid(row=3, column=2, padx=5)

    # 3. Control section (Checkbox and dynamic message)
    ctrl_frame = tk.Frame(main_frame, bg=bg_color)
    ctrl_frame.pack(fill="x")

    # Label to display the component count message
    count_label = tk.Label(ctrl_frame, text="", bg=bg_color, fg="#ff9900", font=("Segoe UI", 8, "italic"))

    def toggle_count_message():
        """Show or hide the component count message based on checkbox state"""
        if apply_all_var.get():
            count_label.config(text=f"This action will affect {same_designator_count} component(s).")
        else:
            count_label.config(text="")

    tk.Checkbutton(
        ctrl_frame,
        text="Apply to all with identical Designator",
        variable=apply_all_var,
        bg=bg_color,
        fg=fg_color,
        selectcolor=tree_bg,
        command=toggle_count_message
    ).pack(anchor="w")

    count_label.pack(anchor="w", padx=25)

    # 4. Action Buttons (Delete & Save)
    btn_frame = tk.Frame(main_frame, bg=bg_color)
    btn_frame.pack(fill="x", pady=(15, 0))

    def delete_component():
        """Deletes the component(s) from the main rows list."""
        msg = "Are you sure you want to delete this component?"
        if apply_all_var.get():
            msg = f"Are you sure you want to delete ALL {same_designator_count} components with Designator '{selected_designator}'?"

        if messagebox.askyesno("Confirm Delete", msg):
            global rows # Ensure we modify the global list
            if apply_all_var.get():
                # Remove all matching components
                rows[:] = [r for r in rows if str(r[0]) != str(selected_designator)]
            else:
                # Remove only the specific target row
                if target_row in rows:
                    rows.remove(target_row)

            refresh_table()
            popup.destroy()

    def apply_changes():
        """Applies coordinate and comment changes."""
        try:
            # Calculate final values
            new_cx = float(cx_var.get()) + float(off_cx_var.get() or 0)
            new_cy = float(cy_var.get()) + float(off_cy_var.get() or 0)
            new_rot = float(rot_var.get()) + float(off_rot_var.get() or 0)
            new_comment = comment_var.get()

            # Calculate the difference (Delta)
            delta_x = new_cx - float(target_row[2])
            delta_y = new_cy - float(target_row[3])
            delta_rot = new_rot - float(target_row[9])

            for r in rows:
                if r is target_row or (apply_all_var.get() and str(r[0]) == str(selected_designator)):

                    # Update Comment (Index 10)
                    r[10] = new_comment

                    # Update X coordinates
                    r[2] = round(float(r[2]) + delta_x, 3)
                    r[4] = round(float(r[4]) + delta_x, 3)
                    r[6] = round(float(r[6]) + delta_x, 3)

                    # Update Y coordinates
                    r[3] = round(float(r[3]) + delta_y, 3)
                    r[5] = round(float(r[5]) + delta_y, 3)
                    r[7] = round(float(r[7]) + delta_y, 3)

                    # Update Rotation; pad 1 and the reference turn with it
                    set_component_rotation(r, float(r[9]) + delta_rot)
                    if delta_rot:
                        angle_flags.pop(id(r), None)

            refresh_table()
            popup.destroy()

        except ValueError:
            messagebox.showerror("Error", "Please enter valid numeric values.")

    # Pack buttons side by side
    tk.Button(btn_frame, text="Delete", command=delete_component, bg="#d9534f", fg="white", width=15, font=("Segoe UI", 9, "bold")).pack(side="left", padx=(10, 0))
    tk.Button(btn_frame, text="Save / Apply", command=apply_changes, bg=btn_bg, fg=btn_fg, width=15, font=("Segoe UI", 9, "bold")).pack(side="right", padx=(0, 10))

def open_search_window(event=None):
    """
    Opens a separate search window.
    Triggered by the Ctrl+F keyboard shortcut.
    """
    search_popup = tk.Toplevel(root)
    search_popup.title("Search Components")
    search_popup.geometry("800x400")
    search_popup.configure(bg=bg_color)

    # Top frame for the search bar
    top_frame = tk.Frame(search_popup, bg=bg_color, pady=10, padx=10)
    top_frame.pack(fill="x")

    tk.Label(top_frame, text="Search:", bg=bg_color, fg=fg_color, font=("Segoe UI", 10, "bold")).pack(side="left")

    search_var = tk.StringVar()
    search_entry = tk.Entry(top_frame, textvariable=search_var, width=40, font=("Segoe UI", 10))
    search_entry.pack(side="left", padx=10)
    search_entry.focus() # Set focus to the search entry immediately

    # Bottom frame for the results table
    bottom_frame = tk.Frame(search_popup, bg=bg_color, padx=10, pady=10)
    bottom_frame.pack(fill="both", expand=True)

    # Create the results treeview similar to the main treeview
    from tkinter import ttk
    columns = tree["columns"] # Copy columns from the main table
    search_tree = ttk.Treeview(bottom_frame, columns=columns, show="headings", selectmode="browse")

    # Copy headings and column configurations from the main tree
    for col in columns:
        search_tree.heading(col, text=tree.heading(col)["text"])
        search_tree.column(col, width=tree.column(col)["width"], anchor=tree.column(col)["anchor"])

    search_tree.pack(side="left", fill="both", expand=True)

    # Add a vertical scrollbar to the search tree
    scrollbar = ttk.Scrollbar(bottom_frame, orient="vertical", command=search_tree.yview)
    search_tree.configure(yscroll=scrollbar.set)
    scrollbar.pack(side="right", fill="y")

    def perform_search(*args):
        """Filters and displays rows based on the search query."""
        # Clear previous search results
        for item in search_tree.get_children():
            search_tree.delete(item)

        query = search_var.get().lower()
        if not query:
            return # Don't show anything if the search field is empty

        # Filter rows and display matching ones in the search tree
        for r in rows:
            # Convert the entire row to a string for easy searching across all columns
            row_str = " ".join(str(item).lower() for item in r)
            if query in row_str:
                search_tree.insert("", "end", values=r)

    # Bind the search variable to the search function (Search as you type)
    search_var.trace_add("write", perform_search)

    # *** Key Point: Bind double-click on the search tree to the existing edit function ***
    search_tree.bind("<Double-1>", on_row_double_click)


# Bind the left mouse double-click event (<Double-1>) on the Treeview to the function
tree.bind("<Double-1>", on_row_double_click)
# Bind the Ctrl+F shortcut to open the search window (both lowercase and uppercase 'F')

root.bind("<Control-f>", open_search_window)
root.bind("<Control-F>", open_search_window)

# =====================================
# Project files
# =====================================
#
# A project holds everything worked on in a session, so it can be closed and
# picked up again: the component list with every edit, the board fields, the
# heights typed in, the heights the Pick & Place file carried, the whole
# machine program with its feeder setup and placement order, and the tape
# widths given to the feeder slots.
#
# Two things are easy to lose and are carried explicitly:
#   - Height(mm) from the original file is not part of the component rows. Left
#     out, an 11mm capacitor would come back as whatever its name suggests.
#   - The board Rotation field is live: changing it turns the whole board. It is
#     restored with that suspended, or a board already turned would turn again.

PROJECT_FORMAT = "kfg-pick-and-place-project"
PROJECT_VERSION = 1
PROJECT_EXT = ".kfg"

project_state = {"path": None, "saved": None}
APP_TITLE = "KFG TECH Pick & Place tool"


def _entry_text(entry):
    return entry.get()


def _set_entry(entry, text):
    entry.delete(0, "end")
    entry.insert(0, "" if text is None else str(text))


def project_snapshot():
    """Everything a project file holds, as plain data."""
    return {
        "format": PROJECT_FORMAT,
        "version": PROJECT_VERSION,
        "rows": [list(r) for r in rows],
        "board": {
            "width": _entry_text(board_width),
            "height": _entry_text(board_height),
            "keepout": _entry_text(board_KeepOutwidth),
            "rotation": board_rotation_var.get(),
            "applied_rotation": last_rotation,
            "offset_x": _entry_text(board_offsetX),
            "offset_y": _entry_text(board_offsety),
            "fiducial_x": _entry_text(fid_x),
            "fiducial_y": _entry_text(fid_y),
            "sort": sort_model.get(),
        },
        "heights": {
            "parts": dict(part_heights),
            "footprints": {k: {"mm": v["mm"], "name": v.get("name", k)}
                           for k, v in footprint_heights.items()
                           if v.get("source") == "manual"},
            "from_file": [[fk, pk, mm] for (fk, pk), mm in file_heights.items()],
            "from_file_counts": [[fk, pk, n, w]
                                 for (fk, pk), (n, w) in file_height_counts.items()],
        },
        "machine": {
            "feeder_header": machine_model["feeder_header"],
            "pre_records": machine_model["pre_records"],
            "smd_header": machine_model["smd_header"],
            "comps": machine_model["comps"],
            "post_records": machine_model["post_records"],
            "path": machine_model["path"],
            "linked": bool(machine_model.get("linked")),
            "linked_to": machine_model.get("linked_to"),
            "stage1": list(machine_model.get("stage1") or []),
        },
        "pnp_source": pnp_source["path"],
        "slot_widths": {k: v for k, v in slot_widths.items() if k not in slot_width_inferred},
        "board_xform": list(board_xform),
        "angle_flags": [[i, angle_flags[id(r)]] for i, r in enumerate(rows)
                        if id(r) in angle_flags],
        "gerber": {
            "files": [[L["name"], L["text"]] for L in gerber_state["layers"]],
            "visible": dict(gerber_state["visible"]),
            "offset": list(gerber_state["offset"]),
            "source": gerber_state["source"],
        },
    }


def _canonical(snapshot):
    """Comparable form of a snapshot, for telling whether anything changed."""
    return json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str)


def project_has_content():
    return bool(rows) or machine_is_loaded()


def project_is_dirty():
    if not project_has_content() and project_state["saved"] is None:
        return False
    return _canonical(project_snapshot()) != project_state["saved"]


def _update_title():
    path = project_state["path"]
    name = os.path.basename(path) if path else "untitled"
    root.title("%s  -  %s" % (APP_TITLE, name) if project_has_content() or path else APP_TITLE)


def status_summary():
    """The facts the status bar shows, as (text, colour) parts."""
    parts = []
    path = project_state["path"]
    name = os.path.basename(path) if path else "untitled"
    dirty = project_has_content() and project_is_dirty()
    parts.append(("Project: %s%s" % (name, "  (unsaved changes)" if dirty else ""),
                  "#ffcc00" if dirty else "#cccccc"))
    if pnp_source["path"]:
        n = len(placeable_rows())
        parts.append(("P&P: %s, %d parts" % (os.path.basename(pnp_source["path"]), n), "#cccccc"))
    if machine_model["comps"]:
        if machine_model.get("stage1"):
            state, colour = "layout taken from the machine", "#5ce65c"
        elif machine_model.get("linked"):
            state, colour = "built here, follows the list", "#5ce65c"
        else:
            state, colour = "imported file", "#7fc8d8"
        parts.append(("Program: %s" % state, colour))
        try:
            stages = len(plan_stages())
        except Exception:
            stages = 1
        parts.append(("%d feeders, %d placements%s" % (
            len(machine_feeders()), len(machine_model["comps"]),
            ", %d stages" % stages if stages > 1 else ""), "#cccccc"))
        bad = overlapping_feeders()
        if bad:
            parts.append(("%d feeder(s) with a pick point on another: %s"
                          % (len(bad), ", ".join(sorted(bad, key=_feeder_sort_key))), "#ff6b5b"))
    else:
        parts.append(("No machine program yet", "#ff9900"))
    return parts


def update_status_bar():
    try:
        parts = status_summary()
    except Exception:
        parts = []
    for w in status_bar.winfo_children():
        w.destroy()
    for i, (text, colour) in enumerate(parts):
        if i:
            tk.Label(status_bar, text="|", bg=status_bg, fg="#555555",
                     font=("Segoe UI", 9)).pack(side="left", padx=4)
        tk.Label(status_bar, text=text, bg=status_bg, fg=colour,
                 font=("Segoe UI", 9)).pack(side="left", padx=2)


def _status_tick():
    update_status_bar()
    root.after(1500, _status_tick)


def save_project(ask=False):
    """Writes the project. Returns True if it was written."""
    if not project_has_content():
        messagebox.showinfo("Save Project", "There is nothing to save yet.")
        return False

    path = project_state["path"]
    if ask or not path:
        initial = os.path.splitext(os.path.basename(path))[0] if path else "project"
        path = filedialog.asksaveasfilename(
            title="Save Project",
            defaultextension=PROJECT_EXT,
            initialfile=initial + PROJECT_EXT,
            filetypes=(("KFG Project", "*" + PROJECT_EXT), ("All Files", "*.*")))
        if not path:
            return False

    snapshot = project_snapshot()
    text = json.dumps(snapshot, indent=1, ensure_ascii=False)

    # Written beside the target and swapped in, so a failure part-way through
    # can never leave a half-written project where a good one was.
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        messagebox.showerror("Save Project", "Could not write the project:\n\n%s" % e)
        return False

    project_state["path"] = path
    project_state["saved"] = _canonical(snapshot)
    _update_title()
    return True


def _read_project(path):
    """Parses and checks a project file. Raises ValueError with a reason."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise ValueError("Could not read the file:\n\n%s" % e)
    except json.JSONDecodeError as e:
        raise ValueError("This is not a project file -- it is not valid JSON "
                         "(line %d)." % e.lineno)

    if not isinstance(data, dict) or data.get("format") != PROJECT_FORMAT:
        raise ValueError("This file is not a KFG Pick & Place project.")
    if not isinstance(data.get("version"), int) or data["version"] > PROJECT_VERSION:
        raise ValueError("This project was saved by a newer version of the tool "
                         "(format %s)." % data.get("version"))

    rows_in = data.get("rows")
    if not isinstance(rows_in, list) or any(
            not isinstance(r, list) or len(r) < 11 for r in rows_in):
        raise ValueError("The project's component list is damaged.")

    machine = data.get("machine") or {}
    for key in ("feeder_header", "pre_records", "smd_header", "comps", "post_records"):
        if not isinstance(machine.get(key, []), list):
            raise ValueError("The project's machine program is damaged (%s)." % key)
    return data


def _confirm_discard(action):
    """Offer to save unsaved work first. False means do not go ahead."""
    if not project_is_dirty():
        return True
    answer = messagebox.askyesnocancel(
        action, "The current work has changes that are not saved.\n\nSave them first?")
    if answer is None:
        return False
    if answer:
        return save_project()
    return True


def open_project(path=None):
    """Reads a project and replaces the current work with it."""
    if not _confirm_discard("Open Project"):
        return False
    if path is None:
        path = filedialog.askopenfilename(
            title="Open Project",
            filetypes=(("KFG Project", "*" + PROJECT_EXT), ("All Files", "*.*")))
        if not path:
            return False

    try:
        data = _read_project(path)
    except ValueError as e:
        # nothing has been touched yet
        messagebox.showerror("Open Project", str(e))
        return False

    global last_rotation, suspend_board_rotation

    rows[:] = [list(r) for r in data["rows"]]

    board = data.get("board") or {}
    _set_entry(board_width, board.get("width", ""))
    _set_entry(board_height, board.get("height", ""))
    _set_entry(board_KeepOutwidth, board.get("keepout", ""))
    _set_entry(board_offsetX, board.get("offset_x", ""))
    _set_entry(board_offsety, board.get("offset_y", ""))
    _set_entry(fid_x, board.get("fiducial_x", ""))
    _set_entry(fid_y, board.get("fiducial_y", ""))
    sort_model.set(board.get("sort", "No Sort"))
    suspend_board_rotation = True
    try:
        board_rotation_var.set(board.get("rotation", "0"))
        last_rotation = float(board.get("applied_rotation", 0.0) or 0.0)
    finally:
        suspend_board_rotation = False

    heights = data.get("heights") or {}
    part_heights.clear()
    part_heights.update({k: float(v) for k, v in (heights.get("parts") or {}).items()})
    footprint_heights.clear()
    for k, v in (heights.get("footprints") or {}).items():
        footprint_heights[k] = {"mm": float(v["mm"]), "source": "manual",
                                "note": "typed in", "name": v.get("name", k)}
    file_heights.clear()
    for fk, pk, mm in heights.get("from_file") or []:
        file_heights[(fk, pk)] = float(mm)
    file_height_counts.clear()
    for fk, pk, n, w in heights.get("from_file_counts") or []:
        file_height_counts[(fk, pk)] = (int(n), int(w))
    learn_footprint_heights()

    machine = data.get("machine") or {}
    machine_model["feeder_header"] = list(machine.get("feeder_header", []))
    machine_model["pre_records"] = [list(r) for r in machine.get("pre_records", [])]
    machine_model["smd_header"] = list(machine.get("smd_header", []))
    machine_model["comps"] = [list(r) for r in machine.get("comps", [])]
    machine_model["post_records"] = [list(r) for r in machine.get("post_records", [])]
    machine_model["path"] = machine.get("path")
    # projects saved before the link existed: a program built here is linked,
    # to whatever list the project carries
    pnp_source["path"] = data.get("pnp_source")
    machine_model["linked"] = bool(machine.get(
        "linked", machine.get("path") is None and machine_model["comps"]))
    machine_model["linked_to"] = machine.get("linked_to", pnp_source["path"])
    machine_model["stage1"] = list(machine.get("stage1") or [])
    for r in rows:                     # names saved before the micro-sign fix
        if "\u039c" in str(r[10]):
            r[10] = upper_part_name(r[10])
    machine_items.clear()

    slot_widths.clear()
    slot_width_inferred.clear()
    slot_widths.update({str(k): v for k, v in (data.get("slot_widths") or {}).items()})

    map_state["selected"] = None
    angle_flags.clear()
    for item in data.get("angle_flags") or []:
        try:
            i, reason = int(item[0]), str(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if 0 <= i < len(rows):
            angle_flags[id(rows[i])] = reason
    xf = data.get("board_xform")
    if isinstance(xf, list) and len(xf) == 6:
        board_xform[:] = [float(v) for v in xf]
    else:
        board_xform[:] = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    map_state["frame"] = None

    gerber = data.get("gerber") or {}
    files = gerber.get("files") or []
    if files:
        try:
            layers = gerber_layers_from_texts([(n, t) for n, t in files])
        except GerberError:
            layers = []
        install_gerber(layers, gerber.get("source"), gerber.get("visible") or None,
                       gerber.get("offset"))
    else:
        board_canvas.delete("gerber")
        gerber_state.update(layers=[], visible={}, offset=(0.0, 0.0), source=None)
        _rebuild_layer_panel()

    refresh_table()
    refresh_feeder_table()
    refresh_machine_comp_table()
    refresh_board_table()
    if machine_is_loaded():
        machine_path_label.config(
            text="from project: %s" % (machine_model["path"] or "built here"),
            fg="#5ce65c")

    project_state["path"] = path
    project_state["saved"] = _canonical(project_snapshot())
    _update_title()
    return True


def on_app_close():
    if _confirm_discard("Exit"):
        root.destroy()


# ---------- File menu ----------
# ==========================================
# Parts Kit tab: what N boards need of every part
# ==========================================
def kit_lines(boards=1, spare_pct=0.0, layers=("top", "bottom")):
    """
    One line per part on the Pick & Place list (fiducials left out): per
    board, for `boards` boards, and with `spare_pct` % extra rounded up, with
    its feeder in the machine program if it has one.
    """
    def side(r):
        t = str(r[8]).strip().lower()
        return "bottom" if t in ("bottomlayer", "bottom", "b") else "top"

    feeder_of = {}
    for c in machine_model["comps"]:
        if not comp_is_fiducial(c):
            feeder_of.setdefault(comp_part_key(c), str(field_at(c, 1)))
    lines = {}
    for r in placeable_rows():
        if side(r) not in layers:
            continue
        key = (part_name_key(r[10]), str(r[1]).strip())
        e = lines.setdefault(key, {"part": str(r[10]), "footprint": str(r[1]),
                                   "value": decode_part_value(r[10]) or "",
                                   "sides": set(), "des": []})
        e["sides"].add(side(r))
        e["des"].append(str(r[0]))
    out = []
    for e in lines.values():
        per = len(e["des"])
        total = per * boards
        e.update(per=per, total=total,
                 spare=int(math.ceil(total * (1 + spare_pct / 100.0) - 1e-9)),
                 feeder=feeder_of.get(part_match_key("%s/%s" % (e["footprint"], e["part"])), ""),
                 side="/".join(sorted(e["sides"])))
        out.append(e)
    out.sort(key=lambda e: (-e["per"], e["footprint"], e["part"]))
    return out


def kit_same_value_groups(lines):
    """Lines with different names but the same value on the same footprint size."""
    groups = {}
    for e in lines:
        if e["value"]:
            size = clean_footprint(e["footprint"])
            groups.setdefault((e["value"], size), []).append(e)
    return [g for g in groups.values() if len(g) > 1]


def boards_per_panel():
    n = sum(1 for r in machine_board_records() if r and r[0] == "mirror")
    return max(1, n)


tab_kit = tk.Frame(notebook, bg=bg_color)
notebook.add(tab_kit, text="Parts Kit")

kit_top = tk.Frame(tab_kit, bg=bg_color)
kit_top.pack(fill="x", padx=14, pady=(12, 6))
create_label(kit_top, "Boards to build:").pack(side="left")
kit_boards_var = tk.StringVar(value="1")
create_entry(kit_top, width=8, var=kit_boards_var).pack(side="left", padx=(6, 18))
create_label(kit_top, "Spare (%):").pack(side="left")
kit_spare_var = tk.StringVar(value="0")
create_entry(kit_top, width=6, var=kit_spare_var).pack(side="left", padx=(6, 18))
kit_top_var = tk.BooleanVar(value=True)
kit_bottom_var = tk.BooleanVar(value=True)
for _t, _v in (("Top side", kit_top_var), ("Bottom side", kit_bottom_var)):
    tk.Checkbutton(kit_top, text=_t, variable=_v, bg=bg_color, fg=fg_color,
                   selectcolor=tree_bg, activebackground=bg_color, activeforeground=fg_color,
                   command=lambda: refresh_kit()).pack(side="left", padx=4)
kit_summary = tk.Label(kit_top, text="", bg=bg_color, fg="#5ce65c",
                       font=("Segoe UI", 10, "bold"))
kit_summary.pack(side="left", padx=20)

kit_table = tk.Frame(tab_kit, bg=bg_color)
kit_table.pack(fill="both", expand=True, padx=14)
_kit_scroll = ttk.Scrollbar(kit_table, orient="vertical")
_kit_scroll.pack(side="right", fill="y")
KIT_COLS = (("part", "Part", 230), ("value", "Value", 80), ("footprint", "Footprint", 210),
            ("side", "Side", 80), ("per", "Per board", 75), ("total", "Total", 75),
            ("spare", "With spare", 85), ("feeder", "Feeder", 60),
            ("des", "Designators", 420))
kit_tree = ttk.Treeview(kit_table, columns=[c for c, _, _ in KIT_COLS], show="headings",
                        yscrollcommand=_kit_scroll.set)
_kit_scroll.config(command=kit_tree.yview)
for _c, _label, _w in KIT_COLS:
    kit_tree.heading(_c, text=_label)
    kit_tree.column(_c, width=_w, anchor="w", stretch=(_c == "des"))
kit_tree.tag_configure("same", foreground="#e0a040")
kit_tree.pack(fill="both", expand=True)

kit_note = tk.Label(tab_kit, text="", bg=bg_color, fg="#e0a040", font=("Segoe UI", 9),
                    justify="left", anchor="w", wraplength=1400)
kit_note.pack(fill="x", padx=14, pady=(6, 0))
kit_bottom = tk.Frame(tab_kit, bg=bg_color)
kit_bottom.pack(fill="x", padx=14, pady=10)
kit_state = {"lines": []}


def _kit_inputs():
    try:
        boards = max(0, int(float(kit_boards_var.get() or 0)))
    except ValueError:
        boards = 0
    try:
        spare = max(0.0, float(kit_spare_var.get() or 0))
    except ValueError:
        spare = 0.0
    layers = tuple(n for n, v in (("top", kit_top_var), ("bottom", kit_bottom_var)) if v.get())
    return boards, spare, layers


def refresh_kit(*_):
    boards, spare, layers = _kit_inputs()
    lines = kit_lines(boards, spare, layers)
    kit_state["lines"] = lines
    same = kit_same_value_groups(lines)
    flagged = {id(e) for grp in same for e in grp}
    kit_tree.delete(*kit_tree.get_children())
    for i, e in enumerate(lines):
        kit_tree.insert("", "end", iid="k%d" % i, values=(
            e["part"], e["value"], e["footprint"], e["side"], e["per"], e["total"],
            e["spare"], e["feeder"], ", ".join(e["des"])),
            tags=("same",) if id(e) in flagged else ())
    per_board = sum(e["per"] for e in lines)
    text = "%d part lines  |  %d parts per board  |  %d boards: %d parts" % (
        len(lines), per_board, boards, sum(e["total"] for e in lines))
    if spare:
        text += ", %d with %g%% spare" % (sum(e["spare"] for e in lines), spare)
    n = boards_per_panel()
    if n > 1 and boards:
        text += "  |  %d panels of %d" % (int(math.ceil(boards / float(n))), n)
    kit_summary.config(text=text if lines else "Load a Pick & Place file first.")
    kit_note.config(text=("Same value, different names -- maybe one reel: " + ";   ".join(
        "%s on %s: %s" % (grp[0]["value"], clean_footprint(grp[0]["footprint"]),
                          " + ".join("%s (%d)" % (e["part"], e["per"]) for e in grp))
        for grp in same)) if same else "")


def export_kit():
    if not kit_state["lines"]:
        messagebox.showinfo("Parts Kit", "There is nothing to export.")
        return
    boards, spare, _ = _kit_inputs()
    path = filedialog.asksaveasfilename(
        title="Save the parts list", defaultextension=".csv",
        initialfile="parts kit - %d boards.csv" % boards,
        filetypes=(("CSV Files", "*.csv"), ("All Files", "*.*")))
    if not path:
        return
    try:
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["Part", "Value", "Footprint", "Side", "Per board",
                        "Total (%d boards)" % boards, "With %g%% spare" % spare,
                        "Feeder", "Designators"])
            for e in kit_state["lines"]:
                w.writerow([e["part"], e["value"], e["footprint"], e["side"], e["per"],
                            e["total"], e["spare"], e["feeder"], " ".join(e["des"])])
    except OSError as err:
        messagebox.showerror("Parts Kit", "Could not save:\n%s" % err)
        return
    messagebox.showinfo("Parts Kit", "Saved:\n%s" % path)


create_btn(kit_bottom, "Export Parts List", export_kit).pack(side="right")
tk.Label(kit_bottom, text="Amber lines: the same value under different names on the same "
                          "size -- check whether one reel covers them.",
         bg=bg_color, fg=fg_color, font=("Segoe UI", 9, "italic")).pack(side="left")
kit_boards_var.trace_add("write", refresh_kit)
kit_spare_var.trace_add("write", refresh_kit)
notebook.bind("<<NotebookTabChanged>>",
              lambda e: refresh_kit() if notebook.select() == str(tab_kit) else None, add="+")

status_bg = "#181818"
status_bar = tk.Frame(root, bg=status_bg, height=24)
status_bar.pack(side="bottom", fill="x", before=notebook)
_status_tick()

load_settings()
menubar = tk.Menu(root)
file_menu = tk.Menu(menubar, tearoff=0)
file_menu.add_command(label="Open Project...", accelerator="Ctrl+O",
                      command=lambda: open_project())
file_menu.add_command(label="Save Project", accelerator="Ctrl+S",
                      command=lambda: save_project())
file_menu.add_command(label="Save Project As...", accelerator="Ctrl+Shift+S",
                      command=lambda: save_project(ask=True))
file_menu.add_separator()
file_menu.add_command(label="Options...", command=lambda: open_options())
file_menu.add_separator()
file_menu.add_command(label="Exit", command=on_app_close)
menubar.add_cascade(label="File", menu=file_menu)
root.config(menu=menubar)

root.bind("<Control-o>", lambda e: open_project())
root.bind("<Control-s>", lambda e: save_project())
root.bind("<Control-S>", lambda e: save_project(ask=True))
root.protocol("WM_DELETE_WINDOW", on_app_close)

root.state("zoomed")      # open full screen
root.mainloop()
