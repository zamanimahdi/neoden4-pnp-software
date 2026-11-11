# NeoDen4: new machine software

A complete replacement for NeoDen4.exe on the NeoDen4 pick-and-place machine (fixed table, no
rail). It runs on the machine's own PC under Ubuntu 24.04 and is used from any browser on the LAN
and from the machine's monitor (kiosk). It does everything the original software did, plus
camera-based setup tools, better vision, pad alignment, an integral vacuum check, a setup wizard
that builds jobs from Gerber + pick-and-place files, and signed updates.

It reads and writes the original software's job files and `Config.ini`, so they stay compatible
with NeoDen4.exe. Not affiliated with or endorsed by NeoDen; NeoDen is the maker's trademark.

```
 Browser on the LAN  ─┐                         Machine PC (Atom D525, Ubuntu 24.04)
 Machine monitor     ─┼── HTTP :80 ──►  neoserver  ── web UI (neoagent/www) + JSON API
   (kiosk: cage +     │                   ├─ neomach   mainboard on COM2 (/dev/ttyS1)
    WebKit)           │                   ├─ cameras   up + head camera, native USB
 SSH (key)          ──┘                   ├─ neojob    the placement run
                                          ├─ neosetup  setup wizard (Gerber + P&P -> job)
                                          ├─ neocalib  calibration
                                          └─ Wi-Fi, files, backups, updates, services, log
```

---

## Use it on your NeoDen4

You can run this software on your own NeoDen4. It replaces only the software on the machine's PC; the
mainboard, cameras and feeders stay as they are, and your job files and `Config.ini` keep working.

1. **Keep the original:** take the machine PC's SSD out and keep it (or copy it whole). Putting it back
   gives you NeoDen4.exe exactly as it was.
