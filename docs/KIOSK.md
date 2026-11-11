# Kiosk: the web UI on the machine's own monitor

At boot, without a login, the machine PC's monitor shows the same web UI as a browser on
the LAN (`http://neoden.local`), full-screen. The keyboard and mouse work in it; arrow keys
jog on the Manual page like in any browser.

## Boot screen

Instead of the Linux boot text the monitor shows the NeoDen boot screen (logo, running
dots, dark background) from the boot loader on, and the kiosk window takes over from it
with a starting page in the same look until neoserver answers; then the UI. Shutdown and
reboot show the same screen.

- plymouth, theme `neokiosk/splash/` (`neoden.plymouth`, `neoden.script`, PNGs) installed as
  `/usr/share/plymouth/themes/neoden` and made the default (`update-alternatives`), in the
  initramfs.
- `/etc/default/grub.d/neoden-splash.cfg`: `quiet splash loglevel=3 rd.udev.log_level=3
  systemd.show_status=auto vt.global_cursor_default=0` (read after `/etc/default/grub`, so
  the image build's own settings stay as they are). Errors are still in the journal and on
  the console when a unit fails.
- `neoden-kiosk.service` runs `plymouth quit --retain-splash` first, so there is no text
  console between the boot screen and cage (cage itself starts on black for a moment).
- The starting page says why only after 30 s without the server (with the PC's addresses).
- Text boot for a test: at the boot menu (hold Shift, or Esc) edit the entry and remove
  `quiet splash`; for good: delete `neoden-splash.cfg`, `sudo update-grub`.
- To change the look: edit `neokiosk/splash/`, run `install.sh` again (it copies the theme
  and runs `update-initramfs` + `update-grub`). Tested in QEMU (bochs-drm): the screen
  changes size during the boot, so the script lays out and repaints again when it does.

## What runs

| piece | what |
|---|---|
| `neoden-kiosk.service` | systemd unit, user `neo`, no login, takes tty1 (the VT active at boot), `Restart=always` |
| `seatd.service` | gives cage the screen, keyboard and mouse (logind would need polkit for that) |
| `python3 -m neokiosk --screens` | before cage, as root: switches off outputs with no monitor behind them (see `output`) |
| `cage` | minimal Wayland kiosk compositor (Ubuntu deb, universe): one full-screen window, no desktop |
| `python3 -m neokiosk` | a WebKit2GTK 4.1 window (`gir1.2-webkit2-4.1`, from Ubuntu's security updates) on `url` |
| `/etc/neoden/kiosk.ini` | settings (URL, zoom, rotation, cursor, renderer) |

The window waits for neoserver: until it answers, it shows "waiting for the machine server"
with the PC's IP addresses and retries every 2 s. If the WebKit page process crashes the
page is reloaded; if the window or cage exits, systemd starts it again after 3 s.
cage never blanks the screen, and the text consoles do not blank either.

Why this browser: Ubuntu 24.04's chromium and firefox are snaps (no snapd in a chroot build,
heavy on the Atom D525). WPE `cog` is not packaged for 24.04. WebKit2GTK is a normal deb
with security updates, runs the UI (ES2020, canvas, fetch), and with CPU drawing (`gpu = no`,
`renderer = pixman`) does not depend on the old GMA 3150 OpenGL driver.

Keys handled by the kiosk itself (everything else goes to the page):

| key | action |
|---|---|
| F5 or Ctrl+R | reload the page |
| Ctrl+plus / Ctrl+minus / Ctrl+0 | zoom in / out / back to `zoom` |
| Ctrl+Alt+F2 ... F6 | text console (log in as `neo`); Ctrl+Alt+F1 back to the kiosk |

There is no right-click menu, no address bar, no Back button; links that open a new window
open in the same one.

## Install

New images: `build/image/setup-chroot.sh` runs `neokiosk/install.sh` (packages, settings
file, unit, enabled).

An existing machine (needs internet for apt; the app on the machine must have the `neokiosk`
folder, e.g. after an update):

    sudo bash /opt/neoden/app/neokiosk/install.sh

It is idempotent and keeps an existing `kiosk.ini`. `--reinstall` runs apt again.

## Stop, start, restart (over SSH: `ssh neo@neoden.local`)

    sudo systemctl stop neoden-kiosk        # tty1 stays empty; Ctrl+Alt+F2 for a text login
    sudo systemctl start neoden-kiosk
    sudo systemctl restart neoden-kiosk     # after editing kiosk.ini
    sudo systemctl disable --now neoden-kiosk && sudo systemctl enable --now getty@tty1   # back to a text console for good
    journalctl -u neoden-kiosk -b           # its log

On the machine itself: Ctrl+Alt+F2 gives a text console, log in as `neo`, then the same
commands. Stopping the kiosk does not stop neoserver or the machine.

## Settings (`/etc/neoden/kiosk.ini`)

    [kiosk]
    url = http://localhost/#job  ; the mount screen; another page, or neoserver on another port: http://localhost:8080/#job
    zoom = 0.85                  ; 0.85: the job tables fit 1280x1024 (new installs; default 1.0)
    output = auto                ; auto: only outputs with a monitor (EDID); all; or names: VGA-1
    fallback_mode = 1280x1024    ; monitor without EDID and none saved: this mode (also 1024x768, 1920x1080)
    rotate = normal              ; 90, 180, 270 (clockwise), flipped, flipped-90 ...
    hide_cursor = auto           ; auto: hidden only with a touch screen and no mouse; yes; no
    renderer = pixman            ; cage drawing: pixman (CPU) or gles2 (GPU)
    gpu = no                     ; WebKit with the GPU: yes / no
    lite = yes                   ; no shadows / transitions in the kiosk (much faster on the CPU)
    watch = auto                 ; reload the page when the web UI files change: auto, a folder, no
    mouse_speed = 1.0            ; pointer speed: 2 = twice as fast, 0.5 = half (the web UI: Settings -> This PC's screen)
    theme = auto                 ; the pages' colours here: dark, light, auto (the theme button on this screen writes it)
    env =                        ; extra environment for WebKit, e.g. WEBKIT_...=1 (space separated)

Then `sudo systemctl restart neoden-kiosk`.

- Change the URL: edit `url`.
- Monitor without EDID: on one boot the Dell on VGA did not answer on the cable (no EDID
  even after probing again). The kernel then gives VGA only 1024x768, both outputs look
  alike, and the page was spread over VGA and the phantom LVDS (2048 px). Now
  `neokiosk --screens` (root, before cage) probes a connector without EDID 3 more times; if
  it still has none it gives the kernel, through debugfs `edid_override`, the monitor's EDID
  saved from an earlier boot (`/var/lib/neoden-kiosk/edid/`) or else a standard EDID for
  `fallback_mode` (made by `make_edid`, maker "NEO"); then only outside connectors (VGA, DVI,
  HDMI, DP) are kept, the LVDS panel is switched off. The override lasts until the next boot.
  Changing the mode or switching an output off inside cage crashes cage 0.1.5, so all of
  this happens before it starts.
- Screens: cage spreads the page over every connected output. The Atom D525 boards report
  an LVDS panel that is not there (`LVDS-1`, connected, no EDID), which put the page's left
  1024 px on that phantom screen and only the rest on the monitor. With `output = auto` the
  service switches off (as root, before cage) every connected output without EDID when one
  with EDID exists, by writing `off` to `/sys/class/drm/card*-LVDS-1/status`; it stays off
  until the next boot. `output = VGA-1` keeps only that one; `all` keeps all. (wlroots 0.17
  has no connector filter, and `wlr-randr --off` inside cage 0.1.5 crashes cage.)
- Rotate the screen: `rotate = 90` (done with `wlr-randr --transform` when the window starts).
  Touch input follows the output in cage.
- Try the GPU: `renderer = gles2` and/or `gpu = yes`. If the screen stays black or the log
  shows EGL/GLES errors, go back to `pixman` / `no`. On the GMA 3150 `gpu = yes` is not usable:
  the i915 driver cannot compile WebKit's shaders ("Exceeded max nr indirect texture lookups"),
  canvases (the camera picture, maps) stay black, and the CPU use goes up.
- Speed (`lite`): with CPU drawing every repaint redraws the page, and the soft card shadows
  (`box-shadow` blur) cost most of it. Measured on the machine (Atom D525, 1280x1024, Manual
  page, head camera live): `lite = no` about 0.5 camera frames/s with the page process at
  100 % of a core (also 100 % on the idle Work page); `lite = yes` about 3.6 frames/s at about
  50 % (0 % on the idle Work page). The rest is the camera grab itself (~190 ms on the machine
  for a half-size frame). `lite` changes only the kiosk; LAN browsers keep the shadows.

- Web UI updates: a LAN browser gets new pages when someone presses F5; nobody does that
  on the machine, so the kiosk kept running the old JavaScript until it was restarted and
  behaved differently from the web. With `watch = auto` (and `url` on this PC) the kiosk checks
  `neoagent/www` every 2 s and reloads the page once a change has settled (an update, a file
  written through `/api/file` or FTP). The page and the control-lock client stay the same.
- WebKitGTK vs Chrome: the kiosk hides the GTK spin buttons of number fields (they are much
  wider than Chrome's and cut the numbers in the job tables); the arrow keys still step.
  Other form controls (drop-downs, check boxes) use the GTK look and are a little taller.
- Fonts: DejaVu plus `fonts-noto-color-emoji` (installed by `install.sh`); without the emoji
  font the UI's 📷 / 📁 / 🔒 buttons are empty boxes.

- Language and theme: the top bar's فا/EN and ☾/☀ buttons (js/i18n.js), stored per screen,
  so the machine's monitor and each browser choose their own. A default for the machine's
  screen: `url = http://localhost/?lang=fa&theme=dark#job` (stored on every start of the
  kiosk, then the buttons change it until the next start). Persian texts: js/fa.js; texts
  not in it yet: `python tools/i18n_extract.py`.

## Notes

- The kiosk is just another browser on neoserver: the same control lock and pages as a
  browser on the LAN.
- Files the page offers for download are not saved anywhere useful; use a LAN browser for
  exports.
- Tested in QEMU (std VGA, 1024x768, CPU drawing) on the built image: install, boot straight
  into the UI, waiting page while neoserver is stopped, restart after a crash, Ctrl+minus zoom,
  Ctrl+Alt+F2 / F1. About 400 MB RAM used in all.
- On the machine (GMA 3150, Dell P1917S on VGA, 1280x1024, CPU drawing) the Work page fits
  the screen at zoom 1.0. Screenshot over SSH: `grim` (not installed; `apt-get download grim`
  and `dpkg -x` it as `neo`), with `XDG_RUNTIME_DIR=/run/neoden-kiosk WAYLAND_DISPLAY=wayland-0`.

## Mouse speed

cage has no pointer setting, so `mouse_speed` goes through libinput: it scales every mouse's motion to
1000 dpi by the mouse's `MOUSE_DPI` (udev hwdb). The root step before cage (`--screens`) writes
`/etc/udev/hwdb.d/71-neoden-mouse.hwdb` with `MOUSE_DPI = 1000 / mouse_speed` for every mouse
(`mouse:*:name:*:`), runs `systemd-hwdb update` and `udevadm trigger`; `mouse_speed = 1` removes it.
The web UI's Settings -> This PC's screen writes `mouse_speed` / `zoom` into kiosk.ini and restarts the
kiosk (`POST /api/kiosk`).
