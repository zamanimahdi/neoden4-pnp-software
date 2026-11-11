#!/bin/bash
# Kiosk on the machine PC's monitor: the web UI full-screen at boot (docs/KIOSK.md).
# Run as root, once: from the image build (setup-chroot.sh) or on a running machine over SSH:
#   sudo bash /opt/neoden/app/neokiosk/install.sh
# Idempotent. Needs apt (internet). Keeps an existing /etc/neoden/kiosk.ini.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
APP=/opt/neoden/app
INI=/etc/neoden/kiosk.ini

# cage: Wayland kiosk compositor (wlroots); WebKit2GTK 4.1 through python3-gi (both plain debs,
# no snap); seatd hands cage the screen and input (logind would need polkit for that);
# mesa for the i915 (GMA 3150) when renderer = gles2; plymouth for the boot screen
if ! dpkg -s cage seatd gir1.2-webkit2-4.1 fonts-noto-color-emoji plymouth >/dev/null 2>&1 || [ "${1:-}" = "--reinstall" ]; then
  apt-get update -q || true
  apt-get install -y -q --no-install-recommends \
    cage seatd wlr-randr python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1 \
    libgl1-mesa-dri libegl-mesa0 libgles2 fonts-dejavu-core fonts-noto-color-emoji xkb-data plymouth
fi
usermod -aG video,render,input neo 2>/dev/null || true     # seatd runs with -g video

if [ ! -f "$INI" ]; then
  port=$(sed -n 's/^[[:space:]]*http_port[[:space:]]*=[[:space:]]*\([0-9]*\).*/\1/p' /etc/neoden/neoserver.ini 2>/dev/null | head -n1)
  port=${port:-80}
  [ "$port" = 80 ] && url=http://localhost/#job || url=http://localhost:$port/#job
  mkdir -p /etc/neoden
  cat > "$INI" <<EOF
; Kiosk: the web UI full-screen on this PC's monitor (docs/KIOSK.md)
; After editing: sudo systemctl restart neoden-kiosk
[kiosk]
url = $url
; page zoom (1.0 = 100 %; 0.85 fits the pages on 1280x1024); Ctrl+plus / Ctrl+minus change it until the next restart
zoom = 0.85
; screens: auto (only outputs with a monitor; the board's phantom LVDS panel is switched off),
; all, or names, e.g. VGA-1
output = auto
; the monitor gives no EDID over the cable and none was saved: 1280x1024, 1024x768, 1920x1080
fallback_mode =
; screen rotation: normal, 90, 180, 270 (clockwise), flipped, flipped-90, ...
rotate = normal
; mouse cursor: auto (hidden only with a touch screen and no mouse), yes (hide), no
hide_cursor = auto
; mouse pointer speed: 1 = as the mouse is, 2 = twice as fast, 0.5 = half (Settings -> This PC's screen)
mouse_speed = 1.0
; the pages' colours on this screen: dark, light, auto (Settings -> This PC's screen, or its theme button)
theme = auto
; compositor drawing: pixman (CPU, works on any graphics) or gles2 (GPU)
renderer = pixman
; WebKit drawing with the GPU: no (CPU) or yes (on the GMA 3150 canvases, e.g. the camera, stay black)
gpu = no
; no shadows or transitions: each repaint is much cheaper on the CPU (camera ~4 frames/s instead of ~0.5)
lite = yes
EOF
  chmod 644 "$INI"
fi

# cage takes the active VT (tty1 at boot) through seatd; no login, no logind session
cat > /etc/systemd/system/neoden-kiosk.service <<EOF
[Unit]
Description=NeoDen kiosk: the web UI full-screen on the machine monitor
After=seatd.service neoserver.service systemd-user-sessions.service plymouth-start.service
# the kiosk takes the screen over from the boot screen (plymouth quit --retain-splash below)
Before=plymouth-quit.service plymouth-quit-wait.service
Wants=seatd.service
Conflicts=getty@tty1.service
StartLimitIntervalSec=0

[Service]
User=neo
SupplementaryGroups=video render input
RuntimeDirectory=neoden-kiosk
RuntimeDirectoryMode=0700
Environment=XDG_RUNTIME_DIR=/run/neoden-kiosk XDG_SESSION_TYPE=wayland LIBSEAT_BACKEND=seatd HOME=/home/neo
WorkingDirectory=$APP
# root (+): end the boot screen but keep its picture until cage draws (no text console between)
ExecStartPre=-+/usr/bin/plymouth quit --retain-splash
# root (+): switch off the board's phantom LVDS output before cage spreads the page over it
ExecStartPre=+/usr/bin/python3 -m neokiosk --screens --config $INI
ExecStart=/usr/bin/python3 -m neokiosk --cage --config $INI
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# boot screen instead of the Linux boot text: the NeoDen plymouth theme from the boot loader
# until the kiosk window, and a quiet kernel / systemd (errors still go to the journal)
T=/usr/share/plymouth/themes/neoden
mkdir -p "$T"
cp "$APP"/neokiosk/splash/neoden.plymouth "$APP"/neokiosk/splash/neoden.script "$APP"/neokiosk/splash/*.png "$T"/
update-alternatives --install /usr/share/plymouth/themes/default.plymouth default.plymouth "$T"/neoden.plymouth 200 >/dev/null
update-alternatives --set default.plymouth "$T"/neoden.plymouth >/dev/null
mkdir -p /etc/default/grub.d
cat > /etc/default/grub.d/neoden-splash.cfg <<'EOF'
# NeoDen boot screen (neokiosk/install.sh, docs/KIOSK.md); read after /etc/default/grub
GRUB_CMDLINE_LINUX_DEFAULT="quiet splash loglevel=3 rd.udev.log_level=3 systemd.show_status=auto vt.global_cursor_default=0"
GRUB_TIMEOUT_STYLE=hidden
EOF
# on a running machine now; the image build runs update-initramfs and update-grub after this
if ! systemd-detect-virt --chroot >/dev/null 2>&1; then
  update-initramfs -u >/dev/null 2>&1 || echo "update-initramfs failed" >&2
  update-grub >/dev/null 2>&1 || echo "update-grub failed" >&2
fi

# no screen blanking on the text consoles either (cage itself never blanks)
mkdir -p /etc/systemd/system/getty@.service.d
cat > /etc/systemd/system/getty@.service.d/noblank.conf <<'EOF'
[Service]
ExecStartPre=-/usr/bin/setterm --blank 0 --powersave off --term linux
EOF

systemctl daemon-reload 2>/dev/null || true
systemctl enable seatd.service neoden-kiosk.service >/dev/null 2>&1
systemctl disable getty@tty1.service >/dev/null 2>&1 || true
# start now on a running machine (not in the image build's chroot)
if [ -d /run/systemd/system ] && ! systemd-detect-virt --chroot >/dev/null 2>&1; then
  systemctl restart seatd.service neoden-kiosk.service || true
fi
echo "kiosk installed: neoden-kiosk.service, settings $INI"
