#!/bin/bash
# One-time password change for user neo: put the new password into /etc/neoden/neo-password
# (for example with the web UI's file API, which runs as root); at the next boot it is set
# and the file is deleted.
F=/etc/neoden/neo-password
[ -s "$F" ] || exit 0
pw=$(head -n1 "$F" | tr -d '\r\n')
if [ -n "$pw" ] && echo "neo:$pw" | chpasswd; then
    echo "neo: password changed"
else
    echo "neo: password NOT changed"
fi
shred -u "$F" 2>/dev/null || rm -f "$F"
exit 0
