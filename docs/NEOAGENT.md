# neoagent: network agent on the machine PC

`neoagent.exe` runs on the NeoDen4's own Windows XP PC and makes the machine reachable over the
**local network**:

- **Web UI** (temporary, until the desktop client exists): dashboard, manual control, cameras,
  setups (calibration), all settings, files, updates.
- **JSON API** for the web UI and, later, the desktop client.
- **FTP server** with access to every drive, to edit files from any FTP client.
- **Updates of everything** (programs, the agent itself, web UI, Config.ini, job files) as signed
  packages, with backup and automatic rollback.
- **Supervisor**: starts `camserver.exe` and `serbridge.exe`, restarts them if they stop, collects
  their output in one log.

```
NeoDen4 PC (Windows XP)                                             any PC / tablet on the LAN
  neoagent.exe  HTTP 8080 (web UI + API), FTP 21  ◄──────────────── browser, FTP client
     ├─ camserver.exe  127.0.0.1:5600  cameras (NeodenCamera.dll / imgdll.dll)
     └─ serbridge.exe  127.0.0.1:5602  mainboard on COM2
```

Plain C, 32-bit, static; it imports only functions that exist in the machine's own XP SP2
`kernel32`, `msvcrt` and `ws2_32` (checked against the DLLs from the disk image).

## Install on the machine PC

1. Close NeoDen4.exe (it uses COM2 and the cameras).
2. Copy to e.g. `C:\neoagent\`: `neoagent.exe` and the `www` folder. Copy `camserver.exe` and
   `serbridge.exe` into `D:\NeoDen4\` (next to the camera DLL).
3. Run `neoagent.exe` once: it writes `neoagent.ini` next to itself. **Change `password` and
   `update_key`**, then start it again. Allow ports 8080 and 21 in the XP firewall.
4. Browse to `http://<machine PC>:8080` and log in.
5. To start it with Windows: put a shortcut to `neoagent.exe` in the Startup folder.

`neoagent.ini`:

```ini
[agent]
http_port=8080
ftp_port=21            ; 0 = no FTP
bind=0.0.0.0           ; 127.0.0.1 = this PC only
user=admin
password=neoden        ; change it (also the FTP password)
update_key=change-me   ; change it; tools/mkpkg.py signs with the same key
www=www
backup=backup
ftp_root=              ; empty = all drives, or a folder
cam_port=5600
neoden_dir=D:\NeoDen4  ; Config.ini and job files (the web UI opens them from here)

[procs]                ; name=folder|command line, kept running
camserver=D:\NeoDen4|camserver.exe --bind 127.0.0.1
serbridge=D:\NeoDen4|serbridge.exe --bind 127.0.0.1
```

## Safety and security

- LAN only. Everything (web, API, FTP) needs the login; FTP and HTTP send it in clear text, as
  usual on a LAN, so do not expose the ports to the internet.
- **One controller at a time**: commands that move or switch the machine need the control lock
  (`Take control` in the header). The lock expires 15 s after the browser stops renewing it.
  neoserver: an owner idle for 10 s (no command; a plain renewal does not count) hands control
  to the next client that asks, so the machine's own screen (docs/KIOSK.md) and a LAN browser
  both work without pressing release. Never during a job or an align call. Scripts that pause
  longer between commands renew with `hold=1`. STOP (`/api/mc/cancel`) needs no lock.
- The machine rules of NeoDen4.exe apply (in `machine.c`, same as `neomach/board.py`): nozzles up
  before any XY or rail move, no nozzle below 1 mm while XY moves, XY limits, homing required.
- **STOP** cancels waits, switches the valves off and raises the nozzles. It is
  not an emergency stop: keep the machine's own emergency stop at hand.
- Every overwrite or delete (web, FTP, update) first copies the old file into
  `backup\<time>\`; any backup can be put back from the Files or Updates page.

## Updates

Build a package on any PC with the update key from `neoagent.ini`:

```
python tools/mkpkg.py -k <update_key> -o update.neopkg ^
    camserver/camserver.exe=D:\NeoDen4\camserver.exe ^
    neoagent/www=@agent\www ^
    neoagent/neoagent.exe=@agent\neoagent.exe
