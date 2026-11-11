#!/bin/bash
# Wi-Fi for any USB dongle. netplan's networkd backend cannot bring up Wi-Fi from a name pattern
# (`match: wl*` in 50-neoden.yaml gives no wpa_supplicant), so at every boot each wl* interface
# gets its own netplan file with its real name (wlx<mac> for USB dongles) and the network from
# /etc/neoden/wifi.env (WIFI_SSID, WIFI_PASS) or, if that is missing, from 50-neoden.yaml.
set -u
# the Network page (neoserver/wifi.py) keeps its networks in wifi.json and writes netplan itself
if [ -f /etc/neoden/wifi.json ]; then
    echo "wifi: managed by the Network page (/etc/neoden/wifi.json)"
    exit 0
fi
ENV=/etc/neoden/wifi.env
if [ -f "$ENV" ]; then
    . "$ENV"
else
    # SSID and password from the access-points block of 50-neoden.yaml
    read -r WIFI_SSID WIFI_PASS < <(python3 - <<'PY'
import re
t = open("/etc/netplan/50-neoden.yaml").read()
m = re.search(r'access-points:\s*\n\s*"?([^"\n:]+)"?:\s*\n\s*password:\s*"?([^"\n]*)"?', t)
print(m.group(1), m.group(2)) if m else print("", "")
PY
)
fi
[ -n "${WIFI_SSID:-}" ] || { echo "wifi: no network configured"; exit 0; }
changed=0
for dev in /sys/class/net/wl*; do
    [ -e "$dev" ] || continue
    ifc=$(basename "$dev")
    f=/etc/netplan/60-wifi-$ifc.yaml
    want=$(cat <<EOF
network:
  version: 2
  renderer: networkd
  wifis:
    $ifc:
      dhcp4: true
      optional: true
      regulatory-domain: IR
      access-points:
        "$WIFI_SSID":
          password: "$WIFI_PASS"
EOF
)
    if [ "$(cat "$f" 2>/dev/null)" != "$want" ]; then
        printf '%s\n' "$want" > "$f"
        chmod 600 "$f"
        changed=1
        echo "wifi: $ifc -> $WIFI_SSID"
    fi
done
if [ "$changed" = 1 ]; then
    netplan generate && netplan apply
fi
exit 0
