"""Machine program: build, feeders, fiducial rules, adopt layout, placement order."""
import os

from _common import GPS, GPS_MACHINE, WORK, Project, ToolError, check, finish, need, raises

need(GPS, GPS_MACHINE)
p = Project()
p.import_pnp(GPS)
p.add_fiducial(2, 2)

# ---------------- build ----------------
b = p.prepare_build(top_only=True)
check("prepare_build: one entry per distinct part", len(b["parts"]) == 40 and b["components"] == 81)
check("no template: feeders from the rules", not b["has_template"]
      and any(x["feeder"] == "49" for x in b["parts"]))
bank = [x for x in b["parts"] if x["source"].startswith("8mm bank")]
check("the 8mm bank 1-16 is filled, nozzles 1 and 3",
      {x["feeder"] for x in bank} == {str(i) for i in range(1, 17)}
      and all(x["nozzles"] == "13" for x in bank))
e = raises(lambda: p.edit_build_part(1, nozzles=""), ToolError)
check("an assignment with no nozzle is refused", e is not None and e.title == "Invalid Nozzles")
e = raises(lambda: p.edit_build_part(1, place="x"), ToolError)
check("a non-number height is refused", e is not None)
taken = b["parts"][2]["feeder"]
r = p.edit_build_part(1, feeder=taken, share_feeder=False)
check("a feeder already used is refused unless shared", r is None)
r = p.edit_build_part(1, pick="0.5", speed="70")
check("an edit sticks", r["parts"][1]["pick"] == "0.5" and r["parts"][1]["speed"] == "70")
r = p.build_machine()
check("build: feeders and placements", r["components"] == 81 and r["feeders"] >= 40)
prog = p.program()
check("the program is linked to the list", prog["linked"])
check("fiducial first, on 49, skipped", prog["placements"][0]["fiducial"]
      and prog["placements"][0]["feeder"] == "49" and prog["placements"][0]["skip"])
check("the fiducial's feeder row leads", p.feeders()[0]["fiducial"] and p.feeders()[0]["id"] == "49")
f1 = next(f for f in p.feeders() if f["part"].endswith("/" + b["parts"][1]["value"]))
check("the edited pick height and speed reached the feeder",
      f1["pick_height"] == "0.5" and f1["speed"] == "70", "%s %s" % (f1["pick_height"], f1["speed"]))
check("every stack row has 26 fields + the trailing one",
      all(len(f["fields"]) == 27 for f in p.feeders()))

# live link: a rename in the list follows into the program
p.rename_part("47UF", "47UF 16V")
check("live link: renamed part follows into the program",
      sum(1 for c in p.placements() if c["value"] == "47UF 16V") == 5)

# ---------------- feeders ----------------
fid = next(f["id"] for f in p.feeders() if not f["fiducial"])
p.set_feeder_fields(fid, {"place_height": "2.75", "Speed (%)": "55", "feeder_power": "40"})
f = next(f for f in p.feeders() if f["id"] == fid)
check("set_feeder_fields by key and by label", f["place_height"] == "2.75"
      and f["speed"] == "55" and f["feeder_power"] == "40")
check("a non-number is refused by the editor",
      raises(lambda: p.set_feeder_fields(fid, {"pick_height": "high"}), ToolError))
check("an unknown feeder is a KeyError", raises(lambda: p.set_feeder_fields("777", {}), KeyError))
r = p.group_edit("place_delay", "120", "8mm")
check("group edit on the 8mm feeders", r["changed"] > 0
      and all(f["place_delay"] == "120" for f in p.feeders()
              if f["nozzles"] == "13" and not f["fiducial"]))
r = p.group_edit("nozzles", "2", "selected", [fid])
check("group edit on chosen feeders", r["changed"] == 1
      and next(f for f in p.feeders() if f["id"] == fid)["nozzles"] == "2")
check("group edit cannot set a slot position",
      raises(lambda: p.group_edit("x", "1", "all"), KeyError))
