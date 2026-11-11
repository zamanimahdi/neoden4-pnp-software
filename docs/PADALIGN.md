# Placement accuracy: 3+ fiducials, pad alignment, lead vision

Two things improve placement accuracy over NeoDen4:

1. **Board transform from 3 or more fiducials** (`[Job] sMarkFit`). NeoDen4 uses two marks:
   it finds the shift and rotation, but not the scale. With `affine` (the default, 3+ marks),
   the fit also finds scale in X and Y and skew, so a board that shrank, grew or sits skewed is
   corrected everywhere. With `bilinear` (4+ marks at the corners), it also finds a twist
   between the corners, i.e. a warped board. With fewer marks, the simpler fit is used. The job
   log shows the fit used and how far each mark is from it (residuals). In the test, a
   stretched and skewed board in the middle between 3 marks was off by 5 µm with `affine`; the
   2-mark fit was off by 21 µm.
2. **Pad alignment** (`[PadAlign]`, `neojob/padalign.py`) for the parts that need it. Just
   before the part is placed, the head camera looks at the part's pads on the board. The
   placement is then corrected by where those pads really are. This catches what the
   fiducials cannot: local warp, etching and copper shifts. It works next to the up camera,
   which measures the part on the nozzle.

3. **Lead vision** (`[LeadVision]`, `neojob/leadvision.py`) for parts whose leads matter more
   than their body: SOT-23, SOIC, QFP, connectors. NeoDen4 measures the outline of the whole
   bright part on the nozzle. The moulding is often off its leads, and leads, tabs and the body
   all pull that outline. Here, the up camera finds the leads and matches them to the part's
   own pads from the Gerber (the same pads file), so the leads land on the pads. In the test, an
   SOT-23 whose body was moulded 0.18 mm off was measured within 4 µm on its leads; its body
   outline was 0.37 mm off.

Pad alignment works for every part with pads, also two-pad passives (0402, 0603): the found
pad sizes and their spacing must match the Gerber (`fMaxScaleErr`), because two points always
fit a rotation and shift exactly.

The camera does not see height. Board warp also changes the place height: use support pins
under the board, and set the Place Height and delay with the nozzle spring in mind.

## How pad alignment works

