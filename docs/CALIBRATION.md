# Calibration (`neocalib`)

Backend for the calibration menu. Everything here is measured with the machine itself;
the UI only has to start each procedure, show progress, and save / load the result.
Tests: `python neocalib/tests/test_motion.py`, `python neocalib/tests/test_xy.py` (simulator
with real XY errors and synthetic cameras).

Suggested calibration menu:

| item | what it finds | needs | result file |
|---|---|---|---|
| 1. Camera scale (motion) - head | px/mm X and Y, camera angle, skew | any textured board under the head camera | `CalResult` json |
| 2. Camera scale (motion) - up | the same for the up camera | a nozzle (with or without a part) over the up camera | `CalResult` json |
| 3. Repeatability / backlash | backlash X / Y, spread with direct and one-sided approach | one fiducial under the head camera | report |
| 4. XY error map | machine scale X / Y, axes skew, correction table | a precise grid plate (glass or fiducial PCB) | `ErrorMap` json |
| 5. Board transform | (used in every job) rotation, scale, skew from 1, 2 or 3+ marks | the job's fiducials | - |

| 6. Feeder pick positions | each feeder's real pocket position and tape angle | tape loaded in the feeders, head camera calibrated (item 1) | list of corrections |

Order on a new or rebuilt machine: 1 (head), 2 (up), 3, 4. Item 4 uses the head camera
calibration from item 1 to read the plate. Item 6 after every feeder change (or before a job).

Tests: `neocalib/tests/test_motion.py`, `test_xy.py`, `test_settle.py`, `test_feeder.py`.

## 1, 2. Camera scale from machine motion (`neocalib.motion`)

The machine moves XY by known amounts around the current position (a cross at half and full
step and four diagonals, one-sided slow approach so backlash stays out) and the image shift
is measured by phase correlation (any texture; `method="blob"` for a plain object such as a
nozzle hole). A least-squares fit gives the full 2 x 2 matrix A (image px per machine mm).

```python
from neocalib import calibrate, make_grab, CalResult
res = calibrate(mb, make_grab(cam, "head", mb), "head")      # cam = neocam.CamClient
print(res.summary())       # k_x, k_y, angle, skew, fit rms, drift, orientation check
res.save("cal_head.json"); res = CalResult.load("cal_head.json")
dX, dY = res.img_to_mm(dx_px, dy_px)    # pixel offset -> machine mm, with angle and skew
```

Step: 20 % of the field of view at NeoDen's nominal scale (head ~1.4 mm, up ~5.9 mm).
`orientation_ok` is false if the frame is mirrored or the axes are swapped (wrong orient
function). A view without texture fails with a clear error instead of a wrong result.
In the tests the set scale (110.4 / 111.2 px/mm, 0.6 deg) comes back within 0.2 % and 0.01 deg.

Vision should use `res.img_to_mm()` (or `res.matrix`) instead of NeoDen's constants
34.5 / 112 px/mm and `fAngleCamLookUp`.

## 3. Repeatability and backlash (`neocalib.xyerror.repeatability`)

The head approaches one target from +X, -X, +Y, -Y with direct moves (backlash shows) and
with the one-sided slow approach (NeoDen's moveToSlow), `cycles` times.

```python
from neocalib import repeatability, make_measure, mark_finder
measure = make_measure(make_grab(cam, "head", mb), cal_head, mark_finder(0.8, 2.5))
r = repeatability(mb, measure, target=(x, y), cycles=5)
print(r.summary())   # backlash X / Y, spread direct vs one-sided, sd
```

`measure()` returns the machine move that would centre the feature under the head camera
(None if not found). Test: backlash 40 / 20 um set, 40.5 / 20.5 um found; spread 48.6 um
direct, 5.4 um one-sided.

## 4. XY error map (`neocalib.xyerror.measure_grid`, `ErrorMap`)

A grid plate with nx x ny features at a known pitch. The user puts the head camera over
node (0, 0) (origin) and gives the plate's rough angle; the program visits every node
(serpentine path, predicted position refined as it goes), fits an affine map, and keeps the
remaining non-linear error per node as a correction table (bilinear interpolation).

```python
from neocalib import measure_grid, ErrorMap
em = measure_grid(mb, measure, origin=(x0, y0), pitch=10.0, nx=11, ny=9, angle_deg=0.0)
print(em.summary())          # scale X / Y %, axes skew, rest rms / max
em.save("xy_errmap.json")
mb.xy_map = ErrorMap.load("xy_errmap.json").correct      # every move is corrected from now on
```

Test (scale +0.3 % / -0.2 %, skew 0.05 deg, 30 um pulley runout, 30 um backlash): found
+0.298 % / -0.221 % and 0.049 deg; worst error between nodes 180 -> 21 um, on nodes
168 -> 7 um. A finer pitch reduces the error left between nodes. The plate's accuracy is
the limit (glass ~5 um, PCB ~50 um). Translation and rotation are not part of the map: the
board fiducials take care of them in every job.

## 5. Board transform (`neocalib.board.BoardTransform`)

NeoDen4.exe's two-mark transform applies only rotation and shift (it computes the mark
distance but never uses it).

