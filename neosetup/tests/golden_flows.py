"""Wizard flows run two ways, for the golden-equivalence test:

  real -- the KFG tool with real Tk, driven as its own tests drive it: exec of the tool,
          file dialogs and message boxes overridden, and every dialog used through its
          real widgets (entries typed into, buttons invoked);
  neo  -- the same steps through neosetup.Project.

    python golden_flows.py <real|neo> <flow> <out dir>

Writes the flow's output files (machine file, stages, kit, P&P CSV, project file) and
results.json (per step: the messages shown; plus the Part Name / Footprint tables) into
<out dir>. test_golden.py runs both sides (separate processes, same PYTHONHASHSEED) and
compares the two directories byte for byte.

Fixtures: KFG_TOOL_DIR (the tool's folder: the GPS sample and "BOARD-C main 2.csv"),
BOARD_A_DIR (BOARD-A board: Pick & Place, Gerber .rar, .kfg project).
"""
import glob
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_PARENT = os.path.dirname(os.path.dirname(HERE))
ENGINE = os.path.join(os.path.dirname(HERE), "engine", "csv_flip_tool.py")

TOOL_DIR = os.environ.get("KFG_TOOL_DIR", "")
SUP_DIR = os.environ.get("BOARD_A_DIR",
                         "")
GPS = os.path.join(TOOL_DIR, "Pick Place for BOARD-C Main(VA.1).csv")
GPS_MACHINE = os.path.join(TOOL_DIR, "BOARD-C main 2.csv")


def _first(pattern):
    hits = sorted(glob.glob(os.path.join(SUP_DIR, pattern)))
    return hits[0] if hits else None


SUP = _first("Pick Place for BOARD-A*.csv")
SUP_RAR = _first("*.rar")
SUP_KFG = _first("*.kfg")


def truth(turned_over, theta, tx, ty):
    """Where a design point really is on the machine (what the camera would read)."""
    t = math.radians(theta)

    def T(x, y):
        if turned_over:
            x = -x
        return (tx + x * math.cos(t) - y * math.sin(t), ty + x * math.sin(t) + y * math.cos(t))
    return T


FLOWS = {
    # the full wizard on the GPS board, with the GPS machine file as template
    "gps_full": {
        "needs": [GPS, GPS_MACHINE],
        "steps": [
            ("import_pnp", GPS),
            ("tables",),
            ("rename", "47UF", "47UF 16V"),
            ("part_height", "ERJ-3EKF1002V", "0.55"),
            ("footprint_height", "SOD-123FL", "1.25"),
            ("sort", "Auto (shortest parts first)"),
            ("rotate_quarter", 90),
            ("import_machine", GPS_MACHINE),
            ("build", True, True, [(0, {"place": "1.50"}), (3, {"speed": "70"})]),
            ("learn_slots", GPS_MACHINE),
            ("export_stages", "stage"),
            ("adopt", "@stage-1.csv"),
            ("group_edit", "Speed (%)", "60", "8mm"),
            ("place_heights",),
            ("speed", "both"),
            ("toggle_skip", ["C1", "R1"]),
            ("move", ["C2"], "top"),
            ("move", ["R3"], 1),
            ("export_machine", "machine.csv"),
            ("export_stages", "final"),
            ("export_kit", "kit.csv", 5, 10),
            ("export_csv", "pnp.csv"),
            ("save_project", "project.kfg"),
            ("tables",),
        ],
    },
    # no template: feeders from the rules, then arranged, sorted, re-ordered for speed
    "gps_scratch": {
        "needs": [GPS],
        "steps": [
            ("import_pnp", GPS),
            ("rotate_quarter", -90),
            ("set_board", "200", "150"),
            ("flip_x",),
            ("flip_x",),
            ("add_fiducial", "5", "5"),
            ("build", False, False, []),
            ("arrange",),
            ("auto_sort",),
            ("speed", "order"),
            ("export_machine", "machine.csv"),
            ("export_stages", "stage"),
            ("export_kit", "kit.csv", 1, 0),
            ("export_csv", "pnp.csv"),
            ("save_project", "project.kfg"),
            ("tables",),
        ],
    },
    # BOARD-A: Gerber decides the side, three-point alignment of a board turned over
    "sup_align": {
        "needs": [SUP, SUP_RAR, GPS_MACHINE],
        "steps": [
            ("import_pnp", SUP),
            ("tables",),
            ("merge_similar",),
            ("gerber", SUP_RAR),
            ("align", (True, 1.3, 300.0, 120.0)),
            ("import_machine", GPS_MACHINE),
            ("build", True, True, []),
            ("speed", "feeders"),
            ("export_machine", "machine.csv"),
            ("export_stages", "stage"),
            ("export_kit", "kit.csv", 10, 5),
            ("export_csv", "pnp.csv"),
            ("save_project", "project.kfg"),
            ("tables",),
        ],
    },
    # a saved project of the tool, re-opened and taken on
    "sup_project": {
        "needs": [SUP_KFG],
        "steps": [
            ("load_project", SUP_KFG),
            ("tables",),
            ("speed", "both"),
            ("arrange",),
            ("export_machine", "machine.csv"),
            ("export_stages", "stage"),
            ("export_kit", "kit.csv", 3, 0),
            ("export_csv", "pnp.csv"),
            ("save_project", "project.kfg"),
        ],
    },
}


