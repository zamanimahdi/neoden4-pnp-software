#!/bin/sh
# Builds the NeoDen machine disk (Ubuntu 24.04 + neoserver) on /dev/vda.
# Runs as root inside the Alpine live VM started by build.py; files come from the host at $SRC.
set -eu
SRC="$1"                                  # http://10.0.2.2:PORT
DISK=/dev/vda
MIRROR=http://archive.ubuntu.com/ubuntu
log() { echo "### $*"; }

log "tools"
apk add --no-cache e2fsprogs sfdisk util-linux wget tar xz >/dev/null

log "partition $DISK (MBR, one ext4 root, BIOS boot)"
echo 'label: dos
start=2048, type=83, bootable' | sfdisk -q "$DISK"
mdev -s 2>/dev/null || true
sleep 1
mkfs.ext4 -q -F -L neoden-root "${DISK}1"
mkdir -p /mnt/t
mount "${DISK}1" /mnt/t

log "ubuntu base"
wget -q -O /tmp/base.tgz "$SRC/ubuntu-base-24.04.5-base-amd64.tar.gz"
tar -xzf /tmp/base.tgz -C /mnt/t
rm /tmp/base.tgz

log "files from the host"
wget -q -O /tmp/payload.tgz "$SRC/payload.tgz"
mkdir -p /mnt/t/tmp/payload
tar -xzf /tmp/payload.tgz -C /mnt/t/tmp/payload

cp /etc/resolv.conf /mnt/t/etc/resolv.conf
for d in dev proc sys; do mount --bind /$d /mnt/t/$d; done
mount -t devpts devpts /mnt/t/dev/pts 2>/dev/null || true

log "configure inside Ubuntu"
chroot /mnt/t /bin/bash /tmp/payload/setup-chroot.sh "$MIRROR" "$DISK" "$SRC"

log "clean up"
for d in dev/pts dev proc sys; do umount /mnt/t/$d 2>/dev/null || true; done
rm -rf /mnt/t/tmp/payload
sync
umount /mnt/t
log "DONE"
