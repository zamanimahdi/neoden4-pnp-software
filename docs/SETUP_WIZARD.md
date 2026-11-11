# Setup wizard back end: `neosetup`

`neosetup` is the KFG Pick & Place Tool (`csv_flip_tool.py`) as a UI-free Python library.
The setup wizard (PySide6) calls it to turn an Altium Pick & Place CSV into the NeoDen
machine program: import, clean-up, heights, board orientation, live alignment, feeders,
placement order, speed, parts kit and export.

The tool itself is vendored **unchanged** in `neosetup/engine/csv_flip_tool.py`.
`neosetup` adds no rules of its own. Every result comes from the tool's own code, and
`tests/test_golden.py` checks that the outputs are byte-identical to the real Tk tool's.

- Python 3.9+, standard library only. It never imports the real `tkinter`. Pillow is not
  needed: it only draws the tool's map.
- One `Project` is one engine: the tool's module globals are loaded fresh into a namespace
  of their own. Projects do not share state.
- It runs inside the machine's web server on Ubuntu with no display. The tkinter stub is put
  into `sys.modules` only while an engine loads. This happens under a process-wide lock, and
  the stub is removed before the call returns.
- Cost: the tool is compiled once per process (about 0.1 s on a desktop PC, a few seconds on
  the Atom). After that, each `Project()` costs milliseconds. Nothing polls and nothing runs
  in the background.

```python
from neosetup import Project, ToolError

p = Project()                                  # tool defaults; never the user's Options
p.import_pnp("/data/jobs/board.csv")
p.import_machine_file("/data/machine/current.csv")     # optional template
p.prepare_build()                              # the Build dialog's table
p.build_machine()
p.speed_plan(); p.apply_speed_plan("both")
p.write_machine_file("/data/jobs/board-neoden.csv")
```

## 1. Conventions

| Thing | How it is referred to |
|---|---|
| Pick & Place component (a row of the list) | `index` into `components()` (stable until rows are added or removed) or the designator string. With a designator, the first row that has it is used. |
| Placement (`comp` row of the program) | its position in `placements()` or its designator |
| Feeder (`stack` row) | its Feeder ID string, e.g. `"7"`. If two rows share an ID, the first one is used. |
| Part | the Part Name exactly as in `bom()` (`"47UF"`); the group key (`part_name_key`) only in `similar_name_groups()` |
| Footprint | the name as in `footprints()` (already cleaned: `"0603"`) |
| Feeder field | a key of `neosetup.FEEDER_FIELDS` (`"place_height"`), the column index (`11`), or the tool's label (`"Place Height (mm)"`) |
| Placement field | a key of `neosetup.COMP_FIELDS` (`"x"`, `"skip"` ...) or its index |

**Messages.** Each message box the tool would show becomes `{"kind", "title", "text"}`:

| `kind` | Meaning |
|---|---|
| `info` | a summary |
| `warning` | something to check |
| `error` | the tool refused the action |
| `check` | the board-coordinate check that runs after every list change |
| `askyesno` | a confirmation question the tool asked. The API answers "yes" for you; the wizard should ask the user before calling. |

Calls that return a dict carry the messages in `"messages"`. Every call also leaves them in
`p.last_messages` and appends them to `p.log`; `p.take_log()` returns and clears that log.

**Errors.**

- `ToolError(title, text, messages)` is raised when the tool showed an error, which means
  it refused the action (normally nothing changed).
- `KeyError` is raised for an unknown component, part, feeder or placement.
- `ValueError` is raised for an argument the UI could not have produced (an unknown sort
  model, a tape width of 10).
- Python errors such as `OSError` pass through.

**State.** All state lives in the engine, exactly as in the tool. `p.engine` is the tool's
namespace (`rows`, `machine_model`, every function). It is an escape hatch, and changes made
through it are not covered by the tests. A `Project` holds a lock for each call: give each
wizard session its own Project.

## 2. Wizard steps → API

### Step 1: Files

