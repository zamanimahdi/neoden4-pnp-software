"""Import, project file, settings isolation, engine isolation."""
import json
import os
import sys

from _common import (GPS, GPS_MACHINE, SUP_RAR, WORK, Project, ToolError, check, finish,
                     need, raises)

need(GPS, GPS_MACHINE)

env_before = os.environ.get("KFG_SETTINGS", "<unset>")
p = Project()
check("loading the engine imports no real tkinter", "tkinter" not in sys.modules)
check("and leaves no stub behind", "tkinter.messagebox" not in sys.modules)
check("KFG_SETTINGS is as it was", os.environ.get("KFG_SETTINGS", "<unset>") == env_before)
check("the tool's built-in defaults, not the user's Options",
      p.settings()["eight_mm_count"] == 16 and p.settings()["slots"] == {})

r = p.import_pnp(GPS)
check("GPS: 80 rows read", r["components"] == 80 and r["placeable"] == 80)
check("the import summary comes back as a message",
      any(m["title"] == "Import Summary" for m in r["messages"]))
check("footprints cleaned on the way in", all(c["footprint"] != "SMD-0603C" for c in p.components()))
check("part names upper-cased", all(c["part"] == c["part"].upper() for c in p.components()))

# a refused file leaves the board as it was
bad = os.path.join(WORK, "bad.csv")
with open(bad, "w") as fh:
    fh.write("a,b,c\n1,2,3\n")
e = raises(lambda: p.import_pnp(bad), ToolError)
check("a file without a P&P header is refused with ToolError", e is not None
      and "header" in e.text, getattr(e, "text", ""))
check("and the loaded board is untouched", len(p.components()) == 80)

m = p.import_machine_file(GPS_MACHINE)
check("machine file: feeders and placements", m["feeders"] == 21 and m["components"] == 52)
check("program() reports it as imported, not linked",
      p.program()["loaded"] and not p.program()["linked"])
check("feeders are dicts with the 26-column row kept",
      all(len(f["fields"]) >= 26 for f in p.feeders()) and p.feeders()[0]["fiducial"])

# ---- project file round trip ----
p.set_part_height("47UF", "11.5")
proj = os.path.join(WORK, "a.kfg")
check("save_project writes the project", p.save_project(proj) and os.path.exists(proj))
check("saved: not dirty", not p.is_dirty())
q = Project()
check("load_project reads it", q.load_project(proj))
check("same snapshot after the round trip",
      json.dumps(q.project_snapshot(), sort_keys=True) == json.dumps(p.project_snapshot(), sort_keys=True))
check("a part height typed in survives", q.project_snapshot()["heights"]["parts"].get("47u") == 11.5)
e = raises(lambda: q.load_project(bad), ToolError)
check("a non-project file is refused", e is not None)

# ---- settings stay per Project ----
q.apply_settings({"eight_mm_count": 9})
check("settings are per Project", len(q.engine["EIGHT_MM_BANK"]) == 9
      and len(p.engine["EIGHT_MM_BANK"]) == 16)
sp = os.path.join(WORK, "opts", "settings.json")
s = Project(settings_path=sp)
s.set_options({"feeder_power": "33"})
check("Options save goes only to the settings_path given",
      os.path.exists(sp) and json.load(open(sp))["feeder_power"] == "33")
s2 = Project()
s2.load_settings_file(sp)
check("load_settings_file applies a settings file", s2.settings()["feeder_power"] == "33")
e = raises(lambda: s.set_options({"eight_mm_count": "many"}), ToolError)
check("Options with a bad number are refused", e is not None and e.title == "Options")
e = raises(lambda: s.set_options({"nope": "1"}), ValueError)
check("an unknown option is a ValueError", e is not None)
opts = s.options()
check("options() lists the dialog's fields", [f["key"] for f in opts["fields"]][:3]
      == ["fb16", "fb9", "fb10"])

got = s.learn_slots(GPS_MACHINE)
check("learn_slots reads the stack rows of a machine file", len(got["slots"]) == 21
      and s.settings()["slots"]["21"]["4"] == "26.88")

# ---- Gerber ----
if SUP_RAR and os.path.exists(SUP_RAR) and os.name == "nt":
    from _common import SUP
    g = Project()
    g.import_pnp(SUP)
    r = g.load_gerber(SUP_RAR)
    check("Gerber .rar: layers loaded and lined up", len(r["layers"]) > 5
          and r["match"][0] > 0.9 * r["match"][1], str(r["match"]))
    check("and the side comes from the copper", g.board_side()[0] == "bottom", g.board_side()[1])
    g.clear_gerber()
    check("clear_gerber empties it", g.gerber()["layers"] == [])
else:
    print("SKIP  Gerber .rar (needs the BOARD-A fixture and Windows tar)")
e = raises(lambda: Project().load_gerber(WORK), ToolError)
check("a folder with no Gerber is refused", e is not None and "No Gerber" in e.text)

finish()
