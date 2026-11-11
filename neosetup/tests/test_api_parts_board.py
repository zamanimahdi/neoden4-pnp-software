"""Parts (BOM, heights, names, fiducials, single components) and board operations."""
from _common import GPS, Project, ToolError, check, finish, need, raises

need(GPS)
p = Project()
p.import_pnp(GPS)

# ---------------- BOM and statistics ----------------
bom = p.bom()
st = p.stats()
check("BOM quantities add up to the components", sum(b["qty"] for b in bom) == st["components"] == 80)
check("BOM is busiest first", [b["qty"] for b in bom] == sorted((b["qty"] for b in bom), reverse=True))
check("one BOM line per Part Name", len(bom) == st["boms"] == 38)
check("footprint table counts too", sum(f["qty"] for f in p.footprints()) == 80
      and len(p.footprints()) == st["footprints"])
check("heights table: every footprint, shortest first",
      len(p.heights()) == st["footprints"] and all("place_height_label" in h for h in p.heights()))
ph = {e["part"]: e for e in p.part_heights()}
check("an electrolytic's height comes from its name (D5H11)", "11.00" in ph["47UF"]["height_label"])

# ---------------- names ----------------
r = p.rename_part("47UF", "47UF 16V")
check("rename: every line renamed", r["renamed"] == 5
      and sum(1 for c in p.components() if c["part"] == "47UF 16V") == 5)
check("renaming an unknown part is a KeyError", raises(lambda: p.rename_part("NOPE", "X"), KeyError))
p.rename_part("10K", "1OK")                      # a letter O typed for a zero
groups = p.similar_name_groups()
p.edit_component(next(c["index"] for c in p.components() if c["part"] == "ERJ-3EKF1002V"),
                 part="10K")
groups = p.similar_name_groups()
check("'1OK' and '10K' are found as one part", len(groups) == 1
      and set(groups[0]["spellings"]) == {"1OK", "10K"}, str(groups))
r = p.merge_similar_names()
check("merging renames them onto the commoner spelling", r["renamed"] >= 1
      and not p.similar_name_groups())

# ---------------- heights ----------------
p.set_part_height("47UF 16V", 10.8)
check("a part height typed in shows starred",
      {e["part"]: e for e in p.part_heights()}["47UF 16V"]["height_label"] == "10.80 *")
check("a non-number height is refused", raises(lambda: p.set_part_height("47UF 16V", "abc"), ToolError))
check("a zero height is refused", raises(lambda: p.set_part_height("47UF 16V", 0), ToolError))
p.set_part_height("47UF 16V", None)
check("clearing it goes back to the footprint's", "*" not in
      {e["part"]: e for e in p.part_heights()}["47UF 16V"]["height_label"])
p.set_footprint_height("SOD-123FL", "1.25")
check("a footprint height typed in is 'manual'",
      {f["footprint"]: f for f in p.footprints()}["SOD-123FL"]["source"] == "manual")
check("unknown footprint is a KeyError", raises(lambda: p.set_footprint_height("NOPE", 1), KeyError))
check("press out of range is refused",
      raises(lambda: p.set_place_height_settings(2.0, 120), ToolError))

# ---------------- remove ----------------
n = p.remove_part("47UF 16V")["removed"]
check("remove_part deletes every line of the part", n == 5 and p.stats()["components"] == 75)
n = p.remove_footprint("SOD-123FL")["removed"]
check("remove_footprint deletes every line on it", n >= 1 and
      all(c["footprint"] != "SOD-123FL" for c in p.components()))

# ---------------- fiducial, single components ----------------
p.add_fiducial(3, 4)
f = p.fiducials()
check("add_fiducial: FID first, at the point", len(f) == 1 and f[0]["index"] == 0
      and (f[0]["x"], f[0]["y"]) == (3.0, 4.0))
check("a fiducial is not a component in the statistics", p.stats()["components"] == p.stats()["components"]
      and p.stats()["fiducials"] == 1)
c = p.components()[5]
p.edit_component(c["index"], dx=1.0, dy=-2.0)
c2 = p.components()[5]
check("edit_component moves centre and pads together",
      abs(c2["x"] - c["x"] - 1.0) < 1e-3 and abs(c2["pad_y"] - c["pad_y"] + 2.0) < 1e-3)
p.turn_component(c["index"], delta=90)
check("turn_component turns it", abs((float(p.components()[5]["rotation"]) - float(c["rotation"]) - 90) % 360) < 1e-6)
d = p.components()[6]["designator"]
p.delete_component(6)
check("delete_component removes the row", all(x["designator"] != d for x in p.components()))
order = [c["designator"] for c in p.table_order()]
check("table order puts the fiducial first", order[0] == "FID")
p.set_table_sort("Designator incremental")
order = [c["designator"] for c in p.table_order()]
check("the sort model sets the table order", order[1:] == sorted(order[1:]))
check("an unknown sort model is a ValueError", raises(lambda: p.set_table_sort("Random"), ValueError))

# ---------------- board ----------------
b = Project()
b.import_pnp(GPS)
before = [(c["x"], c["y"], c["rotation"]) for c in b.components()]
pivot = b.rotate_board_quarter(90)
b.rotate_board_quarter(-90)
after = [(c["x"], c["y"], c["rotation"]) for c in b.components()]
check("+90 then -90 gives the board back", all(abs(a[0] - z[0]) < 0.002 and abs(a[1] - z[1]) < 0.002
                                               for a, z in zip(before, after)), str(pivot))
check("flip without a board width is refused", raises(b.flip_x, ToolError))
b.set_board(width=200, height=150)
b.flip_x()
b.flip_x()
after = [(c["x"], c["y"]) for c in b.components()]
check("flip X twice gives the positions back", all(abs(a[0] - z[0]) < 0.002 for a, z in zip(before, after)))
r = b.check_board_size()
check("board size check passes for a board that fits", r["ok"])
b.set_board(width=10, height=10)
r = b.check_board_size()
check("and reports parts outside a small board", not r["ok"] and r["messages"][0]["kind"] == "check")
b.set_board(width="", height="")
check("board side from the layer column", b.board_side()[0] == "top")
b.mirror_board()
check("mirror_board turns the board over", b.board()["mirrored"])
b.add_fiducial(0, 0)
b.apply_offset(10, 20)
check("1-point offset puts the fiducial where asked", (b.fiducials()[0]["x"], b.fiducials()[0]["y"]) == (10.0, 20.0))
b.rotate_board(30, pivot=(10, 20))
check("rotate_board about a pivot keeps the pivot", (b.fiducials()[0]["x"], b.fiducials()[0]["y"]) == (10.0, 20.0))
r0 = float(b.components()[3]["rotation"])
b.set_board_rotation(15)
r1 = float(b.components()[3]["rotation"])
b.set_board_rotation(15)
check("the Rotation field turns the board by the change, once",
      abs((r1 - r0 - 15) % 360) < 0.01 and float(b.components()[3]["rotation"]) == r1)

finish()