```

Install it from the Updates page, or send it directly:
`python tools/mkpkg.py -k <key> --send http://admin:<pw>@<pc>:8080 SRC=DEST ...`

The agent checks the HMAC signature and each file's SHA-256, backs up every file it replaces,
stops the programs whose folder gets a file, writes all files (temp file + replace), restarts the
programs, and rolls everything back if any step fails. A new `neoagent.exe` is started after the
reply; if it does not answer within 15 s the old one is put back.

## API

All under Basic authentication. Replies are JSON (`{"ok": true, ...}` or
`{"ok": false, "error": "..."}`), except file and image downloads.

| call | |
|---|---|
| `GET /api/ping`, `GET /api/status` | agent version, host, uptime, `neoden_dir`, programs, camera info |
| `GET /api/procs`, `POST /api/proc?name=&action=start\|stop\|restart` | supervised programs |
| `GET /api/log?since=N` | log lines (agent + programs) after N |
| `GET /api/files?path=` | folder listing (`path=` empty: drives) |
| `GET /api/file?path=` / `PUT /api/file?path=` | read / write a file (old version backed up) |
| `POST /api/delete?path=`, `/api/mkdir?path=`, `/api/rename?from=&to=` | |
| `GET /api/backups`, `POST /api/restore?id=` | backups |
| `POST /api/update` (body = package) | install an update |
| `GET /api/cam/grab?cam=0\|1&scale=1..8` | frame as 8-bit BMP, turned like NeoDen4 (0 up camera, 1 head) |
| `POST /api/cam/set?cam=&exposure=&gain=&w=&h=&led=[&save=1]` | camera settings (each optional); save=1 (the Cameras page) also keeps them as the viewing settings (camera.json), else they last until the next view restore |
| `GET /api/mc/status` | XY state, last XY target, nozzle idle bits, vacuum, Z/angles, outputs, control owner |
| `POST /api/mc/lock?client=ID[&hold=1]`, `/api/mc/unlock?client=ID` | control lock (`hold=1`: counts as activity) |
| `POST /api/mc/cancel` | cancel the running wait |
| `POST /api/mc/<cmd>?client=ID&...` | machine commands (need the lock), below |

Machine commands: `home`, `correct` (step loss), `move?x=&y=&slow=&speed=`, `rel?x=&y=&slow=`,
`z?n=&mm=&speed=&wait=`, `rotate?n=&deg=&speed=&wait=`, `raise`, `valve?ch=&state=` (−1 suck, 1 blow,
0 off), `led?v=` (0 off, 1 inner, 2 outer, 3 both), `light?on=` (up-camera light), `beep?on=`,
`board_ready?on=`, `xy_speed?v=&force=`, `rail_speed?v=`, `rail_move?mm=&wait=`, `rail_stop`
(the rail commands stay in the API, but the web UI has no rail controls: this machine uses the
fixed table, PCB mode `Manual`, with no conveyor),
`feed?n=&p0=&p1=&mode=` (mode 0 feed, 1 change ID, 2 calibrate), `feeder?n=` (status),
`peel?n=&a=&b=`, `vib?a=&b=`, `vib_feeder?on=&v=&p=`, `reg_read?addr=`, `reg_write?addr=&data=<16 hex>`.

## Board points (Manual page)

Measure machine coordinates of features on a board for calibration. With the head camera live, click
a feature to bring it under the crosshair (or jog), choose the tool (camera or nozzle 1..4) and press
**Record point**: the stored value is the point under that tool, `head XY − offset(tool)` (the same
frame NeoDen4 uses for stacks and marks). Points have a name and optional **nominal** coordinates
(from the board drawing); "Go to" brings the selected tool back over a point.

With nominal values for 2 points the page fits a similarity (one scale, rotation, offset); with 3 or
more an affine fit: **scale X**, **scale Y** (axis travel per commanded mm), **rotation**, **X/Y skew**
(error of the 90° between the axes), offset, and the residual of every point (µm). Points are saved
to `<neoden_dir>\points.csv` (`name,x,y,nominal_x,nominal_y,tool`) and can be downloaded or imported.