2. **Make the disk:** build the Ubuntu image ([Building the disk image](#building-the-disk-image)) and
   write it to a spare 16 GB (or larger) SATA SSD.
3. **Boot it:** fit that SSD, put it first in the BIOS boot order and start the machine. It homes, and
   the monitor shows the mount screen.
4. **Bring your data:** copy your `Config.ini`, `Config/pkg.csv` and the `Proc/*.csv` jobs from the
   original disk's NeoDen4 folder (Machine settings → Files, or FTP) to `/opt/neoden/data`.
5. **Use it:** open `http://neoden.local/` (or the machine's IP) from any PC or phone on the LAN, check
   the setups (Machine settings) and the calibration, then try a job with **Test run** before you
   place parts.

Tested on a NeoDen4 with the fixed table (no rail / conveyor), PC with an Atom D525. Use it at your own
risk: check every new board with a test run first.

## Contents

1. [Use it on your NeoDen4](#use-it-on-your-neoden4)
1. [The machine PC](#the-machine-pc)
2. [Using it](#using-it)
3. [Work: the mount screen](#work-the-mount-screen)
4. [New job: setup wizard and job editor](#new-job-setup-wizard-and-job-editor)
5. [Feeder settings](#feeder-settings)
6. [Machine settings, setups and calibration](#machine-settings-setups-and-calibration)
7. [Manual control and cameras](#manual-control-and-cameras)
8. [How the placement run works](#how-the-placement-run-works)
9. [Vision](#vision)
10. [Vacuum check](#vacuum-check)
11. [Learning, statistics and after-place checks](#learning-statistics-and-after-place-checks)
11. [Safety: homing, STOP, control lock](#safety-homing-stop-control-lock)
12. [System: files, updates, Wi-Fi, kiosk, SSH](#system-files-updates-wi-fi-kiosk-ssh)
13. [Job file format](#job-file-format)
14. [Code layout](#code-layout)
15. [HTTP API](#http-api)
16. [Building the disk image](#building-the-disk-image)
17. [Development and tests](#development-and-tests)
18. [Status](#status)

---

## The machine PC

| | |
|---|---|
| CPU / board | Intel Atom D525, NM10 chipset, legacy BIOS (no UEFI); details in `docs/MACHINE_PC.md` |
| OS | Ubuntu 24.04 (built by `build/image/build.py`), on a 16 GB SSD |
| mainboard link | COM2 = `/dev/ttyS1` (0x2F8, IRQ 3) |
| cameras | up-looking camera (parts on the nozzles) and head camera (board, feeders), USB |
| network | wired Ethernet (DHCP), optional USB Wi-Fi dongle (scan / connect in the UI) |
| address | `http://<machine IP>/` (port 80), also `http://neoden.local` |
| data | `/opt/neoden/data` = the NeoDen data folder (`Config.ini`, `Proc/*.csv` jobs, `Config/pkg.csv`, `Jobs/`) |
| program | `/opt/neoden/app` (this repository) |

**Speed on the Atom** (measured on the machine): every API reply goes out without Nagle's delay (headers and
body in two writes made each call wait ~45 ms for the client's delayed ACK); the mainboard's status (two serial
queries) is shared by all screens for 0.3 s (1 s while a job runs, so the screens' polling does not hold up the
job's commands); `Config.ini` is parsed once per change of the file (each load its own copy); Feeder settings'
pocket overlay keeps the pads file and the job until they change; the Z map's triangles are found with numpy
(36 points: 5.4 s on the Atom before, a few ms now).

At boot:
1. The NeoDen boot screen (plymouth).
2. The `boot.d` scripts.
3. neoserver starts, connects to the mainboard and **homes the machine**. This always happens; it
   cannot be switched off.
4. The kiosk shows the UI on the monitor, always on the mount screen (`#job`) after a start.

## Using it

Open `http://<machine IP>/` in a browser, or use the monitor. There is no login. The top bar has
three sections and the machine state:

- **Work**: the job list and the mount screen.
- **New job**: the setup wizard and the job editor (job file settings).
- **Machine settings**: settings, setups (positions, calibration), manual control, cameras,
  network, files, updates, system.
- **Home** button (homes XY), **Take control / In control** (the control lock), the **System**
  menu (restart the server, reboot / shut down the PC, logs), and the red **STOP**.

**One UI on every screen.** The browsers and the machine's monitor see the same errors:
- every error toast is sent to the server (`POST /api/event`);
- the machine's, the job's and calibration faults are taken from the log;
- every client gets the new ones with its status poll (`/api/mc/status?ev=`) and shows them, marked
  "(another screen)" when they came from elsewhere;
- the **⚠** button in the top bar lists the last errors.

The job's questions are shown on every screen as well.

## Work: the mount screen

The original NeoDen4 run screen, rebuilt on one page with both cameras.

- **Job list**: the jobs in `Proc/` (double-click to mount), with:
  - parts per board (to place / all; parts that are skipped, or whose feeder is skipped, are not
    counted);
  - panels;
  - last used: recorded at every start in `job_usage.json`; older jobs use their resume file's date;
  - the date changed.

  Buttons: New, Edit, Copy, Import, Export, Delete, Mount.
- **Live numbers**:
  - elapsed time (pauses not counted, stops at the end);
  - the real placement rate now (parts per hour over the last 60 s) and the average since the start;
  - placed / total, progress.
- **Panels** (P1, P2 ...): skip No / Yes / Auto (bad-mark check). Clicking a panel row shows Vision
  Align on that panel.
- **Component list**: feeder, nozzle, name, value, footprint, X, Y, rotation. Skipped parts are
  hidden. X / Y can be edited and saved.
- **Vision Align**: the head camera goes to where the selected part really lands.
  - The board marks are found first (once), so board shift and turn are included.
  - The part's Gerber pads are drawn over the picture as filled yellow boxes (mask size), and its
    outline as a green dashed box 0.25 mm outside the pads.
  - Keys: Up / Down = previous / next part, Space = **Set X/Y here**.
  - Click the picture: the head moves so that point comes to the centre.
  - **Set X/Y here** writes the crosshair point back as the part's design X / Y, through the marks.
- **Cameras live**: the head camera is live by default, the up camera when ticked; the choice is kept per
  screen, the live view gives way to the job's own pictures while a job runs and comes back when it
  stops (never takes machine control by itself). A job's end brings the cameras' viewing settings back
  (the mark search left the head camera at an 800 px window).
- **Cameras**: head and up camera side by side, live / grab, a zoom menu on each (the up camera
  opens at 4x). The texts are under the pictures, not on them. In Vision Align the part is tagged on
  the picture as "P1 R40 · footprint · value". During a run each new vision picture is shown with what vision found:
  - one nozzle: the part's outline, its turn, the offset from the nozzle's axis, the axis circle;
  - Joint (4-up): the whole picture at 1x with every part's outline, axis and turn.
- **Buttons**: Start / Pause / Continuous, Step, Stop, Exit. Job settings, Machine settings and
  Manual control open that page and come back to the run. **Continue ▶**.
- **Continue where it stopped**: Start after a Stop asks what to do.
  - Continue: the parts already placed are not placed again.
  - Start again from the first part: for a new board.
  - A finished board always starts from the first part.
- **Questions** from the run (picking failed, mark not found, ...) appear as a card with Yes / No /
  Ignore. When a mark is not found, the head camera goes live with its lamp, and the arrows or a
  click move it onto the mark.
- **The job file changed on another screen** (Feeder settings or the job editor on the machine's monitor
  or a browser) while the mount screen is open: it is read again within a few seconds when no job runs,
  so skipped feeders and parts drop out of the list.
- **Editing during a pause**:
  - Feeder settings and part data saved during a pause are used when the run continues.
  - Marks and panels are used from the next start.
  - Adding, removing or reordering parts needs a stop and a new start.

## New job: setup wizard and job editor

### Setup wizard (`#wizard`, `neosetup`, `docs/SETUP_WIZARD.md`)

Builds a NeoDen4 job from the design files, in nine steps:

1. **Files**: job name, the Altium pick-and-place CSV, the Gerber (.zip), a template job, or a saved
   tool project (.kfg); option: 0, 0 on the board's bottom-left corner.
2. **Options**: the machine options of the tool.
3. **Parts**: the BOM with footprints, heights, flags.
4. **Board**: outline, silkscreen (yellow), zoom.
5. **Alignment**: put the head camera on each of two or three parts, then record it.
   - Ways to move: the Move panel, the arrow keys, click the picture or the table map.
   - "Find part centre" boxes the pads under the camera.
   - The turn, shift, side and mirror are fitted from those parts.
   - Or **Align by board points**: type a board point's design X, Y (a fiducial, a hole, a pad; or
     take a fiducial's from the list) for A, B and an optional check point C, put the camera on it
     and record it (**Centre on the circle** or **Record crosshair**). Same fit as with parts.
   - **Marks (fiducials)** with the camera: after Apply the board's coordinates are the machine's,
     so **Add mark here** centres the camera on the round mark and adds a mark at that point; each
     mark has Go, Update (centre on the circle, move it there) and ✕. They become the program's
     `mark` line (Whole).
   - After an alignment the program's board rows fit the parts: one panel at the first part, no
     extra turn, the marks taken here. A template's `mark` / `mirror` rows were for another place of
     the board and would shift every part.
6. **Feeders**: build the machine program: footprint / value → feeder, nozzles, pick / place, speed.
   - A new feeder (no template feeder to copy): pick height 0.00, pick delay 200 ms, place height
     2.38 mm, place delay 200 ms (Options shows and changes them).
7. **Order**: placement order (auto sort by heights, footprints, route; move up / down; skip).
8. **Speed**.
9. **Output**: writes `Proc/<name>.csv`.
   - A value with a comma in it (`TLP293(GB-TPL,E`) is written with a `;`: the job format splits on
     every comma. Older files with quoted values are read the same way.
   - Optional board zero point (`origin` line).
   - Stages, the P&P export, the parts kit (with spares).
   - **Panelize →**.

### Job editor (`#jobfile`, "Job file settings")

The original Edit screen, extended.

Unsaved changes are kept in the page only: leaving the editor asks to save them, and the Mount
screen says so (with **Save them now**), because it and the run read the saved file.

**PCB info**
- Run mode.
- Marks: Whole = on the panel, Mirror = on each board; Auto / Manual; mark search settings.
- Bad-mark point, board zero point (`origin`), test (dry) run.

**Feeder settings**
- See [Feeder settings](#feeder-settings). A **Table** view shows all feeders in one table.
- **Group edit** (the Table view, also Components and Panels): tick rows (or the header box for all), then **Skip selected** / **Unskip selected**, or pick a column, give it a value and **Apply to selected**. Nothing is kept until **Save**.
- A placement test of one part asks only for that part's feeder height (with a board height map), not every feeder's.

**Components**
- Every field of every part.
- Per-part **Pad align** and **Size check** (Feeder / Yes / No): the part's own setting wins over
  its feeder's.

**Board**: change a finished job.
- **Remap with the camera** (offset, or after turning the board):
  1. One part (offset only), or two or three parts (offset + turn), are proposed far apart.
  2. **Go** takes the camera to where each should be: through the found marks, else through panel 1.
  3. Put the crosshair on its real centre and press **Set**.
     - Any part can be used: type its name in the Part box (all the job's parts are listed), add a
       row with **+ part**, leave one out with ✕.
     - Or put the crosshair on a part you see and press **Which part is under the crosshair?**: the
       8 nearest parts are listed; pick it and **Set with this part**.
     - Or a board point instead of a part: **+ board point**, type its X, Y on the board (a fiducial,
       a hole, a pad); Go / Set as for a part.
     - **◎ Set** centres the camera on the round mark under it first, then sets; **Centre on the
       circle** only centres.
  4. **Apply** fits the shift (and turn) and moves all parts. The marks move too when they were not
     used. An `origin` line keeps the panels' reference.
- **Turn the board** −90 / +90 / 180° about its zero point: parts, rotations and marks. The panels
  stay valid.
- **New part coordinates** from a pick-and-place file:
  - The file is fitted onto the job as a whole (turn, shift, mirror). Parts that really moved are
    left out of the fit.
  - The parts that differ are listed with how far they move; tick and apply.
- **New Gerber + P&P**: rebuild the pad data used by pad alignment and Vision Align.
- Links to Panelize and the wizard.

**Panels**: the panel table, and **Panelize** for a job that holds one board.
- Columns × rows and the board steps X / Y.
- Or teach the left-bottom / left-top / right-top boards with the head camera (a turned panel too).
- The `mirror` lines are made from panel 1, row by row, with NeoDen4's `mirror_create` line.
- The marks can be switched to each board (Mirror).

**Raw file**: the file as text.

**Assign feeders & nozzles**
- Gives each part its feeder (by footprint / value) and an allowed nozzle.
- The machine's feeder table fills only what the job's feeder lines lack (the job wins).
- It also runs at every start.

**Marks (PCB info):** the job's fiducials can be edited, added and deleted, with the head camera live on
the same page (arrow keys jog, a click moves). **Read the marks now** finds the job's marks first, so a new
mark is written in the board's own frame (no shift from where the board lies today). **Centre on the
circle** puts the camera on the centre of the round mark nearest the picture's middle (found again after
each move); **Add as a new mark** does that and adds it (its search size from the circle found); on each
mark's row **Go** (the camera to it), **Update** (centred again, the mark moved there) and **✕**.

**Panels: teach with the head camera** (open on the Panels tab): choose a part, **Go** takes the camera to
it on the left-bottom board; set the crosshair on it, then on the same part of the left-top and right-top
boards (Go = where it should be). The row / column steps and the panel's turn are worked out and shown,
and **Make panels** uses them.

## Feeder settings

NeoDen4's `frmStackEdit`, one feeder at a time, with the head camera.

**Feeder list**
- Right feeders 1–19, left feeders 20–48, special feeders / trays 49–98, each with its value and
  number of parts.
- Choosing a feeder takes the camera to its pick point (lamp on, live picture).
- Free numbers can be chosen and used (new feeder / new tray).
- **Bulk feeders** (B1 … B30, Machine settings → Bulk feeders) are in the list too: their part, live
  camera, scan, direction, pick angle and what to do when a part is not picked (see Bulk feeders).
  Once a part is chosen, the same tools as any feeder follow (7. Nozzle information, 8. Placement
  test): nozzles, Align, Height test, Pick test, Discard, Release, delays, vacuum, vision, size check,
  Second look, part size and Measure from the part, pad alignment, After placing. Their pick point is
  the part of the last scan nearest the middle, at the bulk feeder's pick height + 1 mm.

**Measure from the part** (beside the part size): a part is picked from the feeder, measured on the
bottom camera and put back in its pocket; its L x W are kept in this job's feeder only (not the
library).

**Parts** (in the feeder's form): a drop-down of the board's parts (by value + footprint). Choosing one
moves every part of that kind to this feeder, and the feeder takes its value / footprint. The feeders
are not fixed: this can be changed in the middle of a job (pause, change, Save; the run reads the job
again when it continues).
- A feeder holds one kind: the kinds it had go to their other feeder, else to no feeder (feeder 0, shown
  as "no feeder"; Assign gives them a feeder of their value later). **— empty this feeder —** clears it.
- The list: this feeder's own part first, then the same footprint, then parts without a feeder, then
  the rest.
- A feeder that still holds several kinds (from before) says so with **Keep only <its part>** (its own
  part: the one its value / footprint names). Placement test lists only its own part, each board copy
  by its key (C21, C21#2 …).

**Head camera**: live, with the **tape pockets found in every picture**.
- Green = pocket with a part, orange = empty pocket, blue = sprocket hole. Round things (sprocket
  holes, round pads, vias) are drawn as circles.
- **Over the board** (the job's Gerber pads, `<job>.pads.json`, through the board's marks, else its
  nominal place): no tape pockets are looked for there; the board's Gerber pads are drawn where they
  are (blue), and round things in yellow. This keeps false pockets off the picture.
- **Pitch:** with two or more pockets in the picture, the centre-to-centre distance is shown and
  rounded to the standard pitch (2, 4, 8, 12 … mm), with a button to make it the feed rate.
- Click a pocket: its centre becomes the pick point. Click elsewhere: the head moves there.
- **Centre on pocket**, and **keep centred** (the camera follows the pocket).
- Under the picture, two rows: moving the camera (To the pick point, Centre on pocket, keep centred,
  Auto light), then what to do with the pocket (Set pick point = this pocket, **Feed test**): feed and
  look without scrolling. On a wide screen (over 1300 px) the feeder list and the camera stay in view
  while the form beside them scrolls. The form: basic information, pick position, feeder (feed rate,
  torque, peel), nozzles, placement test.
- **Auto light** (also after Align): the head camera's exposure set from the picture, the bright
  end near 200 grey, so white paper tape does not burn out and black tape is not too dark. For this
  view only; the Cameras page setting comes back after a vision task.
- Arrows jog 0.1 mm (Shift 1 mm).

**Basic information**
- Footprint, value, skip.
- Feeder ID exchange (the parts follow).

**Pick position**
- Pick X / Y with **Align** (camera there) and **Set here**.
- Pick angle, with −180 / −90 / 0 / +90 / +180 buttons.
- **Find the pocket with the camera before every pick**: the pick follows the tape. Empty pockets
  are skipped by feeding again, up to 5 times.

**Tray** (special feeder, not a real feeder)
- Start X / Y = the first part (left-bottom), Right top X / Y = the last part.
- Columns and rows.
- Go to any cell; the cell the next part is taken from.

**Nozzle information**
- Allowed nozzles 1–4, each with:
  - **Align**: that nozzle over the pick point;
  - **Height test**: the nozzle stays down while the button is held;
  - **Pick test**, **Discard** (to the trash box), **Release**.
- Pick height / delay, place height / delay.
- **Vacuum** check, with an automatic threshold (see [Vacuum check](#vacuum-check)).
- **Vision**: No action / Individual (one nozzle per picture) / Joint (all nozzles in one picture)
  / Large component.
- **Size check**, with the part's **L × W** and its height **H** (H also corrects the Joint picture's
  lens perspective):
  - typed in;
  - or taken from the **package library**: a search box with a drop-down list (suggestions as you
    type, arrow keys + Enter); the packages matching the feeder's footprint / value appear as buttons;
  - next to the size, its name in the library (or "own size");
  - **Save to library** stores a size in the machine's library;
  - empty = by the feeder's footprint in `Config/pkg.csv`.

  The library (`pkglib.js`) holds typical body + lead sizes of standard packages (chips
  01005–2512, tantalum, LEDs, SOD / SMA–SMC, SOT, MSOP / SOIC / TSSOP / SSOP, DFN / QFN, LQFP,
  DPAK, crystals). It is merged with the machine's `Config/pkg.csv`, which wins for the same name.
- **Second look** (on by default; off only when the feeder says No): the see, turn, see loop (below)
  also for this stack's Joint (4-up) parts. Parts
  shot one by one always get it (`Job/bSeeTurnSee`).
- Move speed.
- **Pad alignment** for all its parts.

**Feeder information** (tape)
- Feed rate (mm), feed torque, peel strength. The job's values are sent to the feeder with every feed.
- **Feed test**: peel + feed, then a new picture.

**Placement test**
- One part of this feeder (the one nearest the feeder first) is placed where it belongs, exactly as
  the job would: marks, vision, pad alignment.
- Then the head camera shows the placed part, next to the up-camera picture of the pick, to check
  its direction.
- The resume record is not touched.

## Machine settings, setups and calibration

**Settings**
- Tabs, written to `Config.ini` in NeoDen4's byte-exact format.
- Positions, speeds, vision, feeders and peelers (feed load / torque).
- Job options: end beep on / off, error beep, settle check, mark fit (`neoden` / affine / bilinear),
  vision log, place lift.
- Pad alignment, lead vision.

**Setups** (`#setups`)
- Each position and offset taught with the camera, step by step: camera offsets, nozzle positions
  over the up camera, nozzle offsets, trash box, feeder positions, fixed table.
- A **calibration values table** with all the factors.
- **One-button auto-calibration** (`neocalib.autocal`):
  - up-camera scale and angle (3 mm steps, the part seen moving);
  - each nozzle's axis from 0 / 90 / 180 / −90°;
  - the four nozzles at the 4-up point;
  - **Z tilt**: each nozzle is lowered over the camera in 3 steps to a depth set on the page (7.5 mm by
    default; watch it the first time, the camera window's depth is not checked yet) at 0 / 90 / 180 /
    −90°. The hole radius and the fit are logged per depth, and depths where the tip is too blurred
    are left out.
    The hole centre at each depth is corrected for the bigger image as the tip nears the lens. The fit
    (`neocalib.ztilt`) gives how far the tip moves sideways per mm down: a fixed part (a tilted Z axis)
    and a part that turns with the nozzle (a bent nozzle). The job moves the pick and place points by
    that shift at their depths (`Calib/ptZTiltFix<n>`, `Calib/ptZTiltTurn<n>`, `Calib/fZTiltSign`);
  - the proposed values are shown and applied with Apply. Unticked by default:
    - changes within the measurement noise (under 0.07 mm);
    - the Z tilt, because a tilted up camera looks the same as a tilted Z. Check it with a placement
      test first.

    An empty holder (no nozzle, a bore much bigger than a tip's hole) is skipped. The 4-up points
    are fitted from 6 turns, keeping only spots of the hole's size and leaving out the worst one.

**XY axes correction** (`neocalib.XYCorrection`, `<data>/xy_correction.json`): the machine's linear XY
error, measured on a precise board: each fiducial centred by the head camera (twice, from both sides),
fitted against its Gerber position with the board's own rotation taken out. It gives the travel per
true mm along X and Y and the lean of the Y axis (axes not at 90°). Every move is corrected
(`Mainboard.xy_map`); at the reference point (the up camera, nozzle 1) nothing moves, and the
command is kept inside the travel. Loaded when the machine connects; no file = no correction.
After it is put in (or changed), the taught positions (feeders, bulk feeders, nozzle / camera
offsets) are re-taught or calibrated again. First measurement (BOARD-B panel, 8 fiducials): X
-0.62 mm/m, Y -0.84 mm/m, Y axis lean +0.154° (0.27 mm per 100 mm), rest 52 µm rms.

**Checked by placing** (BOARD-B, U12 = QFN-24 4×4 picked off the board and placed again 3 times,
see, turn, see; the body measured against its Gerber pads by the head camera): the results
scattered 5 µm / 1 µm / 0.12° and were off by a fixed +96 / −165 µm, −0.5°. That fixed part
went into the head camera offset (`Position/dPosCamLookDownMouse1` = −10.301, 36.453; after the
XY correction it had to be −10.174, 36.432 anyway); a 4th placement was +6 / −1 µm, −0.19°. The
up camera's scale measured on the empty nozzle (±1.5 mm moves) is 34.06 px/mm in X and Y
(`Vision/fUpPxPerMm`, was 32.14). A board that is not held down moves up to 0.2 mm when the
nozzle presses on it: clamp it, and read the fiducials again before judging a placement.

**Pad alignment** and **Lead vision** pages, for tuning (`docs/PADALIGN.md`).

**Vision log**: saves every part picture with the vision result (for tuning), size-limited.

**This PC's screen** (Settings -> This PC's screen): the machine monitor's mouse pointer speed and page
zoom (`/etc/neoden/kiosk.ini`); Apply writes them and restarts the kiosk. The speed goes through the
mouse's DPI for libinput (docs/KIOSK.md).
The **theme** of the machine's screen is kept there too (`theme = dark / light / auto`): the kiosk tells the
page (`window.KIOSK`), so a reload or a restart keeps it; its ☾ / ☀ button writes it into kiosk.ini.
Other browsers keep their own choice (or follow the system's dark mode).

**Screens** (one stylesheet, `neoagent/www/style.css`; plain CSS, nothing the kiosk's CPU-only
WebKit has to work hard for: no shadows, blur or animations):
- Over 1100 px (the machine's monitor, ~1500 CSS px at zoom 0.85; a laptop): the full layout.
- Up to 1100 px (the 1024 px kiosk, a small laptop): the top bar in two rows, the sections below the
  machine state and buttons, so the page never scrolls sideways.
- Up to 900 px (a tablet): every grid one column (camera above its form), tables scroll inside their
  card.
- Up to 760 px (a phone): form labels above their fields, the wizard's steps and the tabs as one
  scrolling strip, toasts across the bottom; under 480 px the brand gives way and the connection is a
  coloured dot (green = connected).
- Touch screens get larger buttons and boxes; buttons let the page scroll under a finger, only the
  press-and-hold ones (jog, Height test) do not.
- Every page checked at 375, 768, 1024 and 1500 px: nothing wider than the screen.
- UX rules (from the ui-ux-pro-max guideline list, applied by hand): buttons at least 32 px high
  (small ones 28 px, 8 px apart), pressed feedback, disabled buttons with a "not allowed" cursor, the
  hand cursor on every clickable row / label, the Mount screen's editable cells look like inputs (dashed
  border), the selection colour a theme token, long words wrap inside cards, 15 px text on phones, a
  "Loading …" line while a page waits for the server and an error card (not a blank page) when a page
  fails, icon-only buttons labelled for screen readers, error toasts as alerts. On narrow screens
  STOP is first in the top bar; the Mount screen goes one column with its table headings on one line.

**Speed on the machine's own PC** (the kiosk runs there too):
- The UI files are sent gzipped and asked again each time but answered "304 not changed" while they
  are the same (ETag): ~130 kB on the first load instead of ~400 kB, a few kB after.
- A live camera picture at most ~6 frames a second (slower when a frame takes longer), none while the
  page is hidden; the tape pocket / loose part search at most twice a second.

## Bulk feeders

Loose parts in containers on the table (Machine settings -> **Bulk feeders**, up to 30), found and
checked by the cameras (`neojob/bulkfeed.py`, `neoserver/bulk.py`, `neocam/bulk.py`).

**Layout** (top of the page): the containers stand in one **matrix**: columns × rows (at most 30
blocks), a block's inside size and the step from one block to the next (the gap is shown). Each is set
with the head camera: the crosshair on block 1's lower-left and upper-right inside corners, then on the
lower-left corner of the last block of the first row and of the top block of the first column, each
with its **Set = camera** button. A **drawing** shows the table with the bulk feeders now (blue, a click
selects one), the matrix being set (orange dashed) and the camera (red cross; a click on the drawing
moves the camera there). **Apply** makes the bulk feeders 1 … columns × rows, row by row from block 1
(`<data>/bulk_grid.json`); a block keeps its own settings, and loses its last scan only if it moved.
A single block outside the matrix can be added with its own corners.

**A block (bulk feeder):**
- **The package:** taught from one part lying the right way round. The part is photographed from the
  top, picked by the vacuum, photographed from below, and put back.
- **Pin 1:** the pin-1 test on / off.
- **The underside exposure.**
- **The nozzle.**
- **The pick height** (below).

**The page:**
- **Scan:** the area photographed and stitched, the parts found. "EMPTY: refill" when none.
  Each part is drawn as its rectangle (numbered). Parts that touch are told apart: a body is a convex
  rectangle, so a dark area that fills most of its convex hull is one part and is never cut; a less solid
  one (several parts touching) is shrunk until it falls apart, one piece per part. The marking on a body
  is filled first. Two parts lying flush side by side can still look like one; the look before a pick
  then sees that it touches another and leaves it.
- **Click a part** on the scan picture:
  - **Look:** what it is and which way round, drawn on the picture.
  - **Measure height:** the pick height read once on that part (the nozzle down in 0.1 mm steps until
    the pressure falls), kept on the container.
  - **Teach as:** it becomes the package.
- **Pick height** (mm): where the nozzle tip meets the parts; typed in, or read by Measure (or at the
  first pick when empty). Picks go straight to it, with no stepping down.
- **Trial** (`POST /api/bulk/trial?client=&id=`): every part of the last scan looked at, picked, its
  underside photographed, put back; the time of each step and the pictures go to `<data>/bulk_trial/`.

**In the job's Feeder settings** (the bulk feeders B1 … are in the feeder list):
1. **Part:** the board's part in this block (a special feeder, tray type, is made for it).
2. **Camera:** the head camera goes to the block, live, with a green outline around each part it finds
   and their number.
3. **Scan this bulk feeder** (only this block, when it is larger than one picture): the parts found,
   their number, and **Correct** (the operator checked it) or **scan again**.
4. **Which way round the parts lie:**
   - **laid by hand**, all the same way: the Pick angle says how they lie against the footprint; the
     camera measures only each part's small turn, nothing is taught;
   - **head camera** (from above: the pin-1 mark / the leads), needs the package taught;
   - **bottom camera** (from below: the leads / pads), for parts whose top shows nothing: each part's
     turn comes from its underside. **Teach it from one part** picks a part near the middle, photographs
     it from above and below and puts it back.
5. **Pick angle** of the special feeder.
6. **When a part is not picked:** take the next part (3 misses in a row: the operator is asked), try the
   same part once more, ask the operator at once, or skip the component.

**In a job:** for each part:
1. **Choose:** the next part of the last scan is looked at alone, centred under the head camera, twice
   for the most exact centre. It must be there, be the package (body + leads per side), and lie alone;
   its direction comes from the leads / pin 1 / the picture.
2. **Pick:** suction on, straight down to the feeder's pick height + `Job/fBulkPressMm` (1 mm), up; the
   pressure must stay low (the part came up). 3 misses in a row: the operator is asked to check the
   height. No height on the feeder: read once by the pressure sensor and kept.
3. **Underside:** every lead / pad of the taught underside must be there, otherwise the part goes to the
   trash and another is picked. The angle and the centre come from it.
4. **Placing:** the place angle includes how the part lay.
5. **Empty:** no part left → scanned again → still none: the job asks the operator to refill (Yes =
   scan again, Ignore = skip the part, No = stop).

## Manual control and cameras

**Camera viewing settings** (exposure, gain, window, set on the Cameras page) are kept in
`<data>/camera.json` and applied at every start. A vision task (marks, pad alignment, bulk scan …)
sets its own exposure / window; after every such call the cameras go back to the viewing settings, so
the live pictures do not stay dark. **Centre on the circle** (Manual page, Marks, Panels): the head
camera centred on the round mark nearest the picture's middle.

**Manual control**
- Home, clear step loss.
- Jog (1 step / 0.1 / 1 / 10 mm, arrow keys); Go to a camera or nozzle-1 point; Here; speed.
- A **clickable table map**: click anywhere on the table and the head goes there.
- Nozzles (Z, rotate), valves, head lamp (inner / outer / both), up light, feeders, peelers,
  vibration feeder, beeper.

**Cameras**: both cameras.
- Exposure / gain / window / LED as buttons (steppers).
- Coordinates in the picture; click to move.

**Lights**: the head lamp and up light go off after 30 s without a command or a camera picture (not
during a job, calibration or homing). They come back as they were on the next camera picture,
machine command or align call, so a page left open does not show a dark picture.
A head camera picture also turns the head lamp on when it is off (after a restart, or a vision
task), unless the operator switched it off with Head lamp.

## How the placement run works

`neojob.runner.Runner`, following NeoDen4.exe (`JOB_SPEC.md` §2) with these additions.

1. **Start**
   - No homing, unless XY is not referenced or lost steps.
   - Nozzles up, run speed.
   - **Assign** (feeder data + feeders / nozzles).
   - Vision start:
     - empty-nozzle backgrounds;
     - each nozzle's **axis**: the hole found at 6 turns, a circle fitted through them;
     - the **4-up** shot's axes, when Joint is used.
2. **Marks**
   - Each fiducial is searched where it should be, then at 4 points 1.5 mm around.
   - A fiducial is a circle at least 1 mm across (`Job/fMarkMinDiaMm`). The spot must be round
     (roundness ≥ 0.85) and fill ≥ 80% of its enclosing circle, so a square pad (64%) is not taken.
   - A spot further than `Job/fMarkMaxOffMm` (5 mm) from where the mark should be is not taken.
   - The distances between the marks found must match the board's within `Job/fMarkMaxErrMm`
     (0.3 mm) + `Job/fMarkMaxErrPct` (0.25 %) of the distance; the machine's X / Y scale is off
     about 0.2 %. If they don't, the run stops and asks: find them again, use them anyway, or stop.
   - When it is still not found, the operator is asked: bring the camera onto it (jogging allowed),
     search again, or stop.
   - The board transform is fitted (`neojob.transform`: NeoDen / affine / bilinear), for the whole
     panel or per board.
3. **Cycles** of up to 4 parts (one per nozzle, same speed):
   - **Pick**:
     - optional pocket check (head camera, settled picture);
     - nozzle down to 12 − pick height;
     - vacuum, delay, up;
     - peel + feed;
     - vacuum check.
   - **Vision** on the up camera: one nozzle per picture, or one picture for all Joint parts.
     - **Size check**.
     - An **empty nozzle** is picked again, without a trip to the trash box.
     - A part not recognised or of the wrong size goes to the trash box and is picked again. After
       3 tries the operator is asked.
   - **Prepare**:
     - the place point through the marks;
     - **pad alignment** (head camera on the pads, if on);
     - the part's turn and offset from vision; the turn always ends from the same side
       (`Job/fTurnApproachDeg`, 1.5° short first: the rotation's backlash lands the same way);
     - **see, turn, see** (single-part vision, `Job/bSeeTurnSee`; Joint parts with Second look): the
       part, already turned to its place angle, is photographed again; the turn left is turned out and
       it is looked at again, until it reads within `Job/fSeeTurnTolDeg` (0.1°, the mainboard's step)
       or `Job/nSeeTurnLooks` (5) looks. Nothing turns after the last look; the place point comes from
       that look's offset (a part off the nozzle's axis moves when it turns, and it can slip on the
       nozzle: on the BOARD-B board a turn after the last look left a TSSOP20 3 to 4° off).
   - **Place** (the point corrected for the nozzle's Z tilt at the place depth; the pick too):
     1. nozzle down;
     2. **blow on / vacuum off**, place delay;
     3. lift 0.1 mm, blow off;
     4. up.
     This keeps the air from pushing the part.
   - **Faster motion:** the picks of a cycle, and its places from the camera, go in nearest-neighbour
     order (`Job/bCycleRoute`; places only when the parts' heights are within 1 mm). The place point is
     approached from one side (+X, +Y) as NeoDen4 does, for the backlash, but a head already below-left
     of it goes straight there, and the last millimetre runs at `Job/nPlaceApproachSpeed` (40) instead
     of speed 10. The tape is fed right after the pick and waited for only at that feeder's next pick.
   - **Steps lost mid-job:** when the controller's own correction gives up, XY is homed again (the
     nozzles up, the parts stay on), the last target sent again from one side, and the job goes on,
     up to `Job/nRehomeTries` (2) times before the operator is asked.
4. **Pause / Step / Stop**
   - These are checked between steps.
   - When the run continues after a pause, the job file and `Config.ini` are read again.
   - Every cycle writes `<job>.resume.json`, used by "Continue where it stopped".
5. **End**: the head goes home; end beep (if on).

## Vision

`neocam.vision` is NeoDen4's vision ported to OpenCV 4, with fixes.

**Up camera**
- One nozzle: the part against the empty-nozzle background, minimum-area rectangle, offset from the
  nozzle's axis.
- **4-up**: all nozzles in one picture, each from its own axis.
  - At job start the empty nozzles turn together through six angles. Each tip hole runs on a circle,
    and its centre is that nozzle's axis. The scale at the corners is taken from the nozzle spacing.
  - The up camera's lens is not even: about 32.1 px/mm in the middle (`[Vision] fUpPxPerMm`, single
    shots) and about 34.1 px/mm at the 4-up corners (measured on the machine, 2026-11-02).
  - The picture grows 0.87 % per mm nearer the lens (`fUpMagPerMm`, measured with the tips lowered
    at the 4-up corners). A part's underside is its height below the tip, so in the 4-up shot it
    seems pushed outwards (~0.13 mm per mm of height at the corners); this is corrected with the
    package height H from `Config/pkg.csv`. Lowered tips also drift +23…29 µm/mm in machine Y the
    same everywhere: a tilt of the up camera or of the head's Z, not yet told apart.
- Large parts (hull).
- **Lead vision**: the leads matched to the Gerber pads (`neojob.leadvision`).

**Size check**
- Adjustable limits and orientation (`fSizeMin` / `fSizeMax`).
- Sizes from the feeder's L × W (picked from the package library or typed), or `Config/pkg.csv`.

**Head camera**
- Fiducials (auto exposure, diameter range, a circle of at least 1 mm, checked against the board's
  mark distances).
- **Fiducials from the Gerber** (`POST /api/align/automarks`): the pads file lists the round copper
  pads of no part; each is looked for with the head camera where the board puts it, and the found ones
  farthest apart (two, or three spanning the board) are suggested as the job's marks. Marks on a
  panel frame are not in a board's Gerber (the BOARD-A's): then there is nothing to suggest.
  In PCB info → Marks: **Find marks from the Gerber** lists them (found / not found, how far from where
  expected, Go), **Use the suggested** writes the mark line (Whole) with a search size of 0.7 … 1.3 × their
  diameter; Save the job.
- **Pads** for pad alignment (`neojob.padalign`): the Gerber pads matched to what the camera sees,
  rigid fit, limits.
  - Each pad is found by its own Gerber rectangle. A part bigger than one view, or with pads cut at a
    view's edge, is photographed a second time from the corrected place (U16, U6 on the BOARD-A).
  - Reverse-mount LEDs (`[PadAlign] sHoleRefs` / `sHoleFootprints`): the centre is the board hole
    between the pads, found as the circle most rim edges agree on; the pads give the turn.
    Found without being named, too: a part the pads file marks `hole` (a hole of the Gerber zip's
    Excellon drill file between its pads), and any 2-pad part whose pads stand `fAutoHoleGapMm`
    (1.2 mm) apart or more when the camera sees a clear round hole between them (`bAutoHole`,
    a stricter test: rim on both sides, fit within 0.03 mm).
  - Pad spacing is refused only when it is off by `fMaxScaleErr` (7 %) and `fMaxSpacingErrMm`
    (0.1 mm per pad), so small tinned pads still pass.
- **Tape pockets** (`neocam.tape`): pockets, parts, sprocket holes; full or empty.
- The bad mark.

**Settled pictures** (`neocalib.settle`): frames are taken until the picture stops moving, instead
of a fixed delay after a move.

**Machine axes** (`[Job] fAxisScaleX` / `fAxisScaleY` / `fAxisSkewDeg`): the 2-mark fit is done in
true coordinates, so a machine with scaled or skewed axes still places a rigid board right. Measured
from a board scan (`tools/board_scan.py`): X +0.138 %, Y +0.204 %, skew −0.142° (not written to the
machine yet: the BOARD-A job's mark coordinates must be fixed first).

## Vacuum check

`neomach.vacuum`: there is no threshold to set.

- Suction is switched on above the part, so each pick first measures this nozzle's open-air level.
- On the way down, the seal edge is found.
- After lifting, the decision is the **integral** of the seal fraction.
- The threshold is the midpoint between this pick's open and sealed levels.

Errors, each with its own reason: no drop (empty pocket / missed), weak seal, lost on the way up,
clogged nozzle.

Levels per nozzle are learnt from good picks (`vacuum_learn.json` next to `Config.ini`).

## Check feeders and Check board (New job)

**Check feeders** (after the setup): for each feeder of the job (or one, by its row) the head camera goes
to the pick point and finds the pocket (more than 0.15 mm off: **Set the pick point on it**; an empty
pocket: feed on). With "one part on the bottom camera" a part is picked, measured and put back:
- its **size** against the feeder's (or the library's): another size = a **wrong reel?** (load the right
  one, or take the measured size);
- **which way it lies** on the nozzle: with each part's rotation and the Pick angle, will its long side lie
  **along its pads or across them** (the pads are long across a part's long side: 0603, SOT, SOIC, TSSOP;
  QFN / QFP have no such axis)? All across: the Pick angle +90°; some: their rotation (Check board).
  Kept in `<job>.feedercheck.json`.

**Pick cycles (several nozzles at once):** the run picks up to four parts per cycle, one per nozzle.
- **Assign** (at the job start and Assign feeders & nozzles) plans the nozzles: in placement order each
  part gets a nozzle its feeder allows that is not yet used in its cycle, the one the next parts can
  least do without (`Job/bPlanNozzles`). Before, a job made part by part ran one part per cycle.
- The run fills a cycle from up to `Job/nCycleLookahead` (12) parts ahead when the next part's nozzle is
  taken; every part is still placed once (0 = strictly in order, as NeoDen4).
- Feeders of different speeds share a cycle at the slowest of them (`Job/bCycleMixSpeeds`).
- The limit is the feeders' allowed nozzles (Nozzle 1 … 4 ticks on the feeder): a feeder that allows only
  nozzle 1 gives one part per cycle. BOARD-B: 100 cycles -> 74 (20 feeders allow only nozzle 1).

**Set up feeders** (New job → Set up feeders, `neoserver/feedersetup.py`): feeder by feeder the machine
measures what is set by hand, nothing changes until Apply (per row or Apply all), then Save the job:
- the pocket at the pick point (auto exposure; the nearest full pocket centred → the pick point);
- the pitch: pocket to pocket in the picture, snapped to 2 / 4 / 8 / 12 … mm → the feed rate;
- the pick height by the vacuum: the nozzle over the pick point, suction on, down in 0.1 mm steps until
  the pressure falls (at most 13 mm), the part left in its pocket; Pick height = 12 − (depth +
  `Job/fTapePressMm`, 0.2). Also **Measure height** beside Height test on the feeder page;
- one part picked at that height and measured on the bottom camera (size, the packages of that size in
  the library; the picture kept for the Setup view); optionally a Feed test.
- `POST /api/feedsetup/pick_height?client=&job=&stack=[&nozzle=&x=&y=]`, `POST /api/feedsetup/feeder?client=&job=&stack=&steps=pocket,pitch,height,part`.

**Camera matrix** (Machine settings → Camera matrix, `neocalib/cammap.py`, `neoserver/cammap_cal.py`): each
camera's pixels against machine millimetres over the whole picture, not one px/mm (the up camera is
~32.15 px/mm in the middle and ~34.1 at the corners):
- Measured with a grid of head moves, every point approached from the same side: the head camera follows
  a round mark on the board (7 x 7, ±3.2 mm); the up camera follows nozzle 1's empty tip at its tip
  plane, Z 0 (9 x 9, ±12 mm, up light on). A 3rd order map fits scale X / Y, skew and the lens; the page
  shows what one matrix would leave instead.
- With the map on, every picture (the job's vision and the live views) is straightened with
  precomputed `cv2.remap` tables (a few ms a picture) to exactly the set px/mm everywhere, keeping the
  camera's own rotation (the angle settings stay valid). One plane only: the up camera's depth
  magnification (`fUpMagPerMm`, the part's height) stays on top.
- Off after a calibration (`<data>/cammap.json`, "on" per camera): switching it on means measuring
  again what was measured in raw pixels (the 4-up points, `fAngleCamLookUp`, the camera offset).
- `POST /api/cammap/calibrate?client=&cam=0|1[&n=&span=&nozzle=&exp=]`, `POST /api/cammap/on?client=&cam=&on=`,
  `GET /api/cammap/status`.

**Head camera scale per board** (PCB info → Head camera scale): the head camera is not telecentric, so
its px/mm depends on the height of the surface it looks at: ~115.2 on the BOARD-A, 116.8 on the
BOARD-B's top face (U3's pads; 116.6 / 116.7 from a 98-move grid over its fiducial). **Measure on this
board** puts the camera over the part with the longest regular row of pads (or one you name), finds
every pad's centre in the row and fits it against its Gerber position (a footprint whose spacing
alternates on the Gerber grid, 0.660 / 0.635, reads right), and writes a `headscale,<px/mm>,<how>` line.
With it, the job's run and the align calls use that value instead of `Vision/fHeadPxPerMm` (marks, pad
alignment, holes all follow). `POST /api/feedsetup/headscale?client=&job=[&ref=]`.

**Z height map** (PCB info → Z height map, and the wizard's last step). Two parts:
- **The machine's own heights** (Calibrate machine heights; `<data>/zmap.json`, machine coordinates): the
  axes and the table are not flat, so nozzle 1 meets a flat board at a different depth here and there. A
  grid of points (4 × 3 by default) over the job's board, each moved to the nearest bare spot (1.5 mm clear
  of every pad, part and **keep-out** box: the placed side's solder mask openings, which ring every hole,
  and the drill file's holes, the pads file's `keepout`; for an older pads file `gerber=` adds it; without
  a pads file a 5 mm box round each part; a spot with no board under it gives way to the next one 2 mm on,
  up to 4). At each, nozzle 1 (empty) comes down slowly with the suction on until the pressure falls: the
  first point searched from Z 6, the next ones from 0.8 mm above the first touch; coarse in 0.1 mm steps,
  then fine in 0.02 mm steps from 0.6 mm above the coarse touch. Nozzles 2 .. 4 then touch two points far
  apart: each one's difference from nozzle 1. Between the points the height is **linear** (the points cut
  into triangles, each a plane; outside them the nearest triangle's plane carried on); the card draws it in
  3D (drag to turn, the wheel stretches the heights). A calibration: every job uses it (“use when placing”).
- **Each job's board height** (Measure board height; `<job>.zmap.json`, `board_z`): nozzle 1 at 3 bare spots,
  the machine's own shape there taken off. **Automatic** at a job's start when the job has none
  (`Job/bAutoZMap`), then kept: the next boards of the job go straight to placing.

When placing, at the part's point on the table and for its nozzle: with the board height and the **part's
height** (the feeder's part size H, else pkg.csv; `Job/bZMapDepth`), the place depth is where that nozzle
meets the board there, less the part's height, plus `Job/fPlacePressMm` (0.1). Every feeder used needs
that height: the job asks at its start until it is set (Ignore: those feeders' own place height). Without
it, the feeder's place height moved by the machine's shape there. And the board is a **floor**: with its
height no nozzle goes deeper than the board there, less the part's height, plus 0.3 mm, whatever place
height a feeder has (also with “use when placing” off; the run log says when a depth was held).
`POST /api/zmap/calibrate?client=&job=[&nx=4&ny=3&nozzles=1,2,3,4&clear=1.5&gerber=]`, `POST /api/zmap/board?client=&job=`,
`POST /api/zmap/on?client=&on=`, `GET /api/zmap/get?job=`.

**Reach check** (`neojob/reach.py`): every feeder's pick point (a tray's corner cells), for each nozzle its parts use, plus that nozzle's offset, must lie inside the XY travel (0 .. 416.24 × 0 .. 400.05 mm). Saving the job lists the ones that do not (and a job with any of them does not start), so a feeder set where a nozzle cannot get to shows up at the feeder set-up, not halfway through a board. A test run counts the skipped feeders too; a bulk feeder is not checked (it picks in its scanned container). A bulk feeder's form has its own Skip (its special feeder's). With the Z height map's board height, every used feeder also needs its part's height (part size H, else pkg.csv): a job without it does not start, and the feeder list marks each feeder that still needs something with ⚠ (hover: what). `GET /api/job/check?path=` (`problems`, `feeders`: {id: [what]}).

**Camera offset** (Machine settings → Camera offset): parts of one kind placed for real (Placement test
on a copy of the job in `Source/_calib/` with the after-place photo on every part); the marks and the
photo both go through the head camera's offset, so the mean miss is its error: proposed
`Position/dPosCamLookDownMouse1` = now + mean. Apply, run again: the misses must shrink (the sign is
checked by that second run). `POST /api/feedsetup/camoffset_job?client=&job=`.

**Setup view** (New job → Setup view): every part of one board (a panel: its first board) drawn on its
Gerber pads the way it will be placed, to check all the angles at a glance:
- The feeder's part as the bottom camera saw it on the nozzle (nozzle angle 0), turned by rotation −
  Pick angle + the bottom camera's angle, less the part's own skew the vision removes. A part turned 90
  or 180° wrong shows at once: pin 1, the leads, a diode's band against the pads.
- The picture is taken by **Take picture** here (one part picked, photographed, put back), by Measure
  from the part on the feeder page, or by Check feeders with a part on the bottom camera; kept in
  `<job>.parts/<feeder>.png`.
- Each feeder's Pick angle can be turned −90 / +90 / 180° here; the cards redraw; Save the job.
- **This part −90 / +90 / 180°** on a card turns that one designator (all its board copies): a
  footprint drawn another way than the others of its feeder.
- **Go feeder by feeder**: each feeder in turn (or only those without a picture): one part picked
  (Check feeders: the pocket, the part on the bottom camera, its size against the expected one, along
  or across its pads, suggestions with Apply), shown with the cards it goes on; **OK, next** / **Skip**
  save the job, **Again** picks once more after a fix, **Stop**.
- Turned pads (a part at 45°): drawn as themselves, not as their box (the pads file keeps each pad's
  own turned rectangle, `shapes`, from the Gerber's outline of the flash or region).
- `GET /api/align/setupview?job=`, `GET /api/align/partpic?job=&stack=`.
- Check feeders and Measure share the bottom-camera code: bottom light on and settled, head lamp off,
  the empty nozzle as background, only 5 mm around the nozzle.

**Safety and data** (audit of November 2026, `docs/AUDIT-2026-11.md`):
- A STOP, a stopped job or a stopped camera tool: parts still on the nozzles (suction on) go to the
  waste box with the nozzles up, then home; never blown onto the board. Not homed or no waste box set:
  they stay on the nozzles and the log says so.
- Resume refuses a job whose parts or panels changed since the stop (the record keeps a fingerprint):
  start a new board, or undo the change.
- A tray used up asks for a refill before cell 1 is taken again; the trays' next cells are written back
  into the job at the end of a run (the file read again, only the counters change, atomic write).
- The marks of a panel turned 180° are looked for at P + (A − m), where the placement puts them.
- The arrow keys jog only after a click on a camera picture ("arrows jog" shows on it); press-and-hold
  buttons cannot leave a nozzle down; on Manual, Z / End all the way down needs Shift.
- After a stop, Start offers "Continue from there", "New board" or "Cancel" by name.
- The Mount screen's X / Y are shown, not typed in (a touch must not move a part): they change in the job
  editor's Components tab, or with the camera on the part and "Set X/Y here (Space)"; a value set
  that way is highlighted until saved, and Save says which parts and panel skips it writes.

**Marks, faster**: the automatic exposure is found on the first mark and the next ones of the same
lamp and size start from it (their first search picture checks it; exposed again only when the light is
off). One picture after the move, ONE slow (one-sided, backlash-free) move onto the mark, one picture:
a mark within `Job/fMarkReadMm` (0.3 mm) of the centre is read off that picture (scale and camera angle
add ~2 µm there) instead of more centring moves; else one more slow move. NeoDen4 makes three.

**Placement test** (Feeder settings): always a real placement with the feeder's vision, also when the
job is set to Test run (the log says so).

**Check board**: the marks are read, then each part's pads (Gerber, `<job>.pads.json`) by the head camera on
the ticked panels. Per part (the mean over the panels): move it onto its pads, turn it, it would lie across
its pads (from Check feeders: rotation +90°), the pads not read (lamp / exposure), vision tips (fine pitch:
lead vision and pad alignment; 0402 / 0201: pad alignment, second look). Tick, **Apply the ticked** (the
part's X / Y through the marks, its rotation), Save.

**During a job:** a feeder whose parts fail the size check twice in a row: the operator is told plainly
("the part picked is 3.20 x 1.60 mm, this feeder's part 1.60 x 0.80: is the right reel loaded?"): fixed,
pick again / this size is right (taken for the run) / stop.

## Learning, statistics and after-place checks

`neojob/learn.py`. Each can be switched off in Settings → Job.

**Learnt pick point** (`Job/bPickLearn`): the bottom camera measures where every part sits on the nozzle
(its offset from the nozzle's axis). After `Job/nPickLearnSamples` (5) parts of a feeder, 80 % of their
mean offset is added to the feeder's pick point, so the nozzle goes to the middle of the part; it keeps
following the tape. A part further off than `Job/fPickLearnMaxMm` (1 mm) is not learnt (picked badly),
and the point never moves further than that from the taught one. Kept next to the job
(`<job>.picklearn.json`); shown in the feeder's settings (**Learnt pick point**, with **Reset**).

**Statistics** (`Job/bStats`, Work → **Statistics**): every pick (vacuum), vision result, part thrown
away, placement and after-place check is one line in `<data>/stats/<date>.jsonl`. The page adds them up
for today / 7 / 30 / 365 days: parts placed, jobs, working time, parts per hour, and per **nozzle**,
**feeder** and **part**: picks, failed picks, vision rejects, thrown away, placed, checked and failed
after placing, failed %. A nozzle or feeder failing `Job/fStatsWarnPct` (15 %) or more over 20 picks
gets a **warning** (during the job too, in the ⚠ list: "nozzle 2: clean or check it").

**After-place checks** (`Job/bPlaceCheck` switches them all): set per **feeder** (Feeder settings →
**After placing**, for all its parts) or per **part** (Components → **After placing**: Feeder = as its
feeder, or its own: None / Vacuum / Photo / Both):
- **Vacuum:** after placing, the nozzle up, suction on for a moment: a part still on the nozzle seals
  it. Then it is thrown away and another one is placed there.
- **Photo:** the head camera photographs the place point before and after placing; the difference is
  the part. Its centre and turn against the place point and angle: more than `Job/fPlaceCheckMm`
  (0.15 mm) or `Job/fPlaceCheckDeg` (3°) off asks the operator (go on / stop). The pictures (before |
  after, the part drawn) go to `<data>/PlaceCheck/<date>/`. `Job/bPlaceCheckFirstOnly`: the photo on
  the first panel of a run only (first article).

## Safety: homing, STOP, control lock

**Homing**
- After every boot / server start, always.
- The run re-homes only when XY is not referenced.
- A homed → not-homed change re-homes.
- Step loss is recovered (up to 3 tries).
- The speed / force register is written before the home pulse.

**Red STOP** (any client, no control needed)
- Stops the job, auto-calibration and the move in progress.
- Then valves off, nozzles up and at 0°, the head home.

**Control lock**
- One client at a time moves the machine.
- Control passes to another client 10 s after the last command.
- While a job runs, manual commands are refused, except jogging while the job asks for a mark by
  hand.

NeoDen4's motion rules (nozzles up before XY moves, no lowering while XY moves, range checks) are in
`neomach.Mainboard`.

## System: files, updates, Wi-Fi, kiosk, SSH

**Files**: browse / upload / download / edit. Every overwrite is backed up and can be restored.

**Updates**: signed packages (`tools/mkpkg.py`).
- Every file's SHA-256 is checked.
- The old files are backed up and the affected services restarted.
- Everything is rolled back on any error.

**Services**: start / stop / restart (systemd); the log (`/api/log`).

**Wi-Fi** (`neoserver.wifi`): scan, connect, forget.
- Known networks are kept in `/etc/neoden/wifi.json` (mode 0600).
- netplan is written for each USB Wi-Fi dongle found.

**Kiosk** (`neokiosk`, `docs/KIOSK.md`)
- The monitor shows the UI full-screen without a login (cage + WebKit).
- The NeoDen boot screen.
- It reloads when the UI files change.

**SSH**: on by default, key login. Passwords are set at image build (`credentials.txt`, never
committed).

**boot.d**: scripts run at every boot, before neoserver (`boot.d/README.md`).

## Job file format

`Proc/*.csv`, NeoDen4's format (`JOB_SPEC.md`), read and written byte for byte. Lines this software
does not know are kept.

| line | fields |
|---|---|
| `pcb` | run mode (Lock = fixed table), ... |
| `mark` | Whole / Mirror, Auto / Manual, mark X / Y pairs; `markext` per mark: diameters, lamp, exposure |
| `test` | dry run Yes / No |
| `mirror_create` | panel grid: columns, rows, three points, PCB angle |
| `mirror` | one panel: X, Y (where the first part / zero point is), rotation 0 / 180, skip No / Yes / Auto |
| `stack` | feeder: id, type (0 tape, 1 tray), nozzles, X, Y, angle, footprint, value, pick height / delay, place height / delay, vacuum, threshold, vision (0–3), speed; tape: feed mm, feed torque, peel strength, skip, size check, 4 thresholds; tray: columns, rows, right-top X / Y, counters, skip, size check, 4 thresholds |
| `comp` | part: feeder, nozzle, name, value, footprint, X, Y, rotation, skip |

Our own additions (NeoDen4.exe ignores them):
- `origin,x,y,`: the board's zero point that the panel positions refer to.
- `stack`, after the four thresholds: pocket check, part L, part W, pad alignment, second look, part H,
  bulk feeder (container number), after-place check (none, vacuum, photo, both).
- `comp` fields 9 / 10 / 11: pad alignment / size check / after-place check (none, vacuum, photo, both) of
  that part (empty = as its feeder).
- `<job>.csv.picklearn.json`: the pick points learnt per feeder.

Next to a job:
- `<job>.csv.pads.json`: the Gerber pads per part (pad alignment, Vision Align outlines).
  A panel whose file lists every board's parts under the same designators: each copy has its own
  key (`C10`, `C10#2`, `C10#3` … in job order), matched to its P&P row by position.
- `<job>.csv.resume.json`: where a stopped job continues.

## Code layout

| path | what |
|---|---|
| `neoserver/` | the machine server: HTTP + web UI + JSON API (`__main__.py`), machine and cameras (`hw.py`), job service (`job.py`), vision tools for the UI (`align.py`: marks, parts, pads, pockets, P&P compare), setup wizard API (`setup.py`), Wi-Fi (`wifi.py`), files / updates / services / log / settings (`core.py`), rollback |
| `neoagent/www/` | the web UI (plain JavaScript): `app.js` (router, top bar, STOP), `jobrun.js` (mount screen), `jobfile.js` (job editor, Board, Panelize), `stackedit.js` (feeder settings), `pkglib.js` (package library), `bulk.js` (bulk feeders), `wizard.js`, `setups.js` (setups, calibration), `settings.js`, `manual.js`, `camera.js`, `padalign.js`, `visionlog.js`, `network.js`, `boardmap.js`, `ini.js` (Config.ini) |
| `neojob/` | the run: `runner.py`, `jobfile.py`, `transform.py` (marks → machine), `camvision.py` (cameras during the run), `assign.py`, `padalign.py`, `leadvision.py`, `pads.py` (Gerber + P&P → pads), `bulkfeed.py` (bulk feeders), `visionlog.py` |
| `neomach/` | mainboard control (`board.py`), protocol (`proto.py`), serial / TCP link, board simulator (`sim.py`), vacuum analysis (`vacuum.py`) |
| `neocam/` | vision (`vision.py`), tape pockets (`tape.py`), `Config.ini` (`config.py`), USB cameras (`usbcam.py`), camera client / simulator, test app |
| `neocalib/` | auto-calibration (`autocal.py`), nozzle Z tilt (`ztilt.py`), settle detection, feeder / motion / XY-error calibration |
| `neosetup/` | the setup wizard engine (the KFG tool as a library) and its tests |
| `neokiosk/` | the kiosk on the monitor, boot screen, `install.sh` |
| `boot.d/` | boot scripts (mainboard port, Wi-Fi, password) |
| `build/image/` | the Ubuntu disk image builder (QEMU, on Windows) |
| `camserver/`, `serbridge/`, `neoagent/*.c` | the earlier Windows XP versions (camera server, COM bridge, agent), kept for reference |
| `tools/` | update packages (`mkpkg.py`) and helpers: `board_scan.py` (pad alignment of every part, map + CSV), `upcam_cal.py` (up camera scale / angle, nozzle axes and runout), `fourup_test.py` (4-up vs single shots), `live_vision.py`, `export_public.py` (the public copy: HEAD without the boards, BOMs, machine data, local paths and reverse-engineering notes, into a separate repository; stops if a forbidden word is left) |
| `docs/` | `USER_MANUAL_FA.html` (the user manual in Persian), `MACHINE_PC.md`, `SETUP_WIZARD.md`, `CALIBRATION.md`, `PADALIGN.md`, `KIOSK.md`, `NEOAGENT.md` (API), `SERBRIDGE.md` (protocol), `CAMSERVER.md` |

## HTTP API

JSON over HTTP on port 80 (`docs/NEOAGENT.md` has the full list). Commands that move the machine
need the control lock: `client=<id>`, taken with `POST /api/mc/lock`.

| area | endpoints |
|---|---|
| status, log, events | `GET /api/status`, `GET /api/log?since=`, `POST /api/event` (an error for every screen; they come back in `/api/mc/status?ev=`) |
| files | `GET/PUT /api/file?path=`, `GET /api/files?path=`, backups |
| machine | `GET /api/mc/status`, `POST /api/mc/<cmd>` (home, move, rel, z, rotate, raise, valve, led, light, feed, peel, beep, ...), `POST /api/mc/lock` / `unlock` / `cancel`, `POST /api/mc/stop` (red STOP) |
| cameras | `GET /api/cam/grab?cam=&scale=&settle=`, `POST /api/cam/set` (`save=1`: kept as the viewing settings, camera.json; the Cameras page only), `GET /api/cam/pockets?cam=1` |
| job | `GET /api/job/list?path=` (parts, panels, last used), `POST /api/job/start?path=&resume=&only=`, `POST /api/job/pause` / `continue` / `step` / `stop`, `POST /api/job/answer?a=`, `GET /api/job/status`, `GET /api/job/image?cam=`, `GET /api/job/resume?path=` |
| vision tools | `POST /api/align/detect` / `marks` / `part` / `leads` / `pads` / `pocket` / `repnp` / `camcal`, `GET /api/align/parts` / `point` / `design` |
| setup wizard | `/api/setup/...` (`docs/SETUP_WIZARD.md`) |
| calibration | `/api/calib/status` / `start` / `cancel` |
| statistics | `GET /api/stats?days=` (per nozzle / feeder / part, warnings) |
| bulk feeders | `GET /api/bulk/list`, `GET /api/bulk/mosaic?id=`, `GET` / `POST /api/bulk/grid`, `POST /api/bulk/save` / `delete` / `scan` / `look` / `teach` / `measure` / `confirm` / `trial`, `GET /api/cam/bulkparts?cam=1` (parts in the last picture) |
| Wi-Fi | `/api/wifi/status` / `scan` / `connect` / `forget` |
| system | services, updates (signed packages), restart / reboot / shutdown |

## Building the disk image

```
python build/image/build.py          # -> build/image/neoden-ubuntu.img (write it to the SSD)
python build/image/build.py --test   # boot the image in QEMU and check neoserver
```

What it needs:
- QEMU on Windows.
- In `build/image`: `ubuntu-base-24.04.5-base-amd64.tar.gz`, the Alpine ISO, and `site.env` (Wi-Fi).

Passwords are generated once into `credentials.txt`. `site.env` and `credentials.txt` are never
committed.

Write the image with `build/image/write-image.ps1`, then put the SSD first in the BIOS boot order.

To change the program on a running machine without a new image:
1. `PUT /api/file?path=/opt/neoden/app/<file>` (the server backs up the old file), or an update
   package.
2. Restart neoserver (System menu). The machine homes after the restart.

## Development and tests

Everything runs without the machine: there is a board simulator (`sim://`), a camera simulator and
fake camera DLLs.

```
pip install numpy opencv-python pyserial pyusb
python -m neoserver --config <ini with machine=sim:// and a data folder>   # the server with the simulator
python -m pytest -q tests                      # runner, jobs, vision, tape, pads, padalign, lead vision, 4-up,
                                               # vacuum, Config.ini, neomach, neoserver, Wi-Fi, setup wizard, ...
python -m pytest -q neocalib/tests             # calibration
python neosetup/tests/run_all.py               # setup wizard engine
```

Some tests use the original NeoDen4 data in `$NEODEN_DATA` (a copy of the machine's original NeoDen4 data folder)
(`Config.ini`, the BOARD-C main-1 job).

## Status

Running on the machine:
- boot and homing;
- the web UI and kiosk;
- manual control, cameras, Wi-Fi;
- jobs placing parts (the BOARD-A test job);
- Vision Align;
- the feeder settings with pocket finding.

Newer features are tested on the simulator and still to be checked on the machine:
- the remap, the board turn and the P&P compare;
- the placement test;
- the second look and Joint vision;
- the integral vacuum check;
- the mark search, and jogging during a mark question;
- the tray tools.

Machine-specific values (camera scales, the lamp, vacuum levels) come from `Config.ini` and
calibration.
