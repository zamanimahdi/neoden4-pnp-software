"""neosetup.Project -- the KFG Pick & Place Tool as API calls, for the setup wizard.

One Project = one headless instance of the tool (engine/csv_flip_tool.py, unchanged). Every
call takes explicit arguments (no selections, no dialogs), returns plain Python data, and
the messages the tool would have shown (summaries, warnings, errors) come back as data:
in the result's "messages" where a call returns a dict, and always in `last_messages`.

How it stays faithful:
  - the tool's own functions do the work, on the tool's own state;
  - where the tool takes its input from the screen (selected table row, typed field), the
    headless stub widgets hold that input and the tool's own handler is called
    (rename_part, remove_part, move_comps, toggle_comp_skip, export_csv, the table sort);
  - where the logic sits inside a dialog (Build machine, Speed optimiser, Options, 2-point
    alignment, group edit, field editors, Component Heights, Similar Names, Package
    Heights, Parts Kit) the dialog's steps are repeated here line for line, calling the
    same tool functions; tests/test_golden.py checks them byte for byte against the real
    Tk tool driven through its real dialogs.

Not thread safe across calls on one Project from several threads at once: each public call
holds the Project's lock. Different Projects are independent.
"""
import contextlib
import copy
import json
import math
import os
import tempfile
import threading

from . import _engine, _tkstub

__all__ = ["Project", "ToolError"]


def _unquote_file(path):
    """the machine program as the machine reads it: every comma separates a field, so a value with a comma
    in it (written quoted, "TLP293(GB-TPL,E") gets a ";" instead; the line ends and encoding are kept"""
    from neojob.jobfile import unquote_line
    raw = open(path, "rb").read()
    if b'"' not in raw:
        return
    nl = b"\r\n" if b"\r\n" in raw else b"\n"
    enc = "utf-8"
    try:
        text = raw.decode(enc)
    except UnicodeDecodeError:
        enc, text = "latin-1", raw.decode("latin-1")
    lines = text.split(nl.decode())
    with open(path, "wb") as f:
        f.write(nl.join((unquote_line(l) if '"' in l else l).encode(enc) for l in lines))


class ToolError(Exception):
    """The tool refused the action: it showed an error and (normally) changed nothing.
    `.title`, `.text` are the error; `.messages` everything shown during the call."""

    def __init__(self, title, text, messages):
        Exception.__init__(self, "%s: %s" % (title, text))
        self.title, self.text, self.messages = title, text, messages


# The table sort models the tool's "Sort By" box offers (refresh_table reads them).
SORT_MODELS = [
    "No Sort", "Auto (shortest parts first)", "Pythagoras (X,Y) incremental",
    "Center-X incremental", "Center-Y incremental", "Designator incremental",
    "Comment incremental", "Pythagoras (X,Y) decrease", "Center-X decrease",
    "Center-Y decrease", "Designator decrease", "Comment decrease",
]

# Short names for feeder (stack row) fields; any field may also be given by its index or
# by the tool's own label ("Place Height (mm)").
FEEDER_FIELDS = {
    "id": 1, "type": 2, "nozzles": 3, "x": 4, "y": 5, "angle": 6, "footprint_field": 7,
    "part": 8, "pick_height": 9, "pick_delay": 10, "place_height": 11, "place_delay": 12,
    "vacuum": 13, "threshold": 14, "vision": 15, "speed": 16, "feed_rhythm": 17,
    "feeder_power": 18, "peeler_power": 19, "col21": 20, "col22": 21, "col23": 22,
    "col24": 23, "col25": 24, "col26": 25,
}
COMP_FIELDS = {"feeder": 1, "nozzle": 2, "designator": 3, "name": 3, "value": 4,
               "footprint": 5, "x": 6, "y": 7, "rotation": 8, "skip": 9}

# Messages the tool raises after almost any change, as a board check -- not a refusal.
_CHECK_TITLES = ("Board Coordinate Error",)


def _num(v):
    return None if v == float("inf") else v