| Wizard action | Call | Returns / notes |
|---|---|---|
| Load the Altium P&P CSV | `import_pnp(path)` | `{components, placeable, similar_names, messages}`. Messages: the Import Summary (footprints cleaned, rotations set) and "Check These Heights". Raises `ToolError` if the file is refused, with the board unchanged. Replaces the list. A program built from another board unlinks itself. |
| Open an existing NeoDen machine file | `import_machine_file(path)` | `{feeders, components, messages}`. Becomes the program and the template for Build (taught slots and board/mark rows). Not linked to the list. |
| Gerber | `load_gerber(path)` | Accepts a folder, a `.zip`, a `.rar` (Windows only, see section 6) or any one file of the set. Returns `gerber()`: `{source, offset, match: (matched, total), report, layers: [...], side_mismatches: {listed side: [designators]}}`. |
| | `clear_gerber()`, `gerber()` | |
| Open / save a tool project (.kfg) | `load_project(path)` → bool, `save_project(path)` → bool | This is the tool's own JSON format, and it holds everything. `load_project` discards unsaved work without asking. `project_snapshot()` returns the same data as plain data. `is_dirty()`. |
| Status line | `status()` | `[(text, colour)]`, as the tool's status bar shows them |

### Step 2: Machine options

| Action | Call | Notes |
|---|---|---|
| Show Options | `options()` | `{fields: [{label, key, value}], slots: {id: {field: value}}}`, in the dialog's order |
| Save Options | `set_options(values=None, slots=None, apply_to_program=True)` | `values` maps an option key to text (`"eight_mm_count": "16"`). Wrong numbers raise `ToolError`. Writes to `settings_path` only if you gave one (see below). With `apply_to_program`, the slot profiles go into the open program. |
| Learn slots from a file the machine wrote | `learn_slots(path, apply_to_program=True)` | `{slots: [ids], messages}`. This is the Options dialog's Learn button followed by Save. |
| Raw constants | `settings()`, `apply_settings(d, save=False)`, `load_settings_file(path)` | `d` has the Options-file shape (`current_settings()`) |