# =====================================================================  real Tk tool
class Real:
    def __init__(self, out):
        self.out = out
        os.environ["KFG_SETTINGS"] = ""
        import tkinter
        from tkinter import messagebox, filedialog
        self.tk = tkinter
        self.msgs = []
        self.open_path = self.save_path = ""

        def rec(kind, ret):
            def f(title="", message="", *a, **k):
                self.msgs.append([kind, str(title), str(message or k.get("message", ""))])
                return ret
            return f
        for n, kind, ret in (("showinfo", "info", "ok"), ("showwarning", "warning", "ok"),
                             ("showerror", "error", "ok"), ("askyesno", "askyesno", True),
                             ("askokcancel", "askokcancel", True),
                             ("askyesnocancel", "askyesnocancel", False)):
            setattr(messagebox, n, rec(kind, ret))
        filedialog.askopenfilename = lambda *a, **k: self.open_path
        filedialog.asksaveasfilename = lambda *a, **k: self.save_path
        src = open(ENGINE, encoding="utf-8").read().replace("root.mainloop()", "pass")
        self.g = {"__name__": "__main__"}
        exec(compile(src, ENGINE, "exec"), self.g)
        self.root = self.g["root"]
        self.root.withdraw()

    # ---- widgets ----
    def popups(self):
        return [w for w in self.root.winfo_children() if w.winfo_class() == "Toplevel"]

    def pop(self):
        return self.popups()[-1]

    def close(self):
        for w in self.popups():
            w.destroy()

    def find(self, w, cls, text=None):
        out = []
        for c in w.winfo_children():
            if c.winfo_class() == cls and (text is None or str(c.cget("text")) == text):
                out.append(c)
            out += self.find(c, cls, text)
        return out

    def button(self, w, text):
        b = self.find(w, "Button", text)
        if not b:
            raise RuntimeError("no button %r" % text)
        b[0].invoke()

    def type_into(self, entry, text):
        entry.delete(0, "end")
        entry.insert(0, str(text))

    def take(self):
        m, self.msgs = self.msgs, []
        return m

    def path(self, name):
        return os.path.join(self.out, name[1:]) if name.startswith("@") else name

    # ---- steps ----
    def import_pnp(self, path):
        self.open_path = path
        self.g["load_csv"]()

    def import_machine(self, path):
        self.open_path = path
        self.g["import_machine_file"]()

    def tables(self):
        g = self.g
        v = [list(g["variant_tree"].item(i, "values")) + [list(g["variant_tree"].item(i, "tags"))]
             for i in g["variant_tree"].get_children()]
        f = [list(g["footprint_tree"].item(i, "values"))
             for i in g["footprint_tree"].get_children()]
        return {"parts": v, "footprints": f}

    def rename(self, old, new):
        g = self.g
        g["variant_tree"].selection_set(g["tv_key"](old))
        self.type_into(g["rename_entry"], new)
        g["rename_part"]()

    def part_height(self, part, mm):
        self.g["ask_part_height"](part)
        pop = self.pop()
        self.type_into(self.find(pop, "Entry")[0], mm)
        self.button(pop, "Set")

    def footprint_height(self, fp, mm):
        g = self.g
        g["open_height_editor"]()
        pop = self.pop()
        self.find(pop, "Treeview")[0].selection_set(g["tv_key"](fp))
        self.type_into(self.find(pop, "Entry")[0], mm)
        self.button(pop, "Set Height")

    def sort(self, model):
        self.g["sort_model"].set(model)
        self.g["refresh_table"]()

    def rotate_quarter(self, d):
        self.g["rotate_board_quarter"](d)

    def set_board(self, w, h):
        self.type_into(self.g["board_width"], w)
        self.type_into(self.g["board_height"], h)

    def flip_x(self):
        self.g["flip_x"]()

    def add_fiducial(self, x, y):
        self.type_into(self.g["fid_x"], x)
        self.type_into(self.g["fid_y"], y)
        self.g["add_FIDUCIAL"]()

    def merge_similar(self):
        self.g["open_similar_names"]()
        if self.popups():
            self.button(self.pop(), "Merge All Groups")

    def gerber(self, path):
        self.open_path = path
        self.g["load_gerber_dialog"]()

    def align(self, params):
        g = self.g
        T = truth(*params)
        baseline, iso, a, b = g["suggest_alignment_pairs"](g["rows"])[0]
        c, _ = g["suggest_check_point"](a, b)
        g["open_two_point_align"]()
        pop = self.pop()
        entries = self.find(pop, "Entry")
        vals = []
        for r in (a, b, c):
            x, y = T(float(r[2]), float(r[3]))
            vals += ["%.4f" % x, "%.4f" % y]
        for e, v in zip(entries, vals):
            self.type_into(e, v)
        self.button(pop, "Compute")
        self.button(pop, "Apply to Board")

    def build(self, top_only, keep, edits):
        g = self.g
        g["open_build_machine_dialog"]()
        pop = self.pop()
        if not top_only:
            self.find(pop, "Checkbutton", "Top layer only")[0].invoke()
        cb = self.find(pop, "Checkbutton",
                       "Keep board / mark / mirror rows from the loaded machine file")[0]
        if str(cb.cget("state")) == "normal" and bool(int(self.root.getvar(str(cb.cget("variable"))))) != keep:
            cb.invoke()
        tree = self.find(pop, "Treeview")[0]
        for index, vals in edits:
            tree.selection_set("p%d" % index)
            self.button(pop, "Edit Selected")
            sub = self.pop()
            e = self.find(sub, "Entry")          # feeder, pick, place, speed
            for k, i in (("feeder", 0), ("pick", 1), ("place", 2), ("speed", 3)):
                if k in vals:
                    self.type_into(e[i], vals[k])
            self.button(sub, "Save")
        self.button(pop, "Build")

    def learn_slots(self, path):
        self.g["open_options"]()
        pop = self.pop()
        self.open_path = path
        self.button(pop, "Learn from machine file...")
        self.button(pop, "Save")

    def adopt(self, path):
        self.open_path = self.path(path)
        self.g["adopt_layout_clicked"]()

    def arrange(self):
        self.g["arrange_feeders_clicked"]()

    def auto_sort(self):
        self.g["auto_sort_machine_table"]()

    def group_edit(self, label, value, scope):
        self.g["open_group_edit"]()
        pop = self.pop()
        cb = [w for w in self._all(pop) if w.winfo_class() == "TCombobox"][0]
        cb.set(label)
        self.type_into(self.find(pop, "Entry")[0], value)
        text = {"8mm": "All 8mm feeders", "all": "All feeders except the fiducial"}[scope]
        self.find(pop, "Radiobutton", text)[0].invoke()
        self.button(pop, "Apply")

    def _all(self, w):
        out = []
        for c in w.winfo_children():
            out.append(c)
            out += self._all(c)
        return out

    def place_heights(self):
        self.g["open_height_editor"]()
        self.button(self.pop(), "Apply Place Heights to Feeders")

    def speed(self, mode):
        self.g["open_speed_optimizer"]()
        pop = self.pop()
        text = {"order": "Order only (feeders stay)", "feeders": "Feeders only (order stays)",
                "both": "Both"}[mode]
        self.find(pop, "Radiobutton", text)[0].invoke()
        self.button(pop, "Apply Plan")

    def _select(self, des):
        g = self.g
        ids = []
        for d in des:
            c = next(c for c in g["machine_model"]["comps"] if str(c[3]).strip() == d)
            ids.append(g["machine_item_id"](c))
        g["machine_comp_tree"].selection_set(ids)

    def toggle_skip(self, des):
        self._select(des)
        self.g["toggle_comp_skip"]()

    def move(self, des, d):
        self._select(des)
        self.g["move_comps"](d)

    def export_machine(self, name):
        self.save_path = os.path.join(self.out, name)
        self.g["export_machine_table"]()

    def export_stages(self, name):
        self.save_path = os.path.join(self.out, name)
        self.g["export_stages_clicked"]()

    def export_kit(self, name, boards, spare):
        g = self.g
        g["kit_boards_var"].set(str(boards))
        g["kit_spare_var"].set(str(spare))
        g["refresh_kit"]()
        self.save_path = os.path.join(self.out, name)
        g["export_kit"]()

    def export_csv(self, name):
        self.save_path = os.path.join(self.out, name)
        self.g["export_csv"]()

    def save_project(self, name):
        self.save_path = os.path.join(self.out, name)
        self.g["save_project"](ask=True)

    def load_project(self, path):
        self.g["open_project"](path)