n = len(p.feeders())
new = p.add_feeder({"part": "0603/TEST", "speed": "50"})
check("add_feeder: a new row, next free id, edited", len(p.feeders()) == n + 1
      and new["part"] == "0603/TEST" and new["speed"] == "50")
p.delete_feeder(new["id"])
check("delete_feeder removes it", len(p.feeders()) == n)
pk = p.package_library()
check("package library lists the footprints", any(x["footprint"] == "0603" for x in pk))
r = p.apply_package("0603", place="2.40")
check("apply_package sets every 0603 feeder", r["feeders"] > 0 and all(
    f["place_height"] == "2.40" for f in p.feeders() if f["footprint"] == "0603"))
r = p.apply_place_heights()
check("apply_place_heights puts the place height from the part height back", r["feeders"] > 0)

# fiducial rules: a real part on 49 moves away
real = next(f for f in p.feeders() if not f["fiducial"])
p.set_feeder_fields(real["id"], {"id": "49"})
p.enforce_fiducial_rules()
on49 = [f for f in p.feeders() if f["id"] == "49"]
check("enforce_fiducial_rules: only the fiducial on 49", len(on49) == 1 and on49[0]["fiducial"])

# ---------------- arrange, adopt ----------------
r = p.arrange_feeders()
check("arrange_feeders reports what moved", "moved" in r)
stages = p.stages()
check("stages: the program splits for the 8mm bank", len(stages) >= 1
      and all(s["feeders"][0] == "49" for s in stages))
out = p.export_stages(os.path.join(WORK, "AR"))
s1 = out["files"][0]["path"]
r = p.adopt_layout(s1)
check("adopt_layout from the stage-1 file: parts matched, stage 1 pinned",
      len(r["matched"]) > 10 and p.program()["stage1"])
m = Project()
m.import_pnp(GPS)
m.import_machine_file(GPS_MACHINE)
m.build_machine()
check("with a template, its board / mark rows are kept",
      {r[0] for r in m.board_records()} >= {"pcb", "mark"}, str({r[0] for r in m.board_records()}))
rec = m.board_records()[0]
m.set_board_record(0, {1: "  " + str(rec[1]) + " "})
check("set_board_record writes stripped text", m.board_records()[0][1] == str(rec[1]).strip())

# ---------------- placement order ----------------
o = Project()
o.import_pnp(GPS)
o.build_machine(top_only=False)
o.auto_sort()
pl = o.placements()
check("auto sort keeps placements, fiducials first", len(pl) == 80)
des = [c["designator"] for c in pl]
o.move([des[5]], "top")
check("move to top", o.placements()[0]["designator"] == des[5])
o.move([des[5]], 1)
check("move down one", o.placements()[1]["designator"] == des[5])
o.move_before(des[10], des[0])
check("move_before (drag and drop)", o.placements()[0]["designator"] == des[10])
o.toggle_skip([des[3], des[4]])
check("toggle_skip skips them", all(c["skip"] for c in o.placements() if c["designator"] in des[3:5]))
o.set_skip([des[3]], False)
check("set_skip(False) places one again", not next(c for c in o.placements() if c["designator"] == des[3])["skip"])
fid = o.feeders()[-1]["id"]
o.assign_feeder([des[7]], feeder_id=fid, nozzle=4)
c = next(c for c in o.placements() if c["designator"] == des[7])
check("assign_feeder", c["feeder"] == fid and c["nozzle"] == "4")
check("assign to an unknown feeder is a KeyError", raises(lambda: o.assign_feeder([des[7]], "777"), KeyError))
o.set_placement_fields(des[8], {"x": "12.5"})
check("editing a placement moves the list row too (live link)",
      next(r for r in o.components() if r["designator"] == des[8])["x"] == 12.5)
o.delete_placement(des[9])
check("delete_placement", all(c["designator"] != des[9] for c in o.placements()))
finish()