Settings are isolated. `Project(settings=None, settings_path=None)` starts from the tool's
built-in defaults. The user's own `%APPDATA%\KFG Pick and Place\settings.json` is never
read or written. To use a settings file, pass `settings_path` (the tool's saves go there) or
call `load_settings_file()`.

### Step 3: Parts (BOM review)

| Action | Call | Returns |
|---|---|---|
| Part list | `bom()` | `[{part, key, qty, height_label, flag, footprints}]`, busiest first. `flag` is `"dupe"` (spelled more than one way), `"noheight"` or `None`. |
| Footprints | `footprints()` | `[{footprint, qty, height_label, unknown_height, mm, source, note}]`, shortest first |
| Heights per footprint | `heights()` | the Component Heights window: `[{footprint, qty, height_label, place_height_label, source, note, mm, unknown}]` |
| Heights per part | `part_heights()` | `[{part, key, footprints, height_label, typed_in, suspect, place_height}]`. `suspect` means an IC on a chip footprint, so its height is not trusted. |
| Statistics | `stats()` | `{components, footprints, boms, spelled_more_than_one_way, fiducials}` |
| Same part spelled two ways | `similar_name_groups()` | `[{key, spellings: {name: n}, components, target}]` |
| Merge them | `merge_similar_names(targets=None, keys=None)` | `{renamed, messages}` |
| Rename a part | `rename_part(old, new)` | `{renamed}` |
| Delete a part / footprint | `remove_part(part)`, `remove_footprint(fp)` | `{removed}` |
| Height for a part | `set_part_height(part, mm)` | `mm=None` clears it. Not a number, or ≤ 0: `ToolError`. |
| Height for a footprint | `set_footprint_height(fp, mm)` | the source becomes `manual` |
| Place-height settings | `set_place_height_settings(surface_mm, press_percent)` | |
| Apply place heights to the feeders | `apply_place_heights(surface_mm=None, press_percent=None)` | `{feeders}` |
| Fiducial line | `add_fiducial(x, y)`, `fiducials()` | |
| One component | `components()`, `edit_component(ref, part=, x=, y=, rotation=, dx=, dy=, drot=, all_same_designator=False)`, `delete_component(ref, all_same_designator=False)`, `turn_component(ref, angle=None, delta=None, all_like=False)` | `turn_component` is the 2D map's turn. With `all_like`, every part with the same Part Name turns by the same delta. |
| List order | `set_table_sort(model)` (one of `neosetup.SORT_MODELS`), `table_order()` | Build uses the table order. `"Auto (shortest parts first)"` sorts by height and routes nearest-neighbour within each height. |

Component dict:
`{index, designator, footprint, x, y, ref_x, ref_y, pad_x, pad_y, layer, rotation, part, fiducial, angle_note}`.
The values are as the tool stores them: coordinates are floats rounded to 0.001,
`rotation` is an int or float, and `angle_note` is the reason the tool flags an angle for
checking (or `None`).

### Step 4: Board orientation

| Action | Call | Notes |
|---|---|---|
| Board size | `set_board(width=None, height=None, keepout=None)`, `board()`, `check_board_size()` → `{ok, messages}` | The flips need the size. |
| Turn the board ±90 / 180 | `rotate_board_quarter(deg)` → pivot | Turns about the board's centre. Fiducials stay. |
| Any angle | `rotate_board(deg, pivot=None)` | Every row, fiducials too, about `pivot` (default 0,0) |
| The tool's Rotation field | `set_board_rotation(angle)` | Turns by the difference from the last value, about 0,0 |
| Flip | `flip_x()`, `flip_y()` | Mirror inside the board width / height. Without a size: `ToolError`. |
| Bottom side | `board_side()` → `("top" \| "bottom", reason)`, `mirror_board()` | Normally the alignment decides when to turn the board over (step 5). |
| One-point (fiducial) offset | `apply_offset(x, y)` | moves the first FID row to x, y |

### Step 5: Alignment (live, with the head camera)

1. `suggest_alignment(how_many=6)` returns pairs, best first:
   `[{a, b, c, baseline, isolation, c_off_line}]`.
   - `a`, `b` and `c` are component dicts. `c` is the suggested third point: the check
     point, or `None`.
   - `isolation` is the distance in mm to the nearest identical part; `None` means the part
     is the only one of its kind.
   - Show these to the operator and jog the camera onto each part.
   - Also available: `identifiability()` → `{index: mm or None}`, and
     `check_point(a, b)` → `(component, off_line_mm)`.
2. `plan_alignment(a, b, measured_a, measured_b, side="auto", c=None, measured_c=None)`
   computes the fit and changes nothing. `side` is `"auto"`, `"top"` or `"bottom"`. It
   returns the tool's plan:
   - `theta`, `scale`, `len_d`, `len_m`
   - `mirror` (the board will be turned over), `side`, `decided_by`, `fit`
     (`"two-point"` / `"three-point"`)
   - with three points: `residuals`, `rms`, `outlier`, `overrides_data`, `check_failed`
   - `candidates: {normal, turned_over}`
   - from the dialog: `report` (its lines), `scale_ok`, `bad` and `warnings`
3. `apply_alignment(...)` (same arguments, plus `use_scale=False`) applies it. It returns
   the plan plus `applied_theta`, `fit_scale` and `check_b`: the designator and the X, Y
   the camera should read on B afterwards.

- Points closer than 10 mm raise `ToolError`.
- A scale outside 0.2 % produces a warning: a point was probably sighted on the wrong part.
- With a third point, the measurement decides whether the board is upside down.
- `Project.rigid_fit(design, measured)` is the tool's least-squares fit.

### Step 6: Machine program (feeders)

| Action | Call | Returns / notes |
|---|---|---|
| Build: show the assignment | `prepare_build(top_only=True)` | `{parts: [{index, footprint, value, qty, feeder, nozzles, pick, place, speed, source, designators}], components, bottom_excluded, has_template, top_only}`. The fiducial goes on 49, 8mm parts busiest-first centre-out in the bank, the rest from 21. The loaded machine file is the template. |
| Build: change a line | `edit_build_part(index, feeder=, nozzles=, pick=, place=, speed=, share_feeder=True)` | the updated table. Nozzles are `"13"` or `[1, 3]`. A feeder that is already used is refused when `share_feeder=False`. |
| Build | `build_machine(top_only=True, keep_board_records=None)` | `{feeders, components, untaught, messages}`. Uses the prepared table (any other change drops it; then a fresh one is made). `keep_board_records` defaults to "a machine file is loaded". The program is then linked: edits to the list follow into it. |
| Take the layout from the machine | `adopt_layout(path)` | `{matched, missing}`. Parts found go on their feeders in the file (stage 1). The rest move clear of them for later stages. |
| Program | `program()` | `{loaded, path, linked, stage1, feeders, placements, board_records}` |
| Feeders | `feeders()` | see below |
| Edit a feeder | `set_feeder_fields(id, {field: value})` | Checked as the tool's editor checks them. Every field the editor shows is written back stripped (as Save does). |
| Add / delete a feeder | `add_feeder(values=None)` → feeder, `delete_feeder(id)` | |
| Group edit | `group_edit(field, value, scope="8mm" \| "all" \| "selected", feeder_ids=None)` | `{changed, value}`. `value` is normalised as the tool does: `"%.2f"` for floats, digits for nozzles, Yes/No. |
| Arrange by the rules | `arrange_feeders()` | `{moved}` |
| Fiducial-feeder rules | `enforce_fiducial_rules()` → changed | Feeder 49, placement skipped, first in both lists. The tool also applies these after build, arrange and adopt, and on every list change. |
| Package heights | `package_library()`, `apply_package(fp, pick=, place=, speed=)` | |
| Board / mark / mirror rows | `board_records()`, `set_board_record(i, {col: text})` | |

Feeder dict: `id, type, nozzles, x, y, angle, footprint_field, part, pick_height,
pick_delay, place_height, place_delay, vacuum, threshold, vision, speed, feed_rhythm,
feeder_power, peeler_power, col21..col26`. These are the strings exactly as they appear in
the file. The dict also has:

- `footprint`, `value`
- `nozzle_set`, e.g. `[1, 3]`
- `fiducial`
- `tag`: one of `fid`, `bank`, `big`, `later`, `empty`, `warn` (the tool's row colours)
- `overlapping`: the pick point sits on another feeder's
- `fields`: the raw 27-field row

### Step 7: Placement order

| Action | Call |
|---|---|
| List | `placements()` → `[{position, feeder, nozzle, designator, value, footprint, x, y, rotation, skip, fiducial, fields}]` |
| Auto sort (heights, footprints grouped, route) | `auto_sort()` |
| Top / Up / Down / Bottom | `move(refs, "top" \| "bottom" \| -1 \| 1)` |
| Drag one onto another | `move_before(ref, target)` |
| Skip | `toggle_skip(refs)` (the tool's button: if any is placed, all are skipped) and `set_skip(refs, skip=True)` |
| Feeder / nozzle | `assign_feeder(refs, feeder_id=None, nozzle=None)` |
| Edit / delete one | `set_placement_fields(ref, {field: value})` (pushed back into the list when linked), `delete_placement(ref)` |

Fiducials always stay first, as the tool keeps them.

### Step 8: Speed

| Action | Call | Returns |
|---|---|---|
| Slots | `speed_slots()` | `[{id, side, x, y, reserved, tape, tape_set_by_hand, holds}]` |
| Tape width of slots | `set_slot_widths({id: 8 \| 12 \| 16 \| 24 \| None})` | Kept, and saved with the project |
| Compute | `speed_plan(use_doubtful=False)` | See the list below the table. |
| Apply | `apply_speed_plan(mode="both" \| "order" \| "feeders")` | `{changed_side: [...]}`: parts whose reel changed side of the machine; check their angle |

`speed_plan()` returns:

- `travel_before`, `travel_reordered`, `travel_after` (mm, longer axis) and the two
  `*_saving_pct`
- `summary`: the dialog's three lines
- `parts`: `[{part, qty, height, tape, now, suggested, status}]`
- `loading_list`, `no_slot`, `unstated_slots`, `untaught_pairs`, `notes`, `slots`

`apply_speed_plan()` uses the last `speed_plan()`, or computes one if there is none.

### Step 9: Parts kit and output

| Action | Call | Returns |
|---|---|---|
| Kit for N boards | `parts_kit(boards=1, spare_pct=0, top=True, bottom=True)` | `{lines: [{part, value, footprint, side, sides, per, total, spare, feeder, des}], summary, note, same_value_groups, boards_per_panel}` |
| Kit CSV | `export_kit(path, boards, spare_pct, top, bottom)` | |
| Machine file | `machine_file_text()` → str, `write_machine_file(path)` | byte-identical to Export Machine File (CRLF line ends) |
| Stages for an 8mm bank | `stages()`, `export_stages(base_path)` | `stages()`: `[{feeders, feeder_rows, placements, comps}]`. `export_stages()`: `{files: [{path, feeders, parts}]}`, writing `<base>-1.csv`, `<base>-2.csv` and so on. |
| P&P CSV in table order | `export_pnp_csv(path)` | |

## 3. What is reimplemented, and how it is verified

The engine runs headless on an in-memory tkinter stub (`neosetup/_tkstub.py`):

- Variables keep their values and fire their traces.
- Entries and Text widgets keep their text.
- The Treeview keeps its items, their order, tags and the selection. It returns values
  re-typed the way Tcl does, so `"0603"` comes back as `603`.
- Each widget knows its parent and children.
- Buttons can be `invoke()`d.

Where the tool reads the screen, the tool's own code still runs. Nothing is copied:

| Screen state the tool reads | How it is supplied |
|---|---|
| Table order (sort model, fiducials first) that Build uses: `pnp_rows_in_table_order` | The stub Treeview that `refresh_table` fills |
| `export_csv` (reads the table) | The same Treeview, Tcl re-typing included (an `0603` footprint is written as `603`, exactly as the tool writes it) |
| Selected part / footprint / row / feeder / placements (`rename_part`, `remove_part`, `remove_footprint`, `move_comps`, `toggle_comp_skip`, `add_feeder`) | The item is selected in the stub tree, then the tool's handler runs |
| Typed fields (board size, offsets, fiducial X / Y, Rename to, kit inputs, board Rotation) | Text in the stub Entry or variable, then the tool's handler runs |

**Dialog logic repeated line for line** in `project.py`. These are closures inside the
tool's dialogs, so they cannot be called directly. They call the same tool functions with
the same checks, messages and order:

- Build Machine: `prepare_build`, `edit_build_part`, `build_machine`
- Optimize for Speed: `speed_plan`, `apply_speed_plan`, `set_slot_widths`
- Options save and Learn: `set_options`, `learn_slots`
- 2-Point Align compute and apply: `plan_alignment`, `apply_alignment`
- Group Edit
- the field editors: `set_feeder_fields`, `set_placement_fields`, `set_board_record`,
  `add_feeder`
- Delete Feeder / Delete Placement
- Assign Feeder
- Arrange Feeders and Take Layout (the button handlers' messages)
- Component Heights: `heights`, `set_footprint_height`, `set_place_height_settings`,
  `apply_place_heights`
- the part-height popup: `set_part_height`
- Similar Names merge
- Package Heights
- the row editor: `edit_component`, `delete_component`
- the Parts Kit inputs
- `load_gerber`: the dialog without its Pillow check
- Export Stages and Export Machine File messages

**Verified by golden equivalence** (`neosetup/tests/test_golden.py`). Four wizard flows run
twice, in separate processes with the same hash seed:

1. on the real Tk tool, driven through its real dialogs (entries typed into, buttons
   invoked), as its own tests drive it;
2. through `Project`.

Every output file must be byte-identical: machine file, stage files, kit, P&P CSV and the
full project file. Every step must show the same messages (kind, title and text), and the
Part Name and Footprint tables must match.

| Flow | Covers | Result |
|---|---|---|
| `gps_full` | GPS P&P with `BOARD-C main 2.csv` as template: rename, part and footprint heights, Auto sort, +90°, Build with two line edits, learn slots, export stages, adopt layout from stage 1, group edit, place heights, speed "both", skip, moves, all exports | identical |
| `gps_scratch` | no template: −90°, board size, flip X twice, add fiducial, Build (all layers), Arrange, Auto sort, speed "order", exports | identical |
| `sup_align` | BOARD-A: similar-name merge, Gerber `.rar` (bottom side), three-point alignment of a board turned over and turned 1.3°, Build with template, speed "feeders", exports | identical |
| `sup_project` | the tool's `.kfg` of BOARD-A re-opened: speed "both", Arrange, exports | identical |

There are 97 golden checks and all pass. The unit tests `test_api_*.py` (146 checks) cover
each API group headless, including refusals and errors. They pass on Python 3.9, 3.12 and
3.13.

The tool's own 20 test files also run headless (`tests/run_tool_tests_headless.py`):

- 17 files pass completely.
- The 9 remaining failures are checks of drawing or window behaviour: map canvas items and
  their colours, a column heading text, the pop-up input grab, the right-click menu. These
  are not logic.
- With `BOARD-A` pointed at the current `_05_07` CSV, the SUP halves of the tool's tests
  give the same results headless as on real Tk. They also fail on real Tk, because that
  revision has 212 components, not 213.

## 4. Engine updates

The tool keeps evolving in its own repository. To take a new version:

```
python neosetup/sync_engine.py ["<path to>\csv_flip_tool.py"]
python neosetup/tests/run_all.py
```

`sync_engine.py` copies the file and writes `engine/SOURCE.txt`: the source path, git
commit, uncommitted-changes flag, SHA-256 and line count. A changed rule shows up as a
golden difference only if the repeated dialog logic changed. A change inside the tool's
functions is picked up automatically. If a dialog in the tool changes, compare it with its
twin in `project.py`. Each twin's docstring names the dialog and button it repeats.

## 5. Running the tests

```
python neosetup/tests/run_all.py              # all; the golden test needs a desktop (real Tk)
python neosetup/tests/run_all.py --no-golden  # API tests only: headless, any OS
python neosetup/tests/test_golden.py gps_full # one golden flow; KEEP_GOLDEN=1 keeps outputs
python neosetup/tests/run_tool_tests_headless.py --real   # the tool's own tests
```

Fixtures come from `KFG_TOOL_DIR` (the tool's folder: the GPS sample and `BOARD-C main 2.csv`)
and `BOARD_A_DIR` (the BOARD-A P&P, Gerber `.rar` and `.kfg`). Tests whose fixtures are
missing are skipped.

## 6. Known gaps and cautions

- **`.rar` Gerbers need Windows.** The tool opens them with Windows' `tar.exe`. On the
  Ubuntu machine, `load_gerber` of a `.rar` raises `ToolError` ("needs Windows' built-in
  tar.exe"). Use the folder or a `.zip`.
- **The 2D map is not exposed.** Everything it does to data is: turn a part, delete parts,
  the row editor. Its drawing is not, and neither is `component_shape()` (pad-1 outlines);
  those are reachable through `p.engine`.
- **`mirror_board()` is a composition.** The tool calls `mirror_board` only inside
  `apply_alignment`. The API version adds a table refresh so that the live link follows.
- **`set_board_rotation(angle)` sets the Rotation field in one go.** Typing into the real
  field turns the board once per keystroke ("9", then "90"). The end angle is the same,
  but the rounding is not bit-identical to typing.
- **Confirmations are answered "yes".** The wizard should ask the user before calling the
  API, using the question texts it gets back in `messages` (kind `askyesno`).
  `load_project` discards unsaved work. `edit_build_part(share_feeder=False)` answers "no".
- **The build table is dropped by any other change.** `prepare_build` / `edit_build_part`
  work like the open Build dialog. Any other mutating call drops the prepared table, and
  `build_machine()` then prepares a fresh one.
- **A part on two feeders.** The GPS file's LEDs are on 28 and 29. The speed optimiser
  treats such a part as one reel, on the first feeder. This is the tool's behaviour, kept.
- **Settings and the environment.** The tool reads `KFG_SETTINGS` while it loads. During
  that moment, under the lock, it is set to `""` and then restored. Its later saves go only
  to `Project.settings_path`.
- **Threads.** One Project serves one session at a time. Each call holds the Project's
  lock.

## Additions for the machine wizard (not in the tool)

| call | what it does |
|---|---|
| `shift_board(dx, dy)` | moves every row and the Gerber underlay (ApplyOffset without the FID reference) |
| `board_bounds()` | the board extent in the current frame: Gerber outline, else all Gerber shapes, else the parts |
| `zero_bottom_left()` | puts 0, 0 on the board's bottom-left corner whatever origin the design used; the wizard does it after loading the P&P file and the Gerber (option, on by default) |
| `gerber_shapes(kinds=("outline","silk","copper"))` | the visible Gerber layers in the current P&P frame (through `board_xform` and the Gerber offset), for the board view: outline green, pads red, silkscreen yellow; zoom with the wheel, pan by dragging |

The alignment step shows the head camera and the Move panel. **Find part centre** runs pad detection (`/api/align/detect` with the Pad alignment settings) at the current position. It takes the pad nearest the crosshair plus the pads within the part size around it, boxes them, and records the box centre as the part's measured position.