# =====================================================================  neosetup
class Neo:
    def __init__(self, out):
        sys.path.insert(0, PKG_PARENT)
        from neosetup import Project
        self.out = out
        self.p = Project()

    def take(self):
        return [["error" if m["kind"] == "check" else m["kind"], m["title"], m["text"]]
                for m in self.p.take_log()]

    def close(self):
        pass

    def path(self, name):
        return os.path.join(self.out, name[1:]) if name.startswith("@") else name

    def import_pnp(self, path):
        self.p.import_pnp(path)

    def import_machine(self, path):
        self.p.import_machine_file(path)

    def tables(self):
        v = [[str(e["part"]), str(e["qty"]), str(e["height_label"])]
             + [[e["flag"]] if e["flag"] else []] for e in self.p.bom()]
        f = [[str(e["footprint"]), str(e["qty"]), str(e["height_label"])]
             for e in self.p.footprints()]
        return {"parts": v, "footprints": f}

    def rename(self, old, new):
        self.p.rename_part(old, new)

    def part_height(self, part, mm):
        self.p.set_part_height(part, mm)

    def footprint_height(self, fp, mm):
        self.p.set_footprint_height(fp, mm)

    def sort(self, model):
        self.p.set_table_sort(model)

    def rotate_quarter(self, d):
        self.p.rotate_board_quarter(d)

    def set_board(self, w, h):
        self.p.set_board(width=w, height=h)

    def flip_x(self):
        self.p.flip_x()

    def add_fiducial(self, x, y):
        self.p.add_fiducial(x, y)

    def merge_similar(self):
        self.p.merge_similar_names()

    def gerber(self, path):
        self.p.load_gerber(path)

    def align(self, params):
        T = truth(*params)
        s = self.p.suggest_alignment()[0]
        meas = []
        for r in (s["a"], s["b"], s["c"]):
            x, y = T(float(r["x"]), float(r["y"]))
            meas.append((float("%.4f" % x), float("%.4f" % y)))
        self.p.apply_alignment(s["a"]["index"], s["b"]["index"], meas[0], meas[1], "auto",
                               s["c"]["index"], meas[2])

    def build(self, top_only, keep, edits):
        self.p.prepare_build(top_only)
        for index, vals in edits:
            self.p.edit_build_part(index, **vals)
        self.p.build_machine(keep_board_records=keep)

    def learn_slots(self, path):
        self.p.learn_slots(path)

    def adopt(self, path):
        self.p.adopt_layout(self.path(path))

    def arrange(self):
        self.p.arrange_feeders()

    def auto_sort(self):
        self.p.auto_sort()

    def group_edit(self, label, value, scope):
        self.p.group_edit(label, value, scope)

    def place_heights(self):
        self.p.apply_place_heights()

    def speed(self, mode):
        self.p.speed_plan()
        self.p.apply_speed_plan(mode)

    def toggle_skip(self, des):
        self.p.toggle_skip(des)

    def move(self, des, d):
        self.p.move(des, d)

    def export_machine(self, name):
        self.p.write_machine_file(os.path.join(self.out, name))

    def export_stages(self, name):
        self.p.export_stages(os.path.join(self.out, name))

    def export_kit(self, name, boards, spare):
        self.p.export_kit(os.path.join(self.out, name), boards, spare)

    def export_csv(self, name):
        self.p.export_pnp_csv(os.path.join(self.out, name))

    def save_project(self, name):
        self.p.save_project(os.path.join(self.out, name))

    def load_project(self, path):
        self.p.load_project(path)


def run(side, flow, out):
    os.makedirs(out, exist_ok=True)
    driver = (Real if side == "real" else Neo)(out)
    driver.take()
    results = {"side": side, "flow": flow, "steps": []}
    for step in FLOWS[flow]["steps"]:
        name, args = step[0], step[1:]
        err = None
        ret = None
        try:
            ret = getattr(driver, name)(*args)
        except Exception as e:                # a ToolError is part of the result
            err = "%s" % (getattr(e, "title", None) or type(e).__name__)
        driver.close()
        entry = {"step": [name] + [a if not isinstance(a, str) or os.sep not in a
                                   else os.path.basename(a) for a in args],
                 "messages": [[k, t, x.replace(out, "<OUT>")] for k, t, x in driver.take()]}
        if ret is not None:
            entry["result"] = ret
        if err and side == "neo":
            entry["neo_raised"] = err
        results["steps"].append(entry)
    with open(os.path.join(out, "results.json"), "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=1, ensure_ascii=False, default=str)
    return results


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] not in ("real", "neo") or sys.argv[2] not in FLOWS:
        raise SystemExit(__doc__)
    run(sys.argv[1], sys.argv[2], sys.argv[3])