```python
from neocalib import BoardTransform
bt = BoardTransform(design_marks, measured_marks)          # 1, 2 or 3+ marks
X, Y = bt.apply(x_design, y_design)
part_angle += bt.angle_at(x_design, y_design)
bt.bad_marks(0.05)          # 3+ marks: marks whose residual is too large (misread?)
print(bt.summary())
```

| marks | transform |
|---|---|
| 1 | shift |
| 2 | rotation + shift + scale along the line between the marks (`two_mark_scale="line"`; `"uniform"`; `"none"` = NeoDen) |
| 3+ | full affine (scale X / Y, skew, rotation, shift), least squares, residual per mark |

Test (board rotated 1.2 deg, scaled +0.3 % / -0.2 %, slight skew; worst part error): NeoDen
206 um, two marks with line scale 145 um, four marks affine 0 um.

## Simulator

`neomach.sim` has an XY error model (off by default): `err_scale`, `err_skew_deg`,
`err_periodic` (amplitude, period), `backlash`; `sim.xy_true_mm()` is where the head really is.

## Camera settling (`neocalib.settle`) - for every vision picture

NeoDen4.exe waits a fixed 250 ms after each move. `grab_settled(grab)` grabs frames until
the shift between two frames (phase correlation on a 4x reduced image: CPU-light) stays
below `tol_px` (0.3 px) for `stable` (2) frame pairs, or `max_s` (1 s) runs out.

```python
from neocalib import grab_settled, settle_profile
r = grab_settled(grab)          # r.frame, r.settled, r.time_s, r.frames, r.history
settle_profile(mb, grab, distances=(1, 5, 20, 80))   # settle time after moves of each length
```

Test: a quiet camera is ready after 3 frames (82 ms at 50 fps, vs 250 ms); an image ringing
4 px (tau 50 ms) is returned after 180 ms with 0.09 px left; endless ringing returns at
`max_s` with `settled = False`. `method="diff"` (mean grey difference) for scenes with little
texture. Caution: if the frame rate is a multiple of the ringing frequency, consecutive frames
can look alike while the head still moves (stroboscope); `stable >= 2` and `min_s` reduce it.

## 6. Feeder pick-location calibration (`neocalib.feeder`)

The head camera looks at the feeder's stored pick position, finds the tape's sprocket holes
(round blobs of 1.5 mm, either polarity, blobs cut by the image border ignored), keeps the
row at 4 mm pitch (RANSAC), fits the line (= tape angle) and computes the pocket from the
EIA-481 geometry: 2.0 mm along from a hole, 3.5 / 5.5 / 7.5 / 11.5 mm across for 8 / 12 /
16 / 24 mm tape. The pocket is snapped to the tape's pocket grid nearest the expected
position, so a correction never jumps to the next pocket. If fewer than three holes are seen
(the hole row is at the edge of a small window), extra views are taken across and along the
tape.

```python
from neocalib import calibrate_feeder, calibrate_feeders
r = calibrate_feeder(mb, grab_head, cal_head, expected=(x, y), cam_offset=(dx, dy),
                     tape_width=8, pocket_pitch=4.0)
r.ok, r.pick, r.dx, r.dy, r.tape_angle_deg, r.pitch_mm, r.summary()
res = calibrate_feeders(mb, grab_head, cal_head, [(name, (x, y), {"tape_width": 12}), ...], cam_offset)
```

Coordinates are NeoDen's stored positions (nozzle-1 frame); `cam_offset` =
`getNozzleOffset(0)` (`dPosCamLookDownMouse1`). Corrections above `max_dist` (2 mm) are
refused (wrong feeder, no tape). The measured hole pitch (should be 4.000 mm) also checks the
head camera calibration. Test (synthetic tape, head camera 111.6 / 112.3 px/mm, 0.3 deg):
pocket found within 4-6 um, tape angle within 0.03 deg, for 8 and 12 mm tape, both tape
directions, 1024 and 800 px windows; an expected position 1.6 mm off snaps to the right
pocket; no tape gives a clear error; a batch continues past a failed feeder.