## Vision settings ([Vision] in Config.ini)

New settings, ignored by NeoDen4.exe, edited on Settings → Vision:
`bSizeCheck`, `fSizeMin` / `fSizeMax` (measured / package size, NeoDen4 had fixed 0.8–1.25),
`sSizeOrientation` (`stack` = pair W/H by the stack angle rounded to 90°, `auto` = closest fit),
`bVerifyAngle` (re-image after the angle correction), `fVerifyAngleTol` (°), `fVerifyOffsetTol` (mm),
`nVerifyTries`. The algorithms are in `neocam/vision.py` (`size_check`, `correct_and_verify`).

## Job settings ([Job] in Config.ini)

Settings → Job: `bSound` (all beeps; off = silent; the manual Buzzer button still works),
`bBeepJobEnd`, `bBeepError` (the machine waits for the operator), `bSettle` with
`fSettleTolPx` / `fSettleMaxS`: grab images until the picture stops moving
(`neocalib.grab_settled`) instead of NeoDen4's fixed 250 ms. Camera API:
`/api/cam/grab?cam=&settle=1` answers with the header `X-Settle: ok 82ms 3f` (or `timeout`).
Defaults: `NeoConfig.JOB_DEFAULTS` in `neocam/config.py`.

## Job wizard (neoserver only)

The Job wizard page turns an Altium Pick & Place CSV into a NeoDen machine file on the machine,
with `neosetup` (the KFG tool as a library, docs/SETUP_WIZARD.md): 1 Files (upload the CSV, a
machine file from `Proc` as the template, Gerber `.zip`, `.kfg` projects), 2 Options, 3 Parts
(BOM, heights, similar names), 4 Board (size, turn, flip, fiducial, a plain map), 5 Alignment
(record the head-camera position on two or three parts: point = head − camera offset; compute,
then apply), 6 Feeders (build, edit, arrange), 7 Order, 8 Speed, 9 Output (machine file into
`Proc/<name>.csv`, stages, parts kit, P&P CSV, project). Uploads and outputs live in
`<neoden_dir>/Jobs`; the tool's options in `<backup>/neosetup-settings.json`.

API: `POST /api/setup/call` with `{"sid", "method", "args", "kwargs"}` → `{ok, result, messages,
dirty}`; the tool refusing gives 422 with `title`, `text`, `messages`; an unknown component /
feeder 404; a call not on the list in `neoserver/setup.py` 400. `POST /api/setup/close?sid=`
drops that project. One project per browser tab; idle ones are dropped after 12 h.
Questions the tool would ask are answered "yes" by neosetup, so the page asks first
(loading a project, building again, deleting a part, overwriting a machine file).

## Job page (neoserver only)

Runs a job file from `Proc` on the fixed table with `neojob` (JOB_SPEC.md): Start, Pause,
Continue, Single step, Stop, and "Continue where it stopped" (the progress is kept in
`<job>.csv.resume.json` after every cycle). The machine homes XY at every start. Manual
commands are refused while a job runs. The runner's questions ("try again?") show on the page
with Yes / No / Ignore. Only pcb mode `Lock` (fixed table) is accepted.

API: `GET /api/job/status?since=n`, `GET /api/job/resume?path=`, `POST /api/job/start?path=&resume=0|1&client=`
(needs the control lock), `POST /api/job/pause|continue|step|stop`, `POST /api/job/answer?a=yes|no|ignore`.

## Build and test

```
sh neoagent/build.sh                       # MSYS2 mingw-w64-i686-gcc
python neoagent/test/test_neoagent.py      # HTTP, files, backups, camera, supervisor, FTP, updates, rollback, self update
python neoagent/test/test_machine.py       # API -> neoagent -> serbridge.exe -> board simulator
python neoagent/demo.py                    # whole stack with fake hardware: http://127.0.0.1:8080 (admin / demo)
```

## Not verified on the machine yet

Everything above ran against the simulators only: the real cameras, the real board, the XP
firewall prompt and the timing of the XP PC are still to be tried.
