# camserver: NeoDen4 cameras over the network

`camserver.exe` runs on the machine's own Windows XP PC, next to the existing camera
drivers, and serves both cameras over TCP. The vision / UI program (`neocam`) can then
run on any PC on the network, or on the same PC.

```
NeoDen4 PC (Windows XP, x86)                      any PC
  cyusb3.sys / CGUSB2.sys (existing drivers)
  NeodenCamera.dll  or  imgdll.dll + CGUSB2.dll
  camserver.exe  ── TCP 5600 ─────────────────►  neocam (Python, OpenCV 4)
```

Do not run NeoDen4.exe at the same time: only one program can open the cameras.

## Install on the machine PC

Copy `camserver.exe` into the folder that holds the camera DLLs, i.e.
`D:\NeoDen4` (new camera: `NeodenCamera.dll`) or `C:\Neoden` (old camera:
`imgdll.dll` + `CGUSB2.dll`). Or pass the folder: `camserver --dir D:\NeoDen4`.

```
camserver [--port 5600] [--bind 0.0.0.0] [--dir <dll folder>] [--old | --new]
```

Like NeoDen4.exe, it tries the old camera (`imgdll.dll`) first, then the new one
(`NeodenCamera.dll`); `--old` / `--new` forces one. At start it sets both cameras to a
centred 1024 × 1024 window, exposure 0x19 and gain 8. Windows XP's firewall may ask to
allow it.

## Cameras

| logical id | camera | old DLL id | new DLL id | NeoDen4 class |
|---|---|---|---|---|
| 0 (`UP`) | fixed up-looking camera (parts on the nozzles) | 0 | 5 | `CImgBoard` |
| 1 (`HEAD`) | head camera, looking down (fiducials, positions) | 1 | 1 | `CImgHead` |

`cam | 0x80` addresses a raw DLL camera id. Both sensors are 1280 × 1024 8-bit gray;
the window is centred on the sensor. Frames are sent as the camera delivers them: the
vision code rotates them the way NeoDen4.exe does.

The up camera's ring light is not a camera function. NeoDen4 switches it on with the
mainboard (serial register 7, byte 4 bit 0 — `CFunNodeMainBoard::setOutput`). The head
camera's LED is on the head (register 4, `CFunNodeHead::setLed`). Those go through the
serial link (see `PROTOCOL.md` in the RE notes), not through camserver.

## Protocol

TCP, little-endian, one request at a time, one client at a time.

Request, 16 bytes:

| offset | type | field |
|---|---|---|
| 0 | u8 | command |
| 1 | u8 | camera (logical id, or raw id \| 0x80) |
| 2 | u16 | flags (0) |
| 4 | i32 | a |
| 8 | i32 | b |
| 12 | i32 | c |

Reply: `i32 status` (1 = ok, 0 = failed, -1 = unknown command), `u32 len`, then `len` bytes.

| cmd | name | arguments | reply data |
|---|---|---|---|
| 1 | INFO | — | ASCII, e.g. `camserver 1.0 backend=new cams=2 up=1024x1024 head=1024x1024` |
| 2 | EXPOSURE | a = value (≥ 1) | — |
| 3 | GAIN | a = value | — |
| 4 | WINDOW | a = width, b = height (16..1280 × 16..1024) | — |
| 5 | LED | a = value (new camera only) | — |
| 6 | GRAB | a = timeout ms (0 → 1000) | `u16 w, u16 h`, then `w*h` gray bytes |
| 7 | RESET | — (new camera: vendor 0xB3 + endpoint reset) | — |
| 8 | BYE | — | — (connection closes) |

## What the DLL calls are

New camera (`NeodenCamera.dll`, Cypress FX3, VID 04B4 PID 00F0/00F1/00F3), all `__cdecl`:

| export | USB | used for |
|---|---|---|
| `int img_init()` | open all CyUSB devices, classify by product string (`H` → 1, `B1` → 5, `B4` → 4, `F` → 2/3) | count |
| `img_set_exp(id, u16)` | vendor 0xB4 | exposure |
| `img_set_gain(id, u16)` | vendor 0xB5 | gain |
| `img_set_lt(id, x, y)`, `img_set_wh(id, w, h)` | vendor 0xB6, 0xB7 | window |
| `img_led(id, u16)` | vendor 0xB2 | LED |
| `img_reset(id)` | vendor 0xB3 + bulk endpoint reset | reset |
| `char img_readAsy(id, buf, len, timeout, trigger)` | queue 512 KB bulk reads, vendor 0xB1 if trigger, wait | frame |

Old camera (`imgdll.dll` → `CGUSB2.dll`, CGImagetech CGU2-130M, VID 0828 PID 1002):

| export | does | used for |
|---|---|---|
| `int img_init()` | open camera 0 and 1 (incl. a DES challenge with the camera) | count |
| `img_set(cam, mode, exposure, gain)` | vendor 0xB0 = mode, sensor reg 0x09 = exposure, reg 0x35 = gain | NeoDen4: mode 1 up camera, 2 head |
| `img_setSize(cam, w, h)` | centred window | window |
| `img_capture(cam, buf)` | one frame, `w*h*3` bytes BGR (gray in all three), 1 s timeout | frame |