- **Pads file.** `<job>.csv.pads.json` holds every part's pads in the job's frame. It is built
  from the board's Gerber and its Altium Pick & Place file: `python -m neojob.pads JOB PNP
  GERBER`, or `POST /api/align/pads`.
  - The pads come from the first layer that has any, in this order: Top Pad Master, Paste,
    Solder Mask, Copper. Both flashes and pad-sized regions count.
  - The Gerber is lined up with the P&P file through the pad-1 points (as neosetup does).
  - The P&P file is lined up with the job (board turned or flipped by the wizard) by fitting
    the designators both files share. Rows that did not move with the board, such as
    fiducials, are dropped.
  - Each pad goes to the nearest part.
  - Result on the GPS board: 88 of 90 top parts, 601 pads, fit rms 4 µm.
- **During the job.** For a part that has pad alignment switched on (`bEnabled`, plus
  `sFootprints` or `sRefs`):
  1. The pads are put through the board transform.
  2. The camera looks there: one view, or two opposite corners for a part larger than about
     6 mm.
  3. Bright blobs of pad size are found (Otsu or a fixed threshold, lamp `nLed`, exposure
     `nExposure`, 0 = automatic).
  4. Each expected pad is paired with the nearest blob within `fMatchRadiusMm`.
  5. A rotation + shift about the part centre is fitted, with outliers dropped.
- **Checks.** At least `fMinMatched` of the visible pads must be found, and the rms must be
  below `fMaxRmsMm`. A correction larger than `fMaxShiftMm` / `fMaxTurnDeg` is asked first:
  Yes uses it, Ignore places without it, No stops the job. A failure asks Yes (look again) /
  Ignore / No.
- **Tests.** In synthetic images, the result was within 1–2 µm and 0.005° of the true
  offset, for an SOIC-8 (one view) and a QFP-64 (two views).

## Settings ([PadAlign] in Config.ini)

| key | default | meaning |
|---|---|---|
| bEnabled | false | off until it has been checked on a test board |
| sFootprints / sRefs | "" | which parts: footprints or designators, comma separated; `*` = all |
| nLed | 2 | head lamp: 1 inner, 2 outer, 3 both |
| nExposure | 0 | 0 = automatic (as for marks) |
| nThreshold | 0 | 0 = Otsu, else a grey level |
| bInvert | false | pads darker than the board |
| fMinPadMm2 / fMaxPadMm2 | 0.05 / 25 | blob size that counts as a pad |
| fMatchRadiusMm | 0.6 | how far a pad may be from where it should be |
| fMinMatched | 0.6 | share of the visible pads that must be found |
| fMaxRmsMm | 0.08 | fit quality |
| fMaxShiftMm / fMaxTurnDeg | 0.5 / 2 | larger corrections are asked first |
| fHeadCamAngle | 0 | the head camera turned against the machine axes (from camcal) |

| fMaxScaleErr | 0.04 | found pad spacing vs. the Gerber (the check for 2-pad parts) |

`[Job] sMarkFit`: `neoden`, `affine` (default) or `bilinear`.

## Lead vision ([LeadVision] in Config.ini)

| key | default | meaning |
|---|---|---|
| bEnabled | false | off until it has been checked on parts held by the nozzle |
| sFootprints / sRefs | "" | which parts (`*` = every part that has pads) |
| nExposure | 0 | 0 = the job's up-camera exposure |
| nThreshold | 0 | 0 = automatic: leads are the brightest of three levels (background, body, metal); both Otsu levels are tried |
| fMinLeadMm2 / fMaxLeadMm2 | 0.01 / 4 | blob size of a lead |
| fMatchRadiusMm | 0.4 | how far a lead may be from where the pattern puts it |
| fMinMatched | 0.7 | share of the leads that must be found |
| fMaxRmsMm | 0.05 | leads must fit the pad pattern this well |
| bBodyFallback | false | leads not found: use the body outline instead of asking |

How it works:
1. The expected lead pattern is the part's pads, turned by the stack angle.
2. The four quarter turns are tried. A symmetric part (QFP) keeps the feeder's orientation, so
   pin 1 stays where the feeder puts it, unless another turn is clearly better.
3. The result is the lead pattern's centre and turn, the same as NeoDen's part result. The
   placement then aims that centre at the pad pattern's centre on the board.
4. The size check is skipped for these parts, because the leads matched to the pads are the
   stronger check.

## Calibrating on a test board (live)

Use the API (`neoserver/align.py`), or from a PC on the LAN:
`python tools/padalign_live.py --host neoden.local --password … <command>`. It saves the images
to `padalign_out/`: blue + = expected pad, yellow = found pad, green + = fitted pad, red + =
camera centre.

1. **Head camera scale and angle.** Put the camera over a group of pads (Manual page), then run
   `camcal`. It moves the head 1 mm in X and in Y and follows the pads. Write `px_per_mm` into
   `[Vision] fHeadPxPerMm` and `cam_angle_deg` into `[PadAlign] fHeadCamAngle`. Repeat until
   the values are stable (within 0.2 px/mm and 0.05°).
2. **Pad detection.** Run `detect` over different pad types (small passives, QFP leads, large
   pads). Change `--led`, `--exp`, `--thr` and `--min/--max` until every pad is found, and
   nothing else. `mean` and `hint` in the reply show whether the image is too dark or too
   bright. Put the values that work into `[PadAlign]`.
3. **Pads file.** Run `pads JOB PNP GERBER` (paths on the machine), then `parts JOB`.
4. **Board transform.** Run `marks JOB`. The residuals show how well the marks fit; with 3+
   marks they should be a few hundredths of a mm.
5. **One part.** Run `part JOB U1` a few times. The correction should repeat within about
   0.01 mm. Then place the part in a job with pad alignment on for it, and measure the result.
6. **Lead vision.** Run `leads 1 bg` with an empty nozzle 1. Pick the part by hand (Manual
   page), then run `leads 1 JOB REF`. The reply shows the leads found, the result and NeoDen's
   body outline for comparison (`leads_vs_body_mm`). Tune `--thr` / `--exp` until every lead
   is found.
7. **Switch it on.** Set `bEnabled=true` and the footprints or designators in `sFootprints` / `sRefs`, in
   `[PadAlign]` and/or `[LeadVision]`.

The accuracy depends on the head-camera-to-nozzle offset (`dPosCamLookDownMouse1`). The camera
finds the pads exactly, but the nozzle places the part, so that offset must be calibrated well
(neocalib).

## Vision log ([Job] bVisionLog) — data for calibrating the vision

A temporary tool. When `bVisionLog=true`, every image the job takes goes to
`<neoden_dir>/VisionLog/<job>/<date>/`, with the result the machine applied to it:
- part images (up camera, body outline or leads), with the empty-nozzle backgrounds;
- fiducial images (each of the three centring shots);
- pad-alignment views.

`index.jsonl` holds one line per image: job, panel, designator, feeder, footprint, value,
nozzle, camera, vision mode, method, exposure, px/mm, ROI, stack angle, package size, the
result (found, offset, angle, size) and the calibration in use. Later lines add what was
decided: the size ratio and check, the reject reason, placed and the target. The folder is kept
under `nVisionLogMaxMB` (2000), deleting the oldest days first. Switch it off and delete the
folder (or `POST /api/visionlog/clear`) when you are done.

`tools/visionlog.py` (run on the machine over SSH, or on a copy taken by FTP):
- `summary DIR`: per footprint: images, recognised, placed, rejected by reason, size ratio
  min / median / max. The same data is at `GET /api/visionlog/summary`.
- `rerun DIR --k --thr --roi [--footprint]`: the part vision again on the stored images with
  other px/mm, threshold or ROI, compared with what was used.
- `scale DIR [--footprint]`: up-camera px/mm suggested from the median size ratio. A part's
  outline looks about 1 px larger per side than its datasheet size, so confirm it with a known
  part before writing it into `[Vision] fUpPxPerMm`.
