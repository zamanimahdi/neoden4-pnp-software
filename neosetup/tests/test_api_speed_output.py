"""Speed optimiser, parts kit, and the outputs (machine file, stages, P&P CSV)."""
import csv
import os

from _common import GPS, GPS_MACHINE, WORK, Project, ToolError, check, finish, need, raises

need(GPS, GPS_MACHINE)

# ---------------- speed ----------------
e = raises(lambda: Project().speed_plan(), ToolError)
check("speed plan without a program is refused", e is not None)

p = Project()
p.import_machine_file(GPS_MACHINE)
slots = p.speed_slots()
check("slots: the right bank is 8mm", all(s["tape"] == 8 for s in slots if s["side"] == "RIGHT"))
plan = p.speed_plan()
check("plan: travel before / re-ordered / moved", plan["travel_before"] > 0
      and plan["travel_after"] <= plan["travel_before"]
      and plan["travel_reordered"] <= plan["travel_before"],
      "%.0f %.0f %.0f" % (plan["travel_before"], plan["travel_reordered"], plan["travel_after"]))
check("plan: every placed part listed", sum(x["qty"] for x in plan["parts"]) == 51)
check("plan: untaught slot pairs flagged", ["3", "4"] in plan["untaught_pairs"])
check("plan: the dialog's summary text", plan["summary"].startswith("Head travel"))
before = [f["fields"] for f in p.feeders()]
r = p.apply_speed_plan("order")
# (a part on two feeders -- the GPS file's LEDs on 28 and 29 -- is placed from its first)
check("order only: the feeder rows stay as they are", [f["fields"] for f in p.feeders()] == before)
check("order only: travel now as re-ordered",
      abs(p.speed_plan()["travel_before"] - plan["travel_reordered"]) < 1e-6)
check("an unknown mode is a ValueError", raises(lambda: p.apply_speed_plan("fast"), ValueError))

q = Project()
q.import_machine_file(GPS_MACHINE)
q.set_slot_widths({"23": 8})
check("a tape width set by hand is kept", next(s for s in q.speed_slots() if s["id"] == "23")["tape"] == 8)
plan = q.speed_plan(use_doubtful=True)
r = q.apply_speed_plan("both")
check("both: applied, the plan's loading list is on the feeders",
      r is not None and all(any(f["id"] == l["slot"] for f in q.feeders()) for l in plan["loading_list"]))
check("both: travel as planned", abs(q.speed_plan(use_doubtful=True)["travel_before"]
                                     - plan["travel_after"]) < 1e-6)

# ---------------- kit ----------------
k = Project()
k.import_pnp(GPS)
k.build_machine()
kit = k.parts_kit(boards=5, spare_pct=10)
check("kit: per board adds up to the components", sum(l["per"] for l in kit["lines"]) == 80)
check("kit: totals and spare", all(l["total"] == 5 * l["per"] and l["spare"] >= l["total"]
                                   for l in kit["lines"]))
check("kit: each line has its feeder", all(l["feeder"] for l in kit["lines"]))
check("kit: the tab's summary line", kit["summary"].startswith("39 part lines"), kit["summary"])
top = k.parts_kit(boards=1, top=True, bottom=False)
check("kit: bottom side left out on request", sum(l["per"] for l in top["lines"]) == 80)
path = os.path.join(WORK, "kit.csv")
k.export_kit(path, boards=5, spare_pct=10)
rows = list(csv.reader(open(path, encoding="utf-8-sig")))
check("export_kit: header and one row per line", rows[0][5] == "Total (5 boards)"
      and len(rows) == 1 + len(kit["lines"]))

# ---------------- outputs ----------------
text = k.machine_file_text()
mpath = os.path.join(WORK, "m.csv")
k.write_machine_file(mpath)
check("machine_file_text is exactly what write_machine_file writes",
      open(mpath, "rb").read() == text.encode("utf-8"))
lines = text.splitlines()
check("machine file: #Feeder, stack rows, #SMD, comp rows",
      lines[0].startswith("#Feeder") and any(l.startswith("#SMD") for l in lines)
      and sum(1 for l in lines if l.startswith("comp,")) == 80)
check("CRLF line ends, as csv.writer writes", "\r\n" in text)
st = k.export_stages(os.path.join(WORK, "job.csv"))
check("export_stages writes job-1.csv ...", st["files"][0]["path"].endswith("job-1.csv")
      and all(os.path.exists(f["path"]) for f in st["files"]))
cpath = os.path.join(WORK, "pnp.csv")
k.set_table_sort("Center-X incremental")
k.export_pnp_csv(cpath)
rows = list(csv.reader(open(cpath, encoding="utf-8")))
xs = [float(r[2]) for r in rows[1:] if not r[0].startswith("FID")]
check("export_pnp_csv: table order, 2 decimals", rows[0][2] == "Mid X" and xs == sorted(xs)
      and all(len(r[2].split(".")[1]) == 2 for r in rows[1:]))
check("Tk's re-typing kept: footprint 0603 is written as 603",
      any(r[1] == "603" for r in rows[1:]))
finish()