class Project:
    """One wizard session on one board.

    settings       -- a dict as the tool's Options file holds it (current_settings()
                      shape), applied on top of the tool's built-in defaults.
    settings_path  -- where the tool may save Options (Options save, Component Heights).
                      None (default): nowhere -- the user's own settings file is never read
                      or written unless named here or loaded with load_settings_file().
    machine_fixes  -- what the machine (neoserver) needs beyond the tool's own output: a
                      value with a comma written with ';' (the job format splits on every
                      comma), the board rows made to fit an aligned board (one panel at the
                      first part, the marks taken here), the operator's new-feeder defaults.
                      False (default): byte for byte as the tool writes (the golden tests).
    """

    # the operator's defaults for a new feeder (machine_fixes): pick / place delay, place height
    MACHINE_FEEDER_DEFAULTS = {10: "200", 11: "2.38", 12: "200"}

    def __init__(self, settings=None, settings_path=None, machine_fixes=False):
        self._lock = threading.RLock()
        self._d = _tkstub.Dialogs()
        self._g = _engine.load(self._d)
        self.settings_path = settings_path
        self._g["settings_path"] = lambda: self.settings_path
        self.last_messages = []
        self.log = []
        self._build = None
        self._speed = None
        self.machine_fixes = bool(machine_fixes)
        if self.machine_fixes:
            self._g["FALLBACK_FEEDER"].update(self.MACHINE_FEEDER_DEFAULTS)
        self._marks = []            # the board's marks (fiducials) for the program's mark line, board frame
        self._aligned = False       # an alignment was applied: the parts are in the machine's frame now
        if settings:
            self._g["apply_settings"](settings)

    # ------------------------------------------------------------------ plumbing
    @property
    def engine(self):
        """The tool's module namespace (rows, machine_model, every function). For the
        rare thing this API does not cover; changes made through it are your own."""
        return self._g

    @contextlib.contextmanager
    def _op(self, raise_on_error=True, answer=True, keep_build=False):
        with self._lock:
            self._d.messages = []
            self._d.answer = answer
            if not keep_build:
                self._build = None
            rec = []
            try:
                yield rec
            finally:
                msgs = []
                for kind, title, text in self._d.messages:
                    if kind == "error" and title in _CHECK_TITLES:
                        kind = "check"
                    msgs.append({"kind": kind, "title": title, "text": text})
                self._d.messages = []
                self._d.answer = True
                rec[:] = msgs
                self.last_messages = msgs
                self.log.extend(msgs)
                del self.log[:-500]
            errors = [m for m in msgs if m["kind"] == "error"]
            if raise_on_error and errors:
                raise ToolError(errors[0]["title"], errors[0]["text"], msgs)

    def _show(self, kind, title, text):
        mb = self._g["messagebox"]
        return {"info": mb.showinfo, "warning": mb.showwarning, "error": mb.showerror,
                "askyesno": mb.askyesno}[kind](title, text)

    def take_log(self):
        """Every message since the last take_log(), oldest first."""
        out, self.log = self.log, []
        return out

    # ---- references -------------------------------------------------------
    def _row(self, ref):
        rows = self._g["rows"]
        if isinstance(ref, int):
            if 0 <= ref < len(rows):
                return rows[ref]
            raise KeyError("no component at index %d" % ref)
        for r in rows:
            if str(r[0]).strip() == str(ref).strip():
                return r
        raise KeyError("no component %r" % ref)

    def _ref_row(self, ref):
        """an alignment point: a component (index or designator), or a board point [x, y] in the
        design's coordinates (a row of its own, not in the list: the camera was put on that point)"""
        if isinstance(ref, dict):
            ref = (ref.get("x"), ref.get("y"))
        if isinstance(ref, (list, tuple)):
            x, y = float(ref[0]), float(ref[1])
            return ["point %.3f, %.3f" % (x, y), "", x, y, x, y, x, y, "", 0.0, "", "_point"]
        return self._row(ref)

    @staticmethod
    def _is_point(r):
        return len(r) > 11 and r[11] == "_point"

    def _index_of(self, r):
        for i, x in enumerate(self._g["rows"]):
            if x is r:
                return i
        return None

    def _feeder(self, fid):
        for f in self._g["machine_feeders"]():
            if str(self._g["field_at"](f, 1)) == str(fid).strip():
                return f
        raise KeyError("no feeder %r" % fid)

    def _comp(self, ref):
        comps = self._g["machine_model"]["comps"]
        if isinstance(ref, int):
            if 0 <= ref < len(comps):
                return comps[ref]
            raise KeyError("no placement at position %d" % ref)
        for c in comps:
            if str(self._g["field_at"](c, 3)).strip() == str(ref).strip():
                return c
        raise KeyError("no placement %r" % ref)

    def _select_comps(self, refs):
        g = self._g
        if isinstance(refs, (str, int)):
            refs = [refs]
        comps = [self._comp(r) for r in refs]
        ids = [g["machine_item_id"](c) for c in comps]
        tree = g["machine_comp_tree"]
        if not all(tree.exists(i) for i in ids):
            g["refresh_machine_comp_table"]()
        tree.selection_set(ids)
        return comps

    def _select_feeders(self, fids):
        g = self._g
        feeders = [self._feeder(f) for f in fids]
        ids = [g["machine_item_id"](f) for f in feeders]
        tree = g["feeder_tree"]
        if not all(tree.exists(i) for i in ids):
            g["refresh_feeder_table"]()
        tree.selection_set(ids)
        return feeders

    # ---- plain data -------------------------------------------------------
    def _row_dict(self, i, r):
        g = self._g
        return {"index": i, "designator": str(r[0]), "footprint": str(r[1]),
                "x": r[2], "y": r[3], "ref_x": r[4], "ref_y": r[5], "pad_x": r[6],
                "pad_y": r[7], "layer": r[8], "rotation": r[9], "part": str(r[10]),
                "fiducial": g["is_fiducial"](r), "angle_note": g["angle_flags"].get(id(r))}

    def _feeder_dict(self, f, overlap=None):
        g = self._g
        fa = g["field_at"]
        if overlap is None:
            overlap = g["overlapping_feeders"]()
        part = str(fa(f, 8)).strip()
        d = {k: str(fa(f, i)) for k, i in FEEDER_FIELDS.items()}
        d.update(footprint=g["feeder_footprint"](f),
                 value=part.split("/", 1)[1] if "/" in part else "",
                 nozzle_set=g["parse_nozzles"](fa(f, 3)),
                 fiducial=g["feeder_is_fiducial"](f), tag=g["feeder_row_tag"](f, overlap),
                 overlapping=str(fa(f, 1)) in overlap, fields=list(f))
        return d

    def _comp_dict(self, pos, c):
        g = self._g
        d = {k: str(g["field_at"](c, i)) for k, i in COMP_FIELDS.items() if k != "name"}
        d.update(position=pos, skip=str(g["field_at"](c, 9)).strip().lower() == "yes",
                 fiducial=g["comp_is_fiducial"](c), fields=list(c))
        return d

    # ================================================================== import
    def import_pnp(self, path):
        """Load an Altium Pick & Place CSV (load_csv): footprints cleaned, part names
        upper-cased, rotations set to the machine pattern, heights learned. Replaces the
        component list. Raises ToolError if the file is refused (nothing changed then)."""
        g = self._g
        self._d.open_path = os.fspath(path)
        with self._op() as msgs:
            g["load_csv"]()
            self._d.open_path = ""
            self._marks, self._aligned = [], False           # a new board
            return {"components": len(g["rows"]), "placeable": len(g["placeable_rows"]()),
                    "similar_names": self.similar_name_groups(_inner=True),
                    "messages": msgs}

    def import_machine_file(self, path):
        """Open an existing NeoDen machine file (import_machine_file) as the program."""
        g = self._g
        self._d.open_path = os.fspath(path)
        with self._op() as msgs:
            g["import_machine_file"]()
            self._d.open_path = ""
            if g["machine_model"]["path"] != os.fspath(path):
                errs = [m for m in self._d.messages if m[0] == "warning"]
                if errs:
                    g["messagebox"].showerror(errs[0][1], errs[0][2])
            return {"feeders": len(g["machine_feeders"]()),
                    "components": len(g["machine_model"]["comps"]), "messages": msgs}

    def load_gerber(self, path):
        """Gerber underlay: a folder, a .zip, a .rar (Windows only -- the tool opens .rar
        with Windows' tar.exe) or any one file of the set (its folder is read). Lines the
        Gerber up with the parts and reports pads on the wrong side. As load_gerber_dialog,
        without its Pillow check (Pillow only draws the map)."""
        g = self._g
        path = os.fspath(path)
        with self._op() as msgs:
            lower = path.lower()
            source = path if (os.path.isdir(path) or lower.endswith(".zip")
                              or lower.endswith(".rar")) else os.path.dirname(path)
            try:
                pairs = g["gerber_files_in"](source)
                layers = g["gerber_layers_from_texts"](pairs)
            except (g["GerberError"], OSError) as e:
                g["messagebox"].showerror("Load Gerber", str(e))
                return None
            if not layers:
                g["messagebox"].showerror("Load Gerber", "No Gerber layers were found there.")
                return None
            matched, total = g["install_gerber"](layers, source)
            gs = g["gerber_state"]
            notes = ["%d layers loaded from %s." % (len(layers), os.path.basename(source))]
            if total:
                ox, oy = gs["offset"]
                notes.append("Lined up with the parts: pad 1 of %d of %d components sits on a "
                             "copper pad%s." % (matched, total,
                                                "" if abs(ox) < 1e-9 and abs(oy) < 1e-9
                                                else " (Gerber shifted %.2f, %.2f mm)" % (-ox, -oy)))
                if matched < 0.6 * total:
                    notes.append("That is too few to trust -- the Gerber may be from another "
                                 "revision of the board.")
            wrong = g["pad_side_mismatches"]()
            for listed, des in sorted(wrong.items()):
                other = "Bottom" if listed == "top" else "Top"
                notes.append("%d component(s) listed as %s layer have their pads on %s copper -- "
                             "the Pick & Place layer column is wrong for them, and they are "
                             "really placed on the %s." % (len(des), listed.title(), other,
                                                           other.lower()))
            gs["report"] = "\n\n".join(notes)
            g["messagebox"].showinfo("Load Gerber", gs["report"])
            out = self.gerber()
            out["messages"] = msgs
            return out

    def clear_gerber(self):
        with self._op():
            self._g["clear_gerber"]()

    def gerber(self):
        """What is loaded: layers, offset (Gerber minus Pick & Place, mm), pad match,
        the load report and pads found on the other side's copper."""
        g = self._g
        gs = g["gerber_state"]
        return {"source": gs["source"], "offset": tuple(gs["offset"]),
                "match": tuple(gs.get("match") or (None, None)), "report": gs["report"],
                "layers": [{"key": L["key"], "label": L["label"], "side": L["side"],
                            "kind": L["kind"], "name": L.get("name"),
                            "flashes": len(L["flashes"]),
                            "visible": bool(gs["visible"].get(L["key"]))}
                           for L in gs["layers"]],
                "side_mismatches": g["pad_side_mismatches"]() if gs["layers"] else {}}

    # ---- project file -----------------------------------------------------
    def save_project(self, path):
        """Write the tool's project file (.kfg, JSON) -- everything of the session."""
        self._d.save_path = os.fspath(path)
        with self._op(keep_build=True):
            ok = self._g["save_project"](ask=True)
            self._d.save_path = ""
            return bool(ok)

    def load_project(self, path):
        """Replace the current work with a project file (open_project). Unsaved work in
        this Project is discarded without asking."""
        with self._op(answer=False):
            return bool(self._g["open_project"](os.fspath(path)))

    def project_snapshot(self):
        """What a project file would hold, as plain data."""
        with self._lock:
            return json.loads(json.dumps(self._g["project_snapshot"](), default=str))

    def is_dirty(self):
        with self._lock:
            return bool(self._g["project_is_dirty"]())

    def status(self):
        """The tool's status bar facts: [(text, colour), ...]."""
        with self._lock:
            return list(self._g["status_summary"]())

    # ---- settings / options ----------------------------------------------
    def settings(self):
        """Every machine constant (the Options file's content), plain data."""
        with self._lock:
            return copy.deepcopy(self._g["current_settings"]())

    def apply_settings(self, d, save=False):
        """apply_settings(d); also write them to settings_path if save."""
        with self._op():
            self._g["apply_settings"](d)
            if save:
                self._g["save_settings"]()

    def load_settings_file(self, path):
        """Apply an Options file (e.g. the user's own settings.json) -- only when asked."""
        with open(os.fspath(path), encoding="utf-8") as fh:
            d = json.load(fh)
        self.apply_settings(d)

    def options(self):
        """The Options dialog: [{label, key, value}] in its order, and the learned slots."""
        g = self._g
        with self._lock:
            cur = g["current_settings"]()
            out = []
            for label, k in g["OPTION_FIELDS"]:
                v = cur["fallback_feeder"].get(k[2:], "") if k.startswith("fb") else cur.get(k)
                out.append({"label": label, "key": k, "value": str(v)})
            return {"fields": out,
                    "slots": {fid: {str(i): v for i, v in p.items()}
                              for fid, p in g["LEARNED_SLOTS"].items()}}

    def set_options(self, values=None, slots=None, apply_to_program=True):
        """Options -> Save. `values` {key: text} for any OPTION_FIELDS key (others keep their
        current value); `slots` {feeder id: {field index: value}} replaces the slot table
        (None keeps it). Then saved to settings_path (if any) and, with
        apply_to_program, put into every feeder of the open program."""
        g = self._g
        values = dict(values or {})
        with self._op() as msgs:
            cur = self.options()
            given = {f["key"]: f["value"] for f in cur["fields"]}
            unknown = set(values) - set(given)
            if unknown:
                raise ValueError("unknown option(s): %s" % ", ".join(sorted(unknown)))
            given.update({k: str(v) for k, v in values.items()})
            if slots is None:
                slots = {fid: dict(p) for fid, p in g["LEARNED_SLOTS"].items()}
            self._options_save(given, slots, apply_to_program)
            return {"settings": self.settings(), "messages": msgs}

    def _options_save(self, given, slots, apply_now):
        """The Options dialog's save(), with its entries' text in `given`."""
        g = self._g
        d = {"fallback_feeder": {}, "slots": {str(f): {str(i): v for i, v in p.items()}
                                              for f, p in slots.items()}}
        try:
            for _, k in g["OPTION_FIELDS"]:
                v = given[k].strip()
                if k.startswith("fb"):
                    d["fallback_feeder"][k[2:]] = v
                elif k in ("eight_mm_count", "big_feeders_from"):
                    d[k] = int(v)
                elif k in ("bank_centre", "board_surface_mm", "press_percent"):
                    d[k] = float(v)
                else:
                    d[k] = v
        except ValueError:
            g["messagebox"].showerror("Options", "8mm feeders, centre and big-feeder start "
                                                 "must be numbers.")
            return
        g["apply_settings"](d)
        try:
            g["save_settings"]()
        except OSError as e:
            g["messagebox"].showerror("Options", "Could not save the options:\n%s" % e)
        if apply_now and g["machine_model"]["comps"]:
            g["apply_slots_to_program"]()

    def learn_slots(self, path, apply_to_program=True):
        """Options -> Learn from machine file..., then Save: each slot's pick X / Y,
        angle, feeder and peeler power read from a file the machine wrote (added to /
        replacing the slots already known). Returns the slot ids read."""
        g = self._g
        path = os.fspath(path)
        with self._op() as msgs:
            cur = self.options()
            slots = {fid: dict(p) for fid, p in g["LEARNED_SLOTS"].items()}
            saved = dict(g["LEARNED_SLOTS"])
            g["LEARNED_SLOTS"].clear()
            g["LEARNED_SLOTS"].update(slots)
            try:
                got = g["learn_slots_from_machine_file"](path)
            except OSError as e:
                g["messagebox"].showerror("Options", str(e))
                got = []
            slots = dict(g["LEARNED_SLOTS"])
            g["LEARNED_SLOTS"].clear()
            g["LEARNED_SLOTS"].update(saved)
            g["messagebox"].showinfo("Options", "%d slot(s) read from\n%s" % (len(got), path))
            self._options_save({f["key"]: f["value"] for f in cur["fields"]}, slots,
                               apply_to_program)
            return {"slots": got, "messages": msgs}

    # ================================================================== parts
    def components(self):
        """The Pick & Place list in stored order: one dict per row."""
        with self._lock:
            return [self._row_dict(i, r) for i, r in enumerate(self._g["rows"])]

    def table_order(self):
        """The list in the order the table shows it (sort model, fiducials first) -- the
        order Build uses."""
        with self._lock:
            return [self._row_dict(self._index_of(r), r)
                    for r in self._g["pnp_rows_in_table_order"]()]

    def set_table_sort(self, model):
        """The table's Sort By box (one of SORT_MODELS). "Auto (shortest parts first)" is
        the height order with the nearest-neighbour route inside each height."""
        if model not in SORT_MODELS:
            raise ValueError("sort model must be one of %s" % SORT_MODELS)
        with self._op():
            self._g["sort_model"].set(model)
            self._g["refresh_table"]()

    def bom(self):
        """Part Name table: [{part, key, qty, height_label, flag, footprints}], busiest
        first. flag: "dupe" (spelled more than one way), "noheight" or None."""
        g = self._g
        with self._lock:
            g["refresh_variants"]()
            tv = g["variant_tree"]
            fps = {}
            for r in g["placeable_rows"]():
                fps.setdefault(str(r[10]), set()).add(str(r[1]))
            out = []
            for iid in tv.get_children():
                part, qty, label = tv.raw_values(iid)
                tags = tv.tags_of(iid)
                out.append({"part": part, "key": g["part_name_key"](part), "qty": qty,
                            "height_label": label, "flag": tags[0] if tags else None,
                            "footprints": sorted(fps.get(part, ()))})
            return out

    def footprints(self):
        """Footprint table: [{footprint, qty, height_label, unknown_height, mm, source,
        note}], shortest first (the order the board is assembled)."""
        g = self._g
        with self._lock:
            g["refresh_footprint"]()
            tv = g["footprint_tree"]
            out = []
            for iid in tv.get_children():
                fp, qty, label = tv.raw_values(iid)
                e = g["footprint_heights"].get(g["footprint_key"](fp), {})
                out.append({"footprint": fp, "qty": qty, "height_label": label,
                            "unknown_height": bool(tv.tags_of(iid)), "mm": e.get("mm"),
                            "source": e.get("source"), "note": e.get("note")})
            return out

    def stats(self):
        """Statistics box: components, different footprints, different BOMs."""
        g = self._g
        with self._lock:
            pr = g["placeable_rows"]()
            groups = g["similar_part_groups"]()
            return {"components": len(pr),
                    "footprints": len({str(r[1]).strip() for r in pr}),
                    "boms": len({str(r[10]).strip() for r in pr}),
                    "spelled_more_than_one_way":
                        sum(len(c) for c in groups.values()) - len(groups),
                    "fiducials": sum(1 for r in g["rows"] if g["is_fiducial"](r))}

    def similar_name_groups(self, _inner=False):
        """Same part spelled more than one way: [{key, spellings: {name: n}, components,
        target}] -- target is the default the Similar Names window merges into."""
        g = self._g
        ctx = contextlib.nullcontext() if _inner else self._lock
        with ctx:
            groups = g["similar_part_groups"]()
            return [{"key": k, "spellings": dict(c.most_common()),
                     "components": sum(c.values()), "target": c.most_common(1)[0][0]}
                    for k, c in sorted(groups.items(), key=lambda kv: -sum(kv[1].values()))]

    def merge_similar_names(self, targets=None, keys=None):
        """Similar Names -> Merge (all groups, or the `keys` given). targets {group key:
        spelling} overrides the default target; it must be one of the group's spellings.
        Returns the number of components renamed."""
        g = self._g
        with self._op() as msgs:
            groups = g["similar_part_groups"]()
            if not groups:
                g["messagebox"].showinfo(
                    "Similar Part Names",
                    "Every part name is spelled consistently.\n\nNothing to merge.")
                return {"renamed": 0, "messages": msgs}
            chosen = {k: c.most_common(1)[0][0] for k, c in groups.items()}
            for k, t in (targets or {}).items():
                if k not in groups or t not in groups[k]:
                    raise ValueError("%r is not a spelling of group %r" % (t, k))
                chosen[k] = t
            keys = list(groups) if keys is None else [k for k in keys if k]
            for k in keys:
                if k not in groups:
                    raise KeyError("no similar-name group %r" % k)
            if not keys:
                g["messagebox"].showinfo("Similar Part Names", "Select a group first.")
                return {"renamed": 0, "messages": msgs}
            changed = 0
            for key in keys:
                target = chosen[key]
                spellings = set(groups[key])
                for r in g["rows"]:
                    if str(r[10]) in spellings and str(r[10]) != target:
                        r[10] = target
                        changed += 1
            g["refresh_table"]()
            g["messagebox"].showinfo(
                "Similar Part Names",
                "Renamed %d component(s) across %d group(s)." % (changed, len(keys)))
            return {"renamed": changed, "messages": msgs}

    def rename_part(self, old, new):
        """Rename Part: every component with Part Name `old` becomes `new`."""
        g = self._g
        with self._op() as msgs:
            g["refresh_variants"]()
            iid = g["tv_key"](old)
            if not g["variant_tree"].exists(iid):
                raise KeyError("no part %r" % old)
            n = sum(1 for r in g["rows"] if str(r[10]) == old)
            g["variant_tree"].selection_set(iid)
            g["_set_entry"](g["rename_entry"], new)
            g["rename_part"]()
            renamed = n if str(new).strip() else 0
            return {"renamed": renamed, "messages": msgs}

    def remove_part(self, part):
        """Del Part: delete every component with this Part Name. Returns the count."""
        return self._remove("variant_tree", "remove_part", part, 10, "part")

    def remove_footprint(self, footprint):
        """Del Footprint: delete every component on this footprint. Returns the count."""
        return self._remove("footprint_tree", "remove_footprint", footprint, 1, "footprint")

    def _remove(self, tree, handler, name, col, what):
        g = self._g
        with self._op() as msgs:
            g["refresh_variants"]() if tree == "variant_tree" else g["refresh_footprint"]()
            iid = g["tv_key"](name)
            if not g[tree].exists(iid):
                raise KeyError("no %s %r" % (what, name))
            before = len(g["rows"])
            g[tree].selection_set(iid)
            g[handler]()
            return {"removed": before - len(g["rows"]), "messages": msgs}

    def heights(self):
        """Component Heights window: per footprint, shortest first: qty, height label,
        place height label, source and note."""
        g = self._g
        with self._op():
            if not g["rows"]:
                g["messagebox"].showerror("Component Heights", "Load a Pick & Place file first.")
            g["learn_footprint_heights"]()
            return self._heights_rows()

    def _heights_rows(self):
        g = self._g
        Counter = g["Counter"]
        counts = Counter(str(r[1]) for r in g["placeable_rows"]())
        suspects = g["suspect_part_keys"]()
        heights = {}
        for r in g["placeable_rows"]():
            heights.setdefault(str(r[1]), []).append(
                g["component_height"](r[1], r[10], suspects))
        order = sorted(counts, key=lambda f: (min(heights[f]), f))
        place = {}
        for r in g["placeable_rows"]():
            ph = g["place_height_for"](r[1], r[10], suspects)
            place.setdefault(str(r[1]), []).append(ph if ph is not None else float("inf"))
        out = []
        for fp in order:
            e = g["footprint_heights"].get(g["footprint_key"](fp), {})
            label = g["height_span_label"](heights[fp])
            out.append({"footprint": fp, "qty": counts[fp], "height_label": label,
                        "place_height_label": g["height_span_label"](place[fp]),
                        "source": e.get("source", "?"), "note": e.get("note", ""),
                        "mm": e.get("mm"), "unknown": "?" in label})
        return out

    def part_heights(self):
        """Per part: its height label (as the Part Name table), the height typed in for it
        (or None), whether its footprint is distrusted (IC on a chip footprint), and the
        place height its feeder would get."""
        g = self._g
        with self._lock:
            suspects = g["suspect_part_keys"]()
            out = {}
            for r in g["placeable_rows"]():
                part = str(r[10])
                e = out.setdefault(part, {"part": part, "footprints": set(),
                                          "key": g["part_name_key"](part)})
                e["footprints"].add(str(r[1]))
            for e in out.values():
                e["footprints"] = sorted(e["footprints"])
                e["height_label"] = g["part_height_label"](e["part"], e["footprints"], suspects)
                e["typed_in"] = g["part_heights"].get(e["key"])
                e["suspect"] = e["key"] in suspects
                e["place_height"] = [g["place_height_for"](fp, e["part"], suspects)
                                     for fp in e["footprints"]]
            return list(out.values())

    def set_part_height(self, part, mm):
        """Height for one part (beats its footprint's). mm=None clears it ("Use Footprint
        Height")."""
        g = self._g
        key = g["part_name_key"](part)
        with self._op():
            if mm is None:
                g["part_heights"].pop(key, None)
                g["refresh_table"]()
                return
            text = str(mm).strip()
            try:
                v = float(text)
            except ValueError:
                g["messagebox"].showerror("Height", "'%s' is not a number." % text)
                return
            if v <= 0:
                g["messagebox"].showerror("Height", "Height must be greater than zero.")
                return
            g["part_heights"][key] = v
            g["refresh_table"]()

    def set_footprint_height(self, footprint, mm):
        """Component Heights -> Set Height for a footprint (as shown in the table)."""
        g = self._g
        with self._op():
            g["learn_footprint_heights"]()
            if footprint not in {str(r[1]) for r in g["placeable_rows"]()}:
                raise KeyError("no footprint %r" % footprint)
            text = "" if mm is None else str(mm).strip()
            if not text:
                g["messagebox"].showerror("Component Heights", "Enter a height in millimetres.")
                return
            try:
                v = float(text)
            except ValueError:
                g["messagebox"].showerror("Component Heights", "'%s' is not a number." % text)
                return
            if v <= 0:
                g["messagebox"].showerror("Component Heights", "Height must be greater than zero.")
                return
            g["footprint_heights"][g["footprint_key"](footprint)] = {
                "mm": v, "source": "manual", "note": "typed in", "name": footprint}
            g["refresh_table"]()

    def set_place_height_settings(self, surface_mm, press_percent):
        """Component Heights: nozzle-touches-PCB height and press into paste (%)."""
        with self._op():
            self._read_place_settings(surface_mm, press_percent)

    def _read_place_settings(self, surface, press):
        g = self._g
        try:
            s, p = float(str(surface)), float(str(press))
        except ValueError:
            g["messagebox"].showerror("Component Heights", "Surface and press must be numbers.")
            return False
        if not 0 <= p < 100:
            g["messagebox"].showerror("Component Heights", "Press must be 0 to 99 %.")
            return False
        g["BOARD_SURFACE_MM"], g["PRESS_FRACTION"] = s, p / 100.0
        try:
            g["save_settings"]()
        except OSError:
            pass
        return True

    def apply_place_heights(self, surface_mm=None, press_percent=None):
        """Component Heights -> Apply Place Heights to Feeders. Returns the count."""
        g = self._g
        with self._op() as msgs:
            # the window's fields start as "%.2f" of the surface and "%g" of the press
            s = "%.2f" % g["BOARD_SURFACE_MM"] if surface_mm is None else surface_mm
            p = "%g" % (g["PRESS_FRACTION"] * 100) if press_percent is None else press_percent
            if not self._read_place_settings(s, p):
                return {"feeders": 0, "messages": msgs}
            if not g["machine_feeders"]():
                g["messagebox"].showinfo("Component Heights", "There is no machine program yet; "
                                         "Build uses these place heights.")
                return {"feeders": 0, "messages": msgs}
            n = g["apply_place_heights"]()
            g["refresh_feeder_table"]()
            g["messagebox"].showinfo(
                "Component Heights",
                "Place Height set on %d feeder(s): %.2f + height x %.0f%%. "
                "Parts with no known height were left as they were."
                % (n, g["BOARD_SURFACE_MM"], (1 - g["PRESS_FRACTION"]) * 100))
            return {"feeders": n, "messages": msgs}

    # ---- the board's marks (the program's mark line) ------------------------
    def marks(self):
        """the marks (fiducials) the machine reads before placing: [[x, y]] in the board's frame (after
        an alignment: the machine's), for the program's `mark` line"""
        return [list(m) for m in self._marks]

    def add_mark(self, x, y):
        self._marks.append((round(float(x), 4), round(float(y), 4)))
        self._fix_board_records()
        return self.marks()

    def set_mark(self, index, x, y):
        self._marks[int(index)] = (round(float(x), 4), round(float(y), 4))
        self._fix_board_records()
        return self.marks()

    def delete_mark(self, index):
        del self._marks[int(index)]
        self._fix_board_records()
        return self.marks()

    def _fix_board_records(self):
        """the program's board rows made to fit the parts: after an alignment the parts are where the
        machine reads them, so one panel at the first part (no turn), and the marks taken here. A template's
        rows (taken for another place of the board) would shift and turn every part"""
        g = self._g
        mm = g["machine_model"]
        comps = mm.get("comps") or []
        if not self.machine_fixes or not (self._aligned or self._marks) or not comps:
            return
        stacks = [r for r in mm["pre_records"] if r and r[0] == "stack"]
        board = [r for r in mm["pre_records"] if r and r[0] != "stack"]
        keep = [r for r in board if r[0] not in ("mark", "markext", "mirror", "mirror_create")]
        x0, y0 = comps[0][6], comps[0][7]
        out = [r for r in keep if r[0] == "pcb"]
        if not out:
            out.append(["pcb", "Machine", "Lock", "100", "100", "350", "150", "Front", "10", "10", "0", ""])
        if self._marks:
            out.append(["mark", "Whole", "Auto"] + [("%.3f" % v) for m in self._marks for v in m] + [""])
            out += [["markext", str(i), "0.8", "1.6", "1", "0", ""] for i in range(len(self._marks))]
        elif self._aligned:
            old = [r for r in board if r[0] == "mark"]
            if old:
                out.append(["mark", "Whole", "Auto", ""])     # the template's marks are of another place
        out += [r for r in keep if r[0] not in ("pcb",)]
        out.append(["mirror_create", "1", "1", str(x0), str(y0), "0", "0", "0", "0", "0", ""])
        out.append(["mirror", str(x0), str(y0), "0", "No", ""])
        mm["pre_records"] = stacks + out

    # ---- fiducials, single components ------------------------------------
    def fiducials(self):
        with self._lock:
            return [self._row_dict(i, r) for i, r in enumerate(self._g["rows"])
                    if self._g["is_fiducial"](r)]

    def add_fiducial(self, x, y):
        """Add FIDUCIAL: the fiducial line (designator FID) at x, y, first in the list."""
        g = self._g
        with self._op():
            g["_set_entry"](g["fid_x"], x)
            g["_set_entry"](g["fid_y"], y)
            g["add_FIDUCIAL"]()

    def edit_component(self, ref, part=None, x=None, y=None, rotation=None,
                       dx=0.0, dy=0.0, drot=0.0, all_same_designator=False):
        """The row editor (double-click): absolute values and/or offsets; pads and the
        reference move and turn with the part."""
        g = self._g
        with self._op():
            target = self._row(ref)
            sel = str(target[0])
            new_cx = float(target[2] if x is None else x) + float(dx or 0)
            new_cy = float(target[3] if y is None else y) + float(dy or 0)
            new_rot = float(target[9] if rotation is None else rotation) + float(drot or 0)
            new_comment = str(target[10]) if part is None else part
            delta_x = new_cx - float(target[2])
            delta_y = new_cy - float(target[3])
            delta_rot = new_rot - float(target[9])
            for r in g["rows"]:
                if r is target or (all_same_designator and str(r[0]) == sel):
                    r[10] = new_comment
                    r[2] = round(float(r[2]) + delta_x, 3)
                    r[4] = round(float(r[4]) + delta_x, 3)
                    r[6] = round(float(r[6]) + delta_x, 3)
                    r[3] = round(float(r[3]) + delta_y, 3)
                    r[5] = round(float(r[5]) + delta_y, 3)
                    r[7] = round(float(r[7]) + delta_y, 3)
                    g["set_component_rotation"](r, float(r[9]) + delta_rot)
                    if delta_rot:
                        g["angle_flags"].pop(id(r), None)
            g["refresh_table"]()

    def delete_component(self, ref, all_same_designator=False):
        """The row editor's Delete."""
        g = self._g
        with self._op():
            target = self._row(ref)
            sel = str(target[0])
            if all_same_designator:
                g["rows"][:] = [r for r in g["rows"] if str(r[0]) != sel]
            elif target in g["rows"]:
                g["rows"].remove(target)
            g["refresh_table"]()

    def turn_component(self, ref, angle=None, delta=None, all_like=False):
        """2D map: turn one part (absolute angle or by delta) about its centre; with
        all_like every part of the same Part Name by the same delta."""
        g = self._g
        with self._op():
            g["map_state"]["selected"] = self._row(ref)
            g["map_all_var"].set(bool(all_like))
            g["_map_rotate"](delta=delta, absolute=angle)
            g["map_all_var"].set(False)

    # ================================================================== board
    def set_board(self, width=None, height=None, keepout=None):
        """Board Dimensions fields (mm). Empty/None leaves a field as it is; "" clears."""
        g = self._g
        with self._op():
            for name, v in (("board_width", width), ("board_height", height),
                            ("board_KeepOutwidth", keepout)):
                if v is not None:
                    g["_set_entry"](g[name], v)

    def board(self):
        g = self._g
        with self._lock:
            pts = [(float(r[2]), float(r[3])) for r in g["placeable_rows"]()]
            return {"width": g["board_width"].get(), "height": g["board_height"].get(),
                    "keepout": g["board_KeepOutwidth"].get(),
                    "rotation_field": g["board_rotation_var"].get(),
                    "transform": list(g["board_xform"]), "mirrored": g["board_is_mirrored"](),
                    "extent": (min(p[0] for p in pts), min(p[1] for p in pts),
                               max(p[0] for p in pts), max(p[1] for p in pts)) if pts else None}

    def check_board_size(self):
        """validate_board_coordinates(): True, or False with the error in messages."""
        with self._op(raise_on_error=False) as msgs:
            ok = bool(self._g["validate_board_coordinates"]())
            return {"ok": ok, "messages": msgs}

    def rotate_board_quarter(self, degrees):
        """+90 / -90 / 180 buttons: turn the board about its own centre (fiducials stay).
        Returns the pivot."""
        with self._op():
            return self._g["rotate_board_quarter"](degrees)

    def rotate_board(self, degrees, pivot=None):
        """Turn every row (fiducials too) by `degrees` about `pivot` (default 0,0)."""
        with self._op():
            self._g["rotate_board_polar"](degrees, tuple(pivot) if pivot else None)

    def set_board_rotation(self, angle):
        """The board Rotation field: turns the board by the change from the angle it had
        (about 0,0), as typing a whole new value into the field does."""
        with self._op():
            self._g["board_rotation_var"].set(str(angle))

    def flip_x(self):
        """Flip X: mirror left-right inside the board width (set_board first)."""
        with self._op():
            self._g["flip_x"]()

    def flip_y(self):
        """Flip Y: mirror top-bottom inside the board height (set_board first)."""
        with self._op():
            self._g["flip_y"]()

    def mirror_board(self):
        """Turn the board over (what the alignment does for a bottom side): positions and
        pads mirrored about X=0, angles mirrored, patterned parts from pad 1. The tool does
        this only inside apply_alignment; here it is followed by a table refresh."""
        with self._op():
            self._g["mirror_board"]()
            self._g["refresh_table"]()

    def board_side(self):
        """("top" | "bottom", reason) -- from the Gerber copper if loaded, else the layer
        column."""
        with self._lock:
            return self._g["board_side_auto"]()

    def apply_offset(self, x, y):
        """1-Point (FID): shift the board so the first FID row lands on x, y."""
        g = self._g
        with self._op():
            g["_set_entry"](g["board_offsetX"], x)
            g["_set_entry"](g["board_offsety"], y)
            g["ApplyOffset"]()

    # ---- additions of the machine wizard (not in the tool) ----------------------
    def shift_board(self, dx, dy):
        """Move every row (and the Gerber underlay) by dx, dy mm -- ApplyOffset without
        the FID reference."""
        g = self._g
        dx, dy = float(dx), float(dy)
        with self._op():
            g["xform_then"](1.0, 0.0, 0.0, 1.0, dx, dy)
            for r in g["rows"]:
                for xi, yi in ((2, 3), (4, 5), (6, 7)):
                    try:
                        r[xi] = round(float(r[xi]) + dx, 3)
                        r[yi] = round(float(r[yi]) + dy, 3)
                    except (ValueError, TypeError, IndexError):
                        continue
            g["refresh_table"]()
        return {"dx": dx, "dy": dy}

    def board_bounds(self):
        """The board's extent in the current (P&P) frame and where it came from: the Gerber
        outline layer, else all Gerber shapes, else the parts (fiducials included)."""
        layers = self.gerber_shapes(kinds=None, all_layers=True)["layers"]
        for name, pick in (("outline", lambda L: L["kind"] == "outline"), ("gerber", lambda L: True)):
            xs, ys = [], []
            for L in layers:
                if not pick(L):
                    continue
                for s in L["shapes"]:
                    if s[0] == "C":
                        xs += [s[1] - s[3], s[1] + s[3]]
                        ys += [s[2] - s[3], s[2] + s[3]]
                    else:
                        xs += [p[0] for p in s[1]]
                        ys += [p[1] for p in s[1]]
            if xs:
                return {"source": name, "bounds": (min(xs), min(ys), max(xs), max(ys))}
        with self._lock:
            pts = [(float(r[2]), float(r[3])) for r in self._g["rows"]]
        if not pts:
            return {"source": None, "bounds": None}
        return {"source": "parts", "bounds": (min(p[0] for p in pts), min(p[1] for p in pts),
                                              max(p[0] for p in pts), max(p[1] for p in pts))}

    def zero_bottom_left(self):
        """Put 0, 0 on the board's bottom-left corner (Gerber outline if there is one),
        whatever origin the design used. Returns the shift and what the corner came from."""
        b = self.board_bounds()
        if not b["bounds"]:
            raise ToolError("nothing loaded")
        x0, y0 = b["bounds"][0], b["bounds"][1]
        self.shift_board(-x0, -y0)
        return {"dx": -x0, "dy": -y0, "source": b["source"],
                "size": (b["bounds"][2] - x0, b["bounds"][3] - y0)}

    def gerber_shapes(self, kinds=("outline", "silk", "copper"), all_layers=False, decimals=3):
        """The loaded Gerber layers in the current P&P frame (through board_xform and the
        Gerber offset), for drawing: {"layers": [{key, label, kind, side, visible,
        shapes}]}; a shape is ["C", x, y, r, dark] or ["P", [[x, y], ...], dark]. Only the
        layers the tool shows (visible) unless all_layers."""
        g = self._g
        with self._lock:
            gs = g["gerber_state"]
            a, b, c, d, e, f = g["board_xform"]
            ox, oy = gs["offset"]
            tx = lambda x, y: (round(a * (x - ox) + b * (y - oy) + e, decimals),
                               round(c * (x - ox) + d * (y - oy) + f, decimals))
            k = math.sqrt(abs(a * d - b * c))
            out = []
            for L in gs["layers"]:
                vis = bool(gs["visible"].get(L["key"]))
                if (kinds and L["kind"] not in kinds) or not (vis or all_layers):
                    continue
                shapes = []
                for s in L["shapes"]:
                    if s[0] == "C":
                        x, y = tx(s[1], s[2])
                        shapes.append(["C", x, y, round(s[3] * k, decimals), bool(s[4])])
                    else:
                        shapes.append(["P", [list(tx(px, py)) for px, py in s[1]], bool(s[2])])
                out.append({"key": L["key"], "label": L["label"], "kind": L["kind"], "side": L["side"],
                            "visible": vis, "shapes": shapes})
            return {"layers": out, "mirrored": bool(g["board_is_mirrored"]())}

    # ================================================================== alignment
    def suggest_alignment(self, how_many=6):
        """Sighting pairs, best first: [{a, b, c, baseline, isolation, c_off_line}] where
        a/b/c are component dicts (c = the suggested third, check point; may be None)."""
        g = self._g
        with self._lock:
            out = []
            for baseline, isolation, a, b in g["suggest_alignment_pairs"](g["rows"], how_many):
                c, off = g["suggest_check_point"](a, b)
                out.append({"a": self._row_dict(self._index_of(a), a),
                            "b": self._row_dict(self._index_of(b), b),
                            "c": self._row_dict(self._index_of(c), c) if c is not None else None,
                            "baseline": baseline, "isolation": _num(isolation),
                            "c_off_line": off})
            return out

    def identifiability(self):
        """Per component index: distance to the nearest identical part (None = unique)."""
        g = self._g
        with self._lock:
            s = g["alignment_identifiability"](g["rows"])
            return {i: _num(s.get(id(r))) for i, r in enumerate(g["rows"]) if id(r) in s}

    def check_point(self, a, b):
        """suggest_check_point for two sighted components: (component dict, off line mm)."""
        g = self._g
        with self._lock:
            c, off = g["suggest_check_point"](self._row(a), self._row(b))
            return (self._row_dict(self._index_of(c), c) if c is not None else None), off

    @staticmethod
    def rigid_fit(design, measured):
        """Least-squares rotation + shift (scale reported): (theta, scale, pc, qc)."""
        return _engine_static("rigid_fit")(design, measured)

    def plan_alignment(self, a, b, measured_a, measured_b, side="auto", c=None,
                       measured_c=None):
        """Work out how the board sits from 2 (or 3) sighted components. a/b/c are
        component refs (index or designator) or board points [x, y] (design coordinates),
        measured_* the camera's (x, y). side:
        "auto" | "top" | "bottom". Returns the plan dict plus "report" (the dialog's text
        lines), "scale_ok", "bad" and "warnings"."""
        g = self._g
        with self._op():
            ra, rb = self._ref_row(a), self._ref_row(b)
            rc = self._ref_row(c) if c is not None else None
            out = self._plan(ra, rb, rc, measured_a, measured_b, measured_c, side)
            if out is not None:
                out.pop("_plan")
            return out

    def _plan(self, ra, rb, rc, ma, mb, mc, side):
        g = self._g
        ma, mb = (float(ma[0]), float(ma[1])), (float(mb[0]), float(mb[1]))
        mc = (float(mc[0]), float(mc[1])) if mc is not None else None
        pa = (float(ra[2]), float(ra[3]))
        pb = (float(rb[2]), float(rb[3]))
        pc = (float(rc[2]), float(rc[3])) if (rc is not None and mc is not None) else None
        try:
            plan = g["plan_alignment"](pa, pb, ma, mb, str(side).lower(), pc, mc)
        except ValueError as e:
            g["messagebox"].showerror("2-Point Alignment", str(e))
            return None
        tol = g["ALIGN_SCALE_TOLERANCE"]
        lines = [
            "Side up       %s%s" % (plan["side"].upper(),
                                    "   (board will be turned over)" if plan["mirror"] else ""),
            "decided by    %s" % plan["decided_by"],
            "Rotation      %+8.3f deg" % plan["theta"],
            "Scale         %8.5f   (A-B %.3fmm in the design, %.3fmm measured)"
            % (plan["scale"], plan["len_d"], plan["len_m"]),
        ]
        bad = abs(plan["scale"] - 1.0) > tol
        warnings = []
        if pc is not None:
            cand = plan["candidates"]
            lines.insert(2, "Fit           all three points (best fit, average error %.3fmm)"
                         % plan["rms"])
            lines.append("Leftover      A %.3f   B %.3f   C %.3f mm" % tuple(plan["residuals"]))
            lines.append("Other way up  %.3fmm average -- %s"
                         % (cand[not plan["mirror"]]["rms"],
                            "ruled out" if cand[not plan["mirror"]]["rms"] > 1.0
                            else "close; check the side"))
            if plan["outlier"]:
                who = {"A": ra, "B": rb, "C": rc}[plan["outlier"]]
                lines.append("              -> %s (point %s) is the odd one out; it was "
                             "probably sighted a little off its centre"
                             % (who[0], plan["outlier"]))
            if plan["overrides_data"]:
                lines.append("              -> the measurement disagrees with the data; "
                             "going by the measurement")
            if plan["check_failed"]:
                w = ("WARNING: neither way puts C within %.1fmm -- one of the three "
                     "was sighted on the wrong part." % g["ALIGN_CHECK_TOLERANCE_MM"])
                lines.append(w)
                warnings.append(w)
                bad = True
        if abs(plan["scale"] - 1.0) > tol:
            w = ("WARNING: scale off by %.2f%% -- A or B was sighted on the wrong "
                 "part." % (abs(plan["scale"] - 1.0) * 100))
            lines.append(w)
            warnings.append(w)
        out = json.loads(json.dumps(plan, default=list))
        out["candidates"] = {"normal": out["candidates"].pop("false", None),
                             "turned_over": out["candidates"].pop("true", None)}
        out.update(report=lines, bad=bad, warnings=warnings,
                   scale_ok=abs(plan["scale"] - 1.0) <= tol, _plan=plan)
        return out

    def apply_alignment(self, a, b, measured_a, measured_b, side="auto", c=None,
                        measured_c=None, use_scale=False):
        """2-Point Align -> Apply to Board: turn over if needed, then fit the board onto the
        measured points (all three when given). Returns theta, scale, side, mirror and
        where B should now read under the camera."""
        g = self._g
        with self._op() as msgs:
            ra, rb = self._ref_row(a), self._ref_row(b)
            rc = self._ref_row(c) if c is not None else None
            mcv = (float(measured_c[0]), float(measured_c[1])) if measured_c is not None else None
            out = self._plan(ra, rb, rc, measured_a, measured_b, measured_c, side)
            if out is None:
                return None
            plan = out.pop("_plan")
            ma = (float(measured_a[0]), float(measured_a[1]))
            mb = (float(measured_b[0]), float(measured_b[1]))
            summary = ("%sRotate by %+.3f deg and place point A at X %.3f, Y %.3f.\n\n"
                       "Apply to all %d components?"
                       % ("Turn the board over (%s side up), then:\n" % plan["side"]
                          if plan["mirror"] else "",
                          plan["theta"], ma[0], ma[1], len(g["rows"])))
            if not g["messagebox"].askyesno("Apply Alignment", summary):
                return None
            if plan["mirror"]:                       # board points turn over with the board
                for r in (ra, rb, rc):
                    if r is not None and self._is_point(r):
                        r[2] = -float(r[2])
            theta, scale = g["apply_alignment"](plan, ra, rb, ma, mb, use_scale=bool(use_scale),
                                                row_c=rc, mc=mcv)
            if self._is_point(rb):                   # not in the list, so not moved: where it was seen
                rb[2], rb[3] = mb
            self._aligned = True
            if self._marks:
                msgs.append({"kind": "warning", "title": "Marks", "text": "The board was moved: the %d mark(s) "
                             "taken before were cleared; take them again." % len(self._marks)})
                self._marks = []
            msg = ["Applied: %s side up, turned %+.3f deg." % (plan["side"], theta)]
            if plan["mirror"]:
                msg.append("The board was turned over: positions, pads and angles are mirrored, "
                           "and patterned parts took their angle from the mirrored pad 1.")
            msg.append("Jog the camera back to %s -- it should read X %.3f, Y %.3f."
                       % (rb[0], float(rb[2]), float(rb[3])))
            g["messagebox"].showinfo("2-Point Alignment", "\n\n".join(msg))
            out.update(applied_theta=theta, fit_scale=scale,
                       check_b={"designator": str(rb[0]), "x": float(rb[2]), "y": float(rb[3])},
                       messages=msgs)
            return out

    # ================================================================== machine program
    def prepare_build(self, top_only=True):
        """Build From Pick & Place, as the dialog opens: the parts of the table (in table
        order; bottom layer left out with top_only) each assigned a feeder by the layout
        rules, against the loaded machine file as template. Returns the dialog's table."""
        g = self._g
        with self._op(keep_build=True) as msgs:
            if not g["rows"]:
                g["messagebox"].showerror("Build Machine File",
                                          "Load a Pick & Place file in the first tab.")
                return None
            source_rows = g["pnp_rows_in_table_order"]()
            has_template = bool(g["machine_feeders"]())
            chosen = [r for r in source_rows
                      if not top_only
                      or str(r[8]).strip().lower() in ("toplayer", "top", "t", "")]
            parts = g["auto_assign_feeders"](g["collect_pnp_parts"](chosen))
            self._build = {"rows": chosen, "parts": parts, "has_template": has_template,
                           "source_rows": source_rows, "top_only": top_only}
            out = self._build_view()
            out["messages"] = msgs
            return out

    def _build_view(self):
        b = self._build
        skipped = len(b["source_rows"]) - len(b["rows"])
        return {"parts": [{"index": i, "footprint": e["footprint"], "value": e["value"],
                           "qty": len(e["rows"]), "feeder": e["feeder"], "nozzles": e["nozzles"],
                           "pick": e["pick"], "place": e["place"], "speed": e["speed"],
                           "source": e["source"], "designators": [str(r[0]) for r in e["rows"]]}
                          for i, e in enumerate(b["parts"])],
                "components": len(b["rows"]), "bottom_excluded": skipped,
                "has_template": b["has_template"], "top_only": b["top_only"]}

    def edit_build_part(self, index, feeder=None, nozzles=None, pick=None, place=None,
                        speed=None, share_feeder=True):
        """The Build dialog's Edit Selected -> Save for part `index` of prepare_build().
        nozzles: "13" or [1, 3]. A feeder already used by another part is refused unless
        share_feeder ("Use it for both?")."""
        g = self._g
        if self._build is None:
            raise RuntimeError("call prepare_build() first")
        with self._op(answer=bool(share_feeder), keep_build=True) as msgs:
            entry = self._build["parts"][index]
            if nozzles is None:
                chosen = g["parse_nozzles"](entry["nozzles"]) or [1]
            else:
                text = nozzles if isinstance(nozzles, str) else "".join(str(n) for n in nozzles)
                chosen = [n for n in (1, 2, 3, 4) if str(n) in text]
            if not chosen:
                g["messagebox"].showerror("Invalid Nozzles", "Select at least one nozzle.")
                return None
            fid = str(entry["feeder"] if feeder is None else feeder).strip()
            if not fid:
                g["messagebox"].showerror("Invalid Feeder", "Feeder ID cannot be empty.")
                return None
            clash = [e for e in self._build["parts"] if e is not entry and str(e["feeder"]) == fid]
            if clash and not g["messagebox"].askyesno(
                    "Feeder Already Used",
                    "Feeder %s is already assigned to %s / %s.\n\nUse it for both?"
                    % (fid, clash[0]["footprint"], clash[0]["value"])):
                return None
            given = {"pick": pick, "place": place, "speed": speed}
            texts = {k: str(entry[k] if given[k] is None else given[k]).strip()
                     for k in ("pick", "place", "speed")}
            for key in ("pick", "place", "speed"):
                text = texts[key]
                if text:
                    try:
                        float(text)
                    except ValueError:
                        g["messagebox"].showerror("Invalid Value", "'%s' is not a number." % text)
                        return None
                # as the dialog: each value is written as it is checked, so a bad later
                # value leaves the earlier ones written
                entry[key] = text
            entry["feeder"] = fid
            entry["nozzles"] = g["format_nozzles"](chosen)
            out = self._build_view()
            out["messages"] = msgs
            return out

    def build_machine(self, top_only=True, keep_board_records=None):
        """Build: the prepared assignment (prepare_build + edit_build_part), or a fresh one
        with `top_only`, written as the machine program. keep_board_records (default: when
        a machine file is loaded) keeps its board / mark / mirror rows."""
        g = self._g
        if self._build is None:
            self.prepare_build(top_only)
        b = self._build
        with self._op() as msgs:
            if not b["parts"]:
                g["messagebox"].showerror("Build Machine File", "Nothing to build.")
                return None
            if not g["confirm_drop_machine_layout"]("Build"):
                return None
            untaught = [e for e in b["parts"] if e["source"] == "new (needs teaching)"]
            message = ("Build a machine program with %d feeders and %d components?"
                       % (len(b["parts"]), len(b["rows"])))
            if untaught:
                message += ("\n\n%d feeder(s) have no taught position and will be given "
                            "their slot's estimated position. Teach them on the machine "
                            "before running." % len(untaught))
            if not g["messagebox"].askyesno("Build Machine File", message):
                return None
            keep = b["has_template"] if keep_board_records is None else \
                bool(keep_board_records) and b["has_template"]
            g["build_machine_from_pnp"](b["parts"], b["rows"], keep_board_records=keep)
            g["messagebox"].showinfo(
                "Build Machine File",
                "Built %d feeders and %d components.\n\nReview the Feeder Setup tab, then "
                "use Export Machine File." % (len(g["machine_feeders"]()),
                                              len(g["machine_model"]["comps"])))
            return {"feeders": len(g["machine_feeders"]()),
                    "components": len(g["machine_model"]["comps"]),
                    "untaught": ["%s/%s" % (e["footprint"], e["value"]) for e in untaught],
                    "messages": msgs}

    def adopt_layout(self, path):
        """Take Layout From Machine File: parts go to the feeders they are on in a file the
        machine wrote (stage 1); the rest move clear of them."""
        g = self._g
        with self._op() as msgs:
            if not g["machine_model"]["comps"]:
                g["messagebox"].showinfo("Take Layout From Machine File",
                                         "There is no machine program.")
                return None
            matched, missing = g["adopt_layout_from_machine_file"](os.fspath(path))
            g["refresh_feeder_table"]()
            g["refresh_machine_comp_table"]()
            g["messagebox"].showinfo(
                "Take Layout From Machine File",
                "%d part(s) now on the feeders they are on in the machine file (stage 1).\n"
                "%d part(s) not in it moved clear of those feeders; Export Stages puts them "
                "in the later stages." % (len(matched), len(missing)))
            return {"matched": list(matched), "missing": list(missing), "messages": msgs}

    def program(self):
        """The machine program: feeders, placements, board records, link state."""
        g = self._g
        with self._lock:
            mm = g["machine_model"]
            return {"loaded": g["machine_is_loaded"](), "path": mm["path"],
                    "linked": bool(mm.get("linked")), "stage1": list(mm.get("stage1") or []),
                    "feeders": self.feeders(), "placements": self.placements(),
                    "board_records": self.board_records()}

    def feeders(self):
        """Feeder rows (stack), in file order, as dicts (+ the raw 'fields')."""
        g = self._g
        with self._lock:
            overlap = g["overlapping_feeders"]()
            return [self._feeder_dict(f, overlap) for f in g["machine_feeders"]()]

    def _field_index(self, field, names, spec_labels):
        if isinstance(field, int):
            return field
        if field in names:
            return names[field]
        for i, label in spec_labels:
            if label == field:
                return i
        raise KeyError("unknown field %r" % field)

    def set_feeder_fields(self, feeder_id, values):
        """Edit Feeder -> Save / Apply: {field: value} (field = key of FEEDER_FIELDS, index
        or the tool's label). Values are checked as the editor does (numbers, nozzle
        digits); every field the editor shows is written back stripped."""
        g = self._g
        with self._op():
            f = self._feeder(feeder_id)
            self._field_editor(f, g["FEEDER_COLS"], g["FEEDER_EXTRA_COLS"], values,
                               FEEDER_FIELDS, g["refresh_feeder_table"])

    def _field_editor(self, fields, spec, extra_spec, values, names, on_saved):
        """open_field_editor's apply_changes, with `values` typed into its fields."""
        g = self._g
        labels = [(i, l) for i, l, _ in spec] + [(i, l) for i, l, _ in (extra_spec or [])]
        given = {self._field_index(k, names, labels): v for k, v in (values or {}).items()}
        shown = {i for i, _ in labels}
        bad = set(given) - shown
        if bad:
            raise KeyError("field(s) %s are not editable here" % sorted(bad))
        widgets = {}
        for index, label, kind in spec:
            current = g["field_at"](fields, index)
            if kind == "nozzle":
                selected = g["parse_nozzles"](current)
                if selected is None:
                    widgets[index] = ("text", str(given.get(index, current)))
                else:
                    if index in given:
                        v = given[index]
                        text = v if isinstance(v, str) else "".join(str(n) for n in v)
                        selected = [n for n in (1, 2, 3, 4) if str(n) in text]
                    widgets[index] = ("nozzle", selected)
            elif kind in ("yesno", "part"):
                widgets[index] = ("text", str(given.get(index, current)))
            else:
                widgets[index] = (kind, str(given.get(index, current)))
        for index, label, hint in (extra_spec or []):
            widgets[index] = ("text", str(given.get(index, g["field_at"](fields, index))))
        staged = {}
        for index, (kind, val) in widgets.items():
            if kind == "nozzle":
                if not val:
                    g["messagebox"].showerror("Invalid Nozzles",
                                              "At least one nozzle must be selected.")
                    return False
                staged[index] = g["format_nozzles"](val)
                continue
            text = val.strip()
            if kind == "int":
                try:
                    int(float(text))
                except ValueError:
                    g["messagebox"].showerror("Invalid Value", "'%s' is not a valid number." % text)
                    return False
            elif kind == "float":
                try:
                    float(text)
                except ValueError:
                    g["messagebox"].showerror("Invalid Value", "'%s' is not a valid number." % text)
                    return False
            staged[index] = text
        for index, value in staged.items():
            g["set_field"](fields, index, value)
        if on_saved:
            on_saved()
        return True

    def add_feeder(self, values=None):
        """Add Feeder (copy of the last feeder, next free id, no part), then its editor
        with `values`. Returns the new feeder's dict."""
        g = self._g
        with self._op():
            n = len(g["machine_model"]["pre_records"])
            g["add_feeder"]()
            if len(g["machine_model"]["pre_records"]) == n:
                return None
            new = g["selected_feeder"]()
            if values:
                self._field_editor(new, g["FEEDER_COLS"], g["FEEDER_EXTRA_COLS"], values,
                                   FEEDER_FIELDS, g["refresh_feeder_table"])
            return self._feeder_dict(new)

    def delete_feeder(self, feeder_id):
        """Edit Feeder -> Delete. Placements on it keep that Feeder ID."""
        g = self._g
        with self._op():
            f = self._feeder(feeder_id)
            if not g["messagebox"].askyesno(
                    "Delete Feeder",
                    "Delete feeder %s?\n\nComponents still assigned to it will keep "
                    "that Feeder ID." % g["field_at"](f, 1)):
                return
            g["machine_model"]["pre_records"].remove(f)
            g["refresh_feeder_table"]()

    def group_edit(self, field, value, scope="8mm", feeder_ids=None):
        """Group Edit: one setting on many feeders. scope "selected" (feeder_ids), "8mm"
        or "all" (all but the fiducial). Returns the number changed."""
        g = self._g
        with self._op() as msgs:
            if not g["machine_feeders"]():
                g["messagebox"].showinfo("Group Edit", "There are no feeders to edit.")
                return {"changed": 0, "messages": msgs}
            fields = g["group_edit_fields"]()
            labels = [(i, l) for i, l, _ in fields]
            index = self._field_index(field, FEEDER_FIELDS, labels)
            spec = next((f for f in fields if f[0] == index), None)
            if spec is None:
                raise KeyError("field %r cannot be group-edited" % field)
            chosen = [f for f in (self._feeder(i) for i in (feeder_ids or []))
                      if not g["feeder_is_fiducial"](f)]
            if scope == "selected" and not chosen:
                raise ValueError("scope 'selected' needs feeder_ids (not the fiducial's)")
            index, label, kind = spec
            value = str(value).strip()
            if kind in ("float", "int"):
                try:
                    v = float(value)
                except ValueError:
                    g["messagebox"].showerror("Group Edit", "'%s' is not a number." % value)
                    return None
                value = ("%.2f" % v) if kind == "float" else str(int(round(v)))
            elif kind == "nozzle":
                nz = g["parse_nozzles"](value)
                if not nz:
                    g["messagebox"].showerror("Group Edit", "Nozzles are digits 1-4, e.g. 13.")
                    return None
                value = g["format_nozzles"](nz)
            elif kind == "yesno":
                value = "Yes" if value.lower() in ("yes", "y", "1", "true") else "No"
            if scope == "selected":
                targets = chosen
            elif scope in ("8mm", "all"):
                targets = [f for f in g["machine_feeders"]() if not g["feeder_is_fiducial"](f)]
                if scope == "8mm":
                    fa = g["field_at"]
                    targets = [f for f in targets
                               if g["tape_width_for"](str(fa(f, 8)).split("/", 1)[0]) == 8
                               or (not str(fa(f, 8)).strip()
                                   and str(fa(f, 1)) in g["EIGHT_MM_BANK"])]
            else:
                raise ValueError("scope must be 'selected', '8mm' or 'all'")
            n = g["group_edit_feeders"](targets, index, value)
            g["refresh_feeder_table"]()
            g["messagebox"].showinfo("Group Edit", "%s set to %s on %d feeder(s)." % (label, value, n))
            return {"changed": n, "value": value, "messages": msgs}

    def arrange_feeders(self):
        """Arrange Feeders: lay the feeders out again by the rules (fiducial 49, busiest
        8mm centre-out, big from 20); placement order kept. Returns parts moved."""
        g = self._g
        with self._op() as msgs:
            if not g["machine_model"]["comps"]:
                g["messagebox"].showinfo("Arrange Feeders",
                                         "There is no machine program to arrange.")
                return {"moved": 0, "messages": msgs}
            if not g["confirm_drop_machine_layout"]("Arrange Feeders"):
                return None
            if not g["messagebox"].askyesno(
                    "Arrange Feeders",
                    "Lay the feeders out again by the rules?\n\n"
                    "  - fiducial on feeder 49, placement skipped\n"
                    "  - busiest 8mm parts in the middle of 1-17: 7, 8, 6, 9, 5, 10 ...\n"
                    "  - 8mm feeders: nozzles 1 and 3, feed rhythm 4\n"
                    "  - big feeders (M7, SO-16, 1210 ...) from 20 up\n\n"
                    "Placement order and coordinates stay as they are."):
                return None
            moved = g["arrange_feeders"]()
            g["refresh_feeder_table"]()
            g["refresh_machine_comp_table"]()
            g["messagebox"].showinfo("Arrange Feeders",
                                     "%d part(s) moved to a new feeder. Teach any slot that has "
                                     "no taught position before running." % moved)
            return {"moved": moved, "messages": msgs}

    def enforce_fiducial_rules(self):
        """Fiducial on feeder 49 (Options), placement skipped, first in both lists; a part
        found on 49 moves. Returns True if anything changed. (The tool applies this after
        every build / arrange / adopt and on each list change of a linked program.)"""
        g = self._g
        with self._op():
            changed = g["enforce_fiducial_feeder"]()
            g["refresh_feeder_table"]()
            g["refresh_machine_comp_table"]()
            return bool(changed)

    def package_library(self):
        """Package Height Library: per footprint the feeders, components, and the pick /
        place heights and speeds its feeders carry."""
        g = self._g
        with self._lock:
            out = []
            for fp in g["machine_footprints"]():
                fs = self._pkg_feeders(fp)
                key = g["part_match_key"](fp)
                out.append({"footprint": fp, "feeders": [str(g["field_at"](f, 1)) for f in fs],
                            "components": sum(1 for c in g["machine_model"]["comps"]
                                              if g["part_match_key"](g["field_at"](c, 5)) == key),
                            "pick": sorted({str(g["field_at"](f, 9)) for f in fs}),
                            "place": sorted({str(g["field_at"](f, 11)) for f in fs}),
                            "speed": sorted({str(g["field_at"](f, 16)) for f in fs})})
            return out

    def _pkg_feeders(self, footprint):
        g = self._g
        key = g["part_match_key"](footprint)
        return [f for f in g["machine_feeders"]()
                if g["part_match_key"](g["feeder_footprint"](f)) == key]

    def apply_package(self, footprint, pick=None, place=None, speed=None):
        """Package Height Library -> Apply to Package (blank = unchanged)."""
        g = self._g
        with self._op() as msgs:
            if not g["machine_is_loaded"]():
                g["messagebox"].showerror("Package Library", "Import a machine file first.")
                return None
            updates = []
            for index, val, label in ((9, pick, "Pick Height"), (11, place, "Place Height"),
                                      (16, speed, "Speed")):
                text = "" if val is None else str(val).strip()
                if not text:
                    continue
                try:
                    float(text)
                except ValueError:
                    g["messagebox"].showerror("Invalid Value",
                                              "%s: '%s' is not a valid number." % (label, text))
                    return None
                updates.append((index, text))
            if not updates:
                g["messagebox"].showinfo("Package Library",
                                         "Nothing to apply -- all fields are blank.")
                return {"feeders": 0, "messages": msgs}
            targets = self._pkg_feeders(footprint)
            if not targets:
                g["messagebox"].showwarning(
                    "Package Library",
                    "No feeder carries footprint '%s'.\n\nSet a feeder's "
                    "'Part (Footprint/Value)' field first." % footprint)
                return {"feeders": 0, "messages": msgs}
            for f in targets:
                for index, text in updates:
                    g["set_field"](f, index, text)
            g["refresh_feeder_table"]()
            return {"feeders": len(targets), "messages": msgs}

    def board_records(self):
        """pcb / mark / markext / mirror ... rows, as raw field lists."""
        with self._lock:
            return [list(r) for r in self._g["machine_board_records"]()]

    def set_board_record(self, index, values):
        """Edit Record: {column index: text} on board record `index`."""
        g = self._g
        with self._op():
            record = g["machine_board_records"]()[index]
            spec = [(i, "Col %d" % (i + 1), "text") for i in range(1, len(record))]
            self._field_editor(record, spec, None, values, {}, g["refresh_board_table"])

    # ================================================================== placement order
    def placements(self):
        """The #SMD rows in placement order, as dicts (+ raw 'fields')."""
        with self._lock:
            return [self._comp_dict(i, c) for i, c in enumerate(self._g["machine_model"]["comps"])]

    def auto_sort(self):
        """Auto Sort: shortest bodies first, footprints grouped, nearest-neighbour route."""
        with self._op():
            self._g["auto_sort_machine_table"]()

    def move(self, refs, direction):
        """Top / Up / Down / Bottom for placements (designators or positions).
        direction: "top", "bottom", -1 (up) or +1 (down)."""
        if direction not in ("top", "bottom", -1, 1):
            raise ValueError("direction must be 'top', 'bottom', -1 or 1")
        with self._op():
            self._select_comps(refs)
            self._g["move_comps"](direction)

    def move_before(self, ref, target):
        """Drag and drop in the placement table: `ref` goes where `target` is."""
        g = self._g
        with self._op():
            src, dst = self._comp(ref), self._comp(target)
            if src is dst:
                return
            comps = g["machine_model"]["comps"]
            comps.remove(src)
            comps.insert(comps.index(dst), src)
            g["refresh_machine_comp_table"](select=[g["machine_item_id"](src)])

    def toggle_skip(self, refs):
        """Toggle Skip: if any of them is placed, skip them all; else un-skip them all."""
        with self._op():
            self._select_comps(refs)
            self._g["toggle_comp_skip"]()

    def set_skip(self, refs, skip=True):
        """Skip (or place) these placements -- Toggle Skip pressed once or twice."""
        g = self._g
        with self._op():
            comps = self._select_comps(refs)
            for _ in range(2):
                state = [str(g["field_at"](c, 9)).strip().lower() == "yes" for c in comps]
                if all(s == bool(skip) for s in state):
                    break
                self._select_comps(refs)
                g["toggle_comp_skip"]()

    def assign_feeder(self, refs, feeder_id=None, nozzle=None):
        """Assign Feeder: put placements on another feeder and/or nozzle."""
        g = self._g
        with self._op():
            comps = [self._comp(r) for r in ([refs] if isinstance(refs, (str, int)) else refs)]
            if not comps:
                g["messagebox"].showinfo("Assign Feeder", "Select one or more components first.")
                return
            ids = [str(g["field_at"](f, 1)) for f in g["machine_feeders"]()]
            choice = -1
            if feeder_id is not None:
                if str(feeder_id) not in ids:
                    raise KeyError("no feeder %r" % feeder_id)
                choice = ids.index(str(feeder_id))
            nz = "(unchanged)" if nozzle is None else str(nozzle)
            if nz not in ("(unchanged)", "1", "2", "3", "4"):
                raise ValueError("nozzle must be 1-4")
            if choice < 0 and nz == "(unchanged)":
                g["messagebox"].showinfo("Assign Feeder", "Nothing selected to apply.")
                return
            for r in comps:
                if choice >= 0:
                    g["set_field"](r, 1, ids[choice])
                if nz != "(unchanged)":
                    g["set_field"](r, 2, nz)
            g["refresh_machine_comp_table"](select=[g["machine_item_id"](r) for r in comps])

    def set_placement_fields(self, ref, values):
        """Edit a placement (Feeder ID, Nozzle, Name, Value, Footprint, X, Y, Rotation,
        Skip); a linked program carries the change back into the Pick & Place list."""
        g = self._g
        with self._op():
            comp = self._comp(ref)

            def saved():
                if g["push_comp_to_row"](comp):
                    g["refresh_table"]()
                g["refresh_machine_comp_table"]()
            self._field_editor(comp, g["COMP_COLS"], None, values, COMP_FIELDS, saved)

    def delete_placement(self, ref):
        g = self._g
        with self._op():
            comp = self._comp(ref)
            if not g["messagebox"].askyesno("Delete Component",
                                            "Delete %s?" % g["field_at"](comp, 3)):
                return
            g["machine_model"]["comps"].remove(comp)
            g["refresh_machine_comp_table"]()

    # ================================================================== speed
    def speed_slots(self):
        """The optimiser's slot list: [{id, side, tape, reserved, holds, x, y}]."""
        g = self._g
        with self._lock:
            slots = g["feeder_slots"](include_empty=True)
            g["default_slot_widths"](slots)
            return self._slot_view(slots)

    def _slot_view(self, slots):
        g = self._g
        out = []
        for s in sorted(slots, key=lambda s: (s["side"], s["y"])):
            w = g["slot_widths"].get(s["id"])
            out.append({"id": s["id"], "side": s["side"], "x": s["x"], "y": s["y"],
                        "reserved": s["reserved"], "tape": w,
                        "tape_set_by_hand": s["id"] in g["slot_widths"]
                        and s["id"] not in g["slot_width_inferred"],
                        "holds": (str(g["field_at"](s["row"], 8)) if s["row"] is not None
                                  else "") or "-"})
        return out

    def set_slot_widths(self, widths):
        """Tape width per slot {slot id: 8 | 12 | 16 | 24 | None (any)}; kept (and saved
        with the project) until changed."""
        g = self._g
        with self._op(keep_build=True):
            for sid, w in widths.items():
                if w not in (8, 12, 16, 24, None, "8", "12", "16", "24", "any"):
                    raise ValueError("tape width must be 8, 12, 16, 24 or None")
                g["slot_widths"][str(sid)] = None if w in (None, "any") else int(w)
                g["slot_width_inferred"].discard(str(sid))

    def speed_plan(self, use_doubtful=False):
        """Optimize for Speed -> Compute Plan. Returns travel before / re-ordered / with
        reels moved (mm, longer axis), the per-part suggestion, loading list and notes."""
        g = self._g
        with self._op(keep_build=True) as msgs:
            if not g["machine_is_loaded"]() or not g["machine_model"]["comps"]:
                g["messagebox"].showerror("Optimize for Speed",
                                          "Import a machine file, or build one from Pick & Place, first.")
                return None
            slots = g["feeder_slots"](include_empty=True)
            if not slots:
                g["messagebox"].showerror("Optimize for Speed",
                                          "The machine file has no taught feeder slots to plan with.")
                return None
            g["default_slot_widths"](slots)
            warn = g["suspicious_slots"](slots)
            out = self._speed_compute(slots, warn, use_doubtful)
            out["messages"] = msgs
            return out

    def _speed_compute(self, slots, warn, use_doubtful):
        g = self._g
        fa = g["field_at"]
        parts = g["current_parts"]()
        pinned = set(g["machine_model"].get("stage1") or [])
        if pinned:
            parts = [p for p in parts if p["key"] in pinned or p["tape"] != 8]
        keys = {p["key"] for p in parts}
        foreign = {s["id"] for s in slots if s["row"] is not None
                   and str(fa(s["row"], 8)).strip()
                   and not s["reserved"]
                   and g["part_match_key"](fa(s["row"], 8)) not in keys}
        doubtful = {sid for pr in warn for sid in pr}
        occupied = {p["slot"] for p in parts}
        live = [s for s in slots
                if (use_doubtful or s["id"] not in doubtful or s["id"] in occupied)
                and s["id"] not in foreign]
        slot_xy = {s["id"]: (s["x"], s["y"]) for s in slots}
        side_of = {s["id"]: s["side"] for s in slots}

        layout, unassigned = g["suggest_feeder_layout"](parts, live)
        nozzles_now = {}
        for f in g["machine_feeders"]():
            nz = g["parse_nozzles"](fa(f, 3)) or [1]
            nozzles_now[str(fa(f, 1))] = len(nz)
        nozzles_new = dict(nozzles_now)
        for p in parts:
            if p["key"] in layout:
                nozzles_new[layout[p["key"]]] = len(p["nozzles"])

        before = g["estimate_travel"](g["current_sequence"](slot_xy), slot_xy, nozzles_now)
        resequenced = g["estimate_travel"](
            g["plan_sequence"](parts, {}, slot_xy, side_of), slot_xy, nozzles_now)
        plan = g["plan_sequence"](parts, layout, slot_xy, side_of)
        after = g["estimate_travel"](plan, slot_xy, nozzles_new)
        self._speed = {"parts": parts, "layout": layout, "plan": plan,
                       "plan_same": g["plan_sequence"](parts, {}, slot_xy, side_of)}

        def pct(x):
            return 100.0 * (before - x) / before if before else 0.0
        summary = ("Head travel, as the program stands now   %9.0f mm\n"
                   "Same reels, placements re-ordered        %9.0f mm   %5.1f%% less\n"
                   "Reels moved as suggested, re-ordered     %9.0f mm   %5.1f%% less"
                   % (before, resequenced, pct(resequenced), after, pct(after)))
        table = []
        for p in sorted(parts, key=lambda p: (p["height"], -p["qty"])):
            sid = layout.get(p["key"])
            table.append({"part": "%s / %s" % (p["footprint"], p["value"]),
                          "footprint": p["footprint"], "value": p["value"], "qty": p["qty"],
                          "height": _num(p["height"]), "tape": p["tape"], "now": p["slot"],
                          "suggested": sid,
                          "status": "none" if sid is None else
                                    ("moved" if sid != p["slot"] else "kept")})
        lines = []
        by_slot = {}
        for p in parts:
            if p["key"] in layout:
                by_slot[layout[p["key"]]] = p
        lines.append("LOADING LIST")
        loading = []
        for sid in sorted(by_slot, key=g["_feeder_sort_key"]):
            p = by_slot[sid]
            lines.append("  slot %-3s  %-28s %-16s x%d"
                         % (sid, p["footprint"][:28], p["value"][:16], p["qty"]))
            loading.append({"slot": sid, "footprint": p["footprint"], "value": p["value"],
                            "qty": p["qty"]})
        no_slot = []
        if unassigned:
            lines.append("")
            lines.append("NO SLOT (feed by hand or add a feeder):")
            for p in unassigned:
                why = ("tape width unknown" if p["tape"] is None
                       else "needs %dmm tape" % p["tape"])
                lines.append("  %-28s %-16s x%d  (%s)"
                             % (p["footprint"][:28], p["value"][:16], p["qty"], why))
                no_slot.append({"footprint": p["footprint"], "value": p["value"],
                                "qty": p["qty"], "why": why})
        unstated = sorted({sid for sid in by_slot if g["slot_widths"].get(sid) is None},
                          key=g["_feeder_sort_key"])
        if unstated:
            lines.append("")
            lines.append("Slots with no tape width set, check the reel fits: %s"
                         % ", ".join(unstated))
        return {"travel_before": before, "travel_reordered": resequenced,
                "travel_after": after, "reordered_saving_pct": pct(resequenced),
                "after_saving_pct": pct(after), "summary": summary, "parts": table,
                "loading_list": loading, "no_slot": no_slot, "unstated_slots": unstated,
                "untaught_pairs": [list(p) for p in warn], "notes": "\n".join(lines),
                "slots": self._slot_view(slots), "use_doubtful": bool(use_doubtful)}

    def apply_speed_plan(self, mode="both"):
        """Apply Plan. mode "order" (re-order, reels stay), "feeders" (move reels, order
        stays) or "both". Uses the last speed_plan(); computes one if there is none.
        Returns the parts whose reel changed side (check their angle)."""
        g = self._g
        if mode not in ("order", "feeders", "both"):
            raise ValueError("mode must be 'order', 'feeders' or 'both'")
        if self._speed is None:
            if self.speed_plan() is None:
                return None
        with self._op(keep_build=True) as msgs:
            st = self._speed
            question = {
                "order": "Re-order the placements for speed? The feeders stay as they are.",
                "feeders": "Move the reels to the suggested slots? The placement order stays.",
                "both": "Move the reels to the suggested slots and re-order the placements?",
            }[mode]
            if not g["messagebox"].askyesno(
                    "Apply Plan", question + "\n\nEach part's tuned settings move with its "
                    "reel; each slot keeps its own position, angle and feeder / peeler power."):
                return None
            if mode == "order":
                moved_bank = g["apply_speed_plan"](st["parts"], {}, st["plan_same"])
            else:
                moved_bank = g["apply_speed_plan"](st["parts"], st["layout"], st["plan"],
                                                   reorder=(mode == "both"))
            g["refresh_feeder_table"]()
            g["refresh_machine_comp_table"]()
            self._speed = None
            msg = "Plan applied. Load the reels as in the loading list, then export."
            if moved_bank:
                msg += ("\n\nThese moved to the other side of the machine, where feeders are "
                        "mounted the other way round. The angle now comes from the new slot -- "
                        "check orientation on the first board:\n  " +
                        "\n  ".join("%s / %s" % (p["footprint"], p["value"]) for p in moved_bank))
            g["messagebox"].showinfo("Optimize for Speed", msg)
            return {"changed_side": ["%s / %s" % (p["footprint"], p["value"]) for p in moved_bank],
                    "messages": msgs}

    # ================================================================== parts kit
    def parts_kit(self, boards=1, spare_pct=0.0, top=True, bottom=True):
        """Parts Kit tab: what `boards` boards need of every part (+ spare %), with feeder.
        Returns lines, the summary line and the same-value note, as the tab shows them."""
        g = self._g
        with self._op(keep_build=True):
            self._kit_inputs(boards, spare_pct, top, bottom)
            lines = []
            for e in g["kit_state"]["lines"]:
                d = dict(e)
                d["sides"] = sorted(d["sides"])
                d["des"] = list(d["des"])
                lines.append(d)
            same = g["kit_same_value_groups"](g["kit_state"]["lines"])
            return {"lines": lines, "summary": g["kit_summary"].cget("text"),
                    "note": g["kit_note"].cget("text"),
                    "same_value_groups": [[e["part"] for e in grp] for grp in same],
                    "boards_per_panel": g["boards_per_panel"]()}

    def _kit_inputs(self, boards, spare_pct, top, bottom):
        g = self._g
        g["kit_top_var"].set(bool(top))
        g["kit_bottom_var"].set(bool(bottom))
        g["kit_boards_var"].set(str(boards))
        g["kit_spare_var"].set("%g" % spare_pct if isinstance(spare_pct, float) else str(spare_pct))
        g["refresh_kit"]()

    def export_kit(self, path, boards=1, spare_pct=0.0, top=True, bottom=True):
        """Export Parts List (CSV, UTF-8 with BOM)."""
        g = self._g
        with self._op(keep_build=True):
            self._kit_inputs(boards, spare_pct, top, bottom)
            self._d.save_path = os.fspath(path)
            g["export_kit"]()
            self._d.save_path = ""
            return os.path.exists(os.fspath(path))

    # ================================================================== output
    def write_machine_file(self, path):
        """Export Machine File: the whole program (fiducial feeder and fiducials pinned
        first, as the tool does on export)."""
        g = self._g
        self._fix_board_records()
        with self._op(keep_build=True):
            self._d.save_path = os.fspath(path)
            g["export_machine_table"]()
            self._d.save_path = ""
            if self.machine_fixes and os.path.exists(os.fspath(path)):
                _unquote_file(os.fspath(path))
            return os.path.exists(os.fspath(path))

    def machine_file_text(self):
        """The machine file Export Machine File writes, as text (CRLF line ends)."""
        fd, tmp = tempfile.mkstemp(suffix=".csv", prefix="neosetup-")
        os.close(fd)
        try:
            self.write_machine_file(tmp)
            with open(tmp, "rb") as fh:
                return fh.read().decode("utf-8")
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    def stages(self):
        """plan_stages(): [{feeders: [ids], placements: n, parts: n}] per stage."""
        g = self._g
        with self._lock:
            out = []
            for feeders, comps in g["plan_stages"]():
                out.append({"feeders": [str(g["field_at"](f, 1)) for f in feeders],
                            "feeder_rows": [list(f) for f in feeders],
                            "placements": sum(1 for c in comps if not g["comp_is_fiducial"](c)),
                            "comps": [list(c) for c in comps]})
            return out

    def export_stages(self, base_path):
        """Export Stages: <base>-1.csv, <base>-2.csv ... Returns [{path, feeders, parts}]."""
        g = self._g
        with self._op(keep_build=True) as msgs:
            if not g["machine_model"]["comps"]:
                g["messagebox"].showerror("Export Stages", "There is no machine program to split.")
                return None
            try:
                done = g["export_stages"](os.fspath(base_path))
            except Exception as e:
                g["messagebox"].showerror("Export Stages", "Error writing the stage files:\n\n%s" % e)
                return None
            g["messagebox"].showinfo("Export Stages", "\n".join(
                "%s   %d feeders, %d parts placed" % (os.path.basename(p), nf, nc)
                for p, nf, nc in done))
            return {"files": [{"path": p, "feeders": nf, "parts": nc} for p, nf, nc in done],
                    "messages": msgs}

    def export_pnp_csv(self, path):
        """Export CSV: the Pick & Place list in table order, as the table shows it
        (2-decimal coordinates, T/B layer; numbers re-typed as Tk does)."""
        g = self._g
        with self._op(keep_build=True):
            self._d.save_path = os.fspath(path)
            g["export_csv"]()
            self._d.save_path = ""
            return os.path.exists(os.fspath(path))


def _engine_static(name):
    """A pure function of the tool, from a shared read-only engine (rigid_fit etc.)."""
    global _STATIC
    if _STATIC is None:
        _STATIC = _engine.load(_tkstub.Dialogs())
    return _STATIC[name]


_STATIC = None
