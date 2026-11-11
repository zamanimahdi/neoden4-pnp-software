#!/bin/bash
# Runs inside the Ubuntu 24.04 root (chroot) on the target disk. Settings come from
# /tmp/payload/settings.env (written by build.py): user password, update key, Wi-Fi.
set -euo pipefail
MIRROR="$1"; DISK="$2"; SRC="$3"
P=/tmp/payload
source $P/settings.env
export DEBIAN_FRONTEND=noninteractive
log() { echo "### $*"; }

log "apt sources ($MIRROR)"
cat > /etc/apt/sources.list.d/ubuntu.sources <<EOF
Types: deb
URIs: $MIRROR
Suites: noble noble-updates noble-security
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF
rm -f /etc/apt/sources.list
echo 'Acquire::Retries "10";' > /etc/apt/apt.conf.d/80retries
apt-get update -q

log "packages"
echo "grub-pc grub-pc/install_devices multiselect $DISK" | debconf-set-selections
apt-get install -y -q --no-install-recommends \
  systemd-sysv systemd-resolved udev kmod dbus init-system-helpers \
  linux-image-generic linux-firmware initramfs-tools grub-pc \
  netplan.io wpasupplicant iw wireless-regdb iproute2 iputils-ping ca-certificates \
  openssh-server avahi-daemon libnss-mdns vsftpd sudo nano less rsync tzdata locales \
  python3 python3-numpy python3-opencv python3-usb python3-serial \
  usbutils pciutils setserial psmisc

log "system"
echo neoden > /etc/hostname
printf '127.0.0.1 localhost\n127.0.1.1 neoden\n' > /etc/hosts
ln -sf /usr/share/zoneinfo/Asia/Tehran /etc/localtime
locale-gen en_US.UTF-8 >/dev/null
echo 'LANG=en_US.UTF-8' > /etc/default/locale
ROOTUUID=$(blkid -s UUID -o value ${DISK}1)
echo "UUID=$ROOTUUID / ext4 defaults,noatime,errors=remount-ro 0 1" > /etc/fstab
systemctl enable systemd-networkd systemd-resolved ssh avahi-daemon vsftpd >/dev/null

log "user neo"
id neo >/dev/null 2>&1 || useradd -m -s /bin/bash -G sudo,dialout,plugdev neo
echo "neo:$NEO_PASSWORD" | chpasswd
passwd -l root >/dev/null

log "network: Ethernet DHCP + Wi-Fi dongle (DHCP)"
cat > /etc/netplan/50-neoden.yaml <<EOF
network:
  version: 2
  renderer: networkd
  ethernets:
    all-eth:
      match: {name: "en*"}
      dhcp4: true
      optional: true
  wifis:
    all-wifi:
      match: {name: "wl*"}
      dhcp4: true
      optional: true
      regulatory-domain: IR
      access-points:
        "$WIFI_SSID":
          password: "$WIFI_PASS"
EOF
chmod 600 /etc/netplan/50-neoden.yaml

log "ssh + ftp"
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
cat > /etc/vsftpd.conf <<EOF
listen=YES
listen_ipv6=NO
anonymous_enable=NO
local_enable=YES
write_enable=YES
local_umask=022
utf8_filesystem=YES
pasv_enable=YES
pasv_min_port=40000
pasv_max_port=40100
EOF

log "neoden software (packed by the host now: the newest code)"
python3 -c "import urllib.request,sys; open('/tmp/app.tgz','wb').write(urllib.request.urlopen(sys.argv[1]+'/app.tgz', timeout=600).read())" "$SRC"
tar -xzf /tmp/app.tgz -C $P && rm /tmp/app.tgz
mkdir -p /opt/neoden /etc/neoden /opt/neoden/data /opt/neoden/backup
rm -rf /opt/neoden/app
cp -a $P/app /opt/neoden/app
cp -a $P/data/. /opt/neoden/data/ 2>/dev/null || true
cat > /etc/neoden/neoserver.ini <<EOF
; neoserver settings (restart neoserver after editing: sudo systemctl restart neoserver)
[server]
http_port = 80
bind = 0.0.0.0
login = no
update_key = $UPDATE_KEY
www = /opt/neoden/app/neoagent/www
backup = /opt/neoden/backup
neoden_dir = /opt/neoden/data
camera = usb
machine = serial:///dev/ttyS1
services = neoserver,ssh,vsftpd,avahi-daemon
home = /opt/neoden/app
EOF
chmod 600 /etc/neoden/neoserver.ini
chown -R neo:neo /opt/neoden/data
log "kiosk (web UI on the machine screen, docs/KIOSK.md)"
bash /opt/neoden/app/neokiosk/install.sh

cat > /etc/systemd/system/neoserver.service <<EOF
[Unit]
Description=NeoDen4 machine server (web UI, API, cameras, machine)
After=network.target
OnFailure=neoserver-rollback.service
StartLimitIntervalSec=120
StartLimitBurst=3

[Service]
ExecStart=/usr/bin/python3 -m neoserver --config /etc/neoden/neoserver.ini
WorkingDirectory=/opt/neoden/app
Restart=on-failure
RestartSec=3
Nice=-5

[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/neoserver-rollback.service <<EOF
[Unit]
Description=Undo the last neoserver update after a failed start

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -m neoserver.rollback --config /etc/neoden/neoserver.ini
WorkingDirectory=/opt/neoden/app
EOF
# boot hook: /opt/neoden/app/boot.d/*.sh once at every boot, before neoserver (boot.d/README.md)
cat > /usr/local/sbin/neoden-boot <<'EOS'
#!/bin/bash
cd /opt/neoden/app || exit 0
for f in boot.d/*.sh; do
  [ -f "$f" ] || continue
  chmod +x "$f" 2>/dev/null
  echo "boot.d: $f"
  timeout 120 "$f" || echo "boot.d: $f failed ($?)"
done
exit 0
EOS
chmod 755 /usr/local/sbin/neoden-boot
cat > /etc/systemd/system/neoden-boot.service <<EOF
[Unit]
Description=NeoDen boot scripts (/opt/neoden/app/boot.d)
After=local-fs.target network.target
Before=neoserver.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/neoden-boot
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
systemctl enable neoserver neoden-boot >/dev/null

# cameras (Cypress FX3, VID 04b4) and the serial port for the server (runs as root) and user neo
cat > /etc/udev/rules.d/60-neoden.rules <<EOF
SUBSYSTEM=="usb", ATTR{idVendor}=="04b4", MODE="0666"
SUBSYSTEM=="usb", ATTR{idVendor}=="52cb", MODE="0666"
KERNEL=="ttyS1", GROUP="dialout", MODE="0660"
EOF

log "boot loader"
sed -i 's/^GRUB_CMDLINE_LINUX_DEFAULT=.*/GRUB_CMDLINE_LINUX_DEFAULT=""/; s/^GRUB_TIMEOUT=.*/GRUB_TIMEOUT=2/' /etc/default/grub
# no serial console: COM1/COM2 belong to the machine (the mainboard is on ttyS1)
update-initramfs -u -k all
grub-install --target=i386-pc "$DISK"
update-grub
apt-get clean
log "chroot done"
