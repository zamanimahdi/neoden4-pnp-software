#!/bin/sh
# 10-neomach.sh - mainboard serial port (NeoDen4.exe's COM2 = /dev/ttyS1), checked at boot.
# Nothing here moves the machine: it frees the port, sets it up and reads the status once
# so the journal shows whether the mainboard answers.
PORT=${NEOMACH_PORT:-/dev/ttyS1}
NAME=$(basename "$PORT")

if [ ! -e "$PORT" ]; then
    echo "neomach: $PORT does not exist - is COM2 enabled in the BIOS?"
    exit 1
fi

# Nobody else may open the port: a login console would answer the mainboard and
# ModemManager probes serial ports with AT commands.
systemctl mask --now "serial-getty@$NAME.service" >/dev/null 2>&1
if systemctl is-active --quiet ModemManager; then
    echo "neomach: stopping ModemManager (it probes serial ports)"
    systemctl disable --now ModemManager >/dev/null 2>&1
fi
if command -v fuser >/dev/null 2>&1 && fuser "$PORT" >/dev/null 2>&1; then
    echo "neomach: $PORT is already open by: $(fuser "$PORT" 2>&1)"
fi

# Group dialout read/write (the service user must be in dialout); low latency so each
# answer byte is delivered at once (neomach also asks for it when it opens the port).
chgrp dialout "$PORT" && chmod 660 "$PORT"
command -v setserial >/dev/null 2>&1 && setserial "$PORT" low_latency && setserial -g "$PORT"

# One status read: shows in the journal whether the mainboard answers and if XY is homed.
if timeout 15 /usr/bin/python3 -m neomach --url "serial://$PORT" status; then
    echo "neomach: mainboard answers on $PORT"
else
    echo "neomach: no answer from the mainboard on $PORT (machine off? cable?) - neoserver starts anyway"
    exit 1
fi
