"""Wi-Fi on the machine PC (Ubuntu, netplan + systemd-networkd + wpa_supplicant), for the web UI's
Network page: scan, status, connect to a network, forget one.

The machine PC has no Wi-Fi of its own; this is for a USB dongle (docs/MACHINE_PC.md). The known
networks are kept in /etc/neoden/wifi.json (root, 0600). Connecting writes them all into
/etc/netplan/50-neoden.yaml (the file the image build writes, with the same Ethernet section) and
runs `netplan apply` a moment after the reply, so the browser gets its answer before the network
restarts. Wired Ethernet stays as it is. Passwords are never sent back to the browser.

netplan's networkd backend starts wpa_supplicant only for a Wi-Fi entry named after a real
interface (a `match: wl*` entry never connects), so there is one entry per wl* interface present
(wlx<mac> for USB dongles). boot.d/20-wifi.sh steps aside once wifi.json exists, and its
/etc/netplan/60-wifi-*.yaml files are removed here. ensure() rewrites the file when a dongle
appears that the file does not name yet (called at server start and on every status).

    w = Wifi(log)
    w.status()                 # {iface, connected, ssid, signal_dbm, freq, ip, known: [...]}
    w.scan()                   # {iface, networks: [{ssid, signal_dbm, freq, secure, known, connected}]}
    w.connect("lab", "secret") # saves it first in the list, applies netplan
    w.forget("lab")
"""
import json
import os
import re
import subprocess
import threading
import time

NETPLAN = "/etc/netplan/50-neoden.yaml"
STORE = "/etc/neoden/wifi.json"
ENV = "/etc/neoden/wifi.env"                  # boot.d/20-wifi.sh's network (first use only)
REG_DOMAIN = "IR"

ETHERNET = """network:
  version: 2
  renderer: networkd
  ethernets:
    all-eth:
      match: {name: "en*"}
      dhcp4: true
      optional: true
"""


def _q(s):
    """YAML double-quoted scalar (netplan reads YAML; SSIDs and passwords may hold anything)."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def netplan_text(networks, ifaces):
    """The whole netplan file: Ethernet DHCP + every known Wi-Fi network (first = preferred) on each
    Wi-Fi interface by its real name. No interface or no network: Ethernet only."""
    t = ETHERNET
    if not networks or not ifaces:
        return t
    t += "  wifis:\n"
    for dev in ifaces:
        t += (f"    {dev}:\n      dhcp4: true\n      optional: true\n"
              f"      regulatory-domain: {REG_DOMAIN}\n      access-points:\n")
        for n in networks:
            t += f"        {_q(n['ssid'])}:\n"
            if n.get("password"):
                t += f"          password: {_q(n['password'])}\n"
            if n.get("hidden"):
                t += "          hidden: true\n"
    return t


def netplan_networks(text):
    """Known networks from a netplan file written by the image build or by netplan_text() (the
    networks of the first Wi-Fi entry; every entry lists the same ones)."""
    out, cur, seen = [], None, set()
    for line in text.splitlines():
        m = re.match(r'^ {8}"((?:[^"\\]|\\.)*)":\s*$', line)
        if m:
            ssid = m.group(1).replace('\\"', '"').replace("\\\\", "\\")
            cur = {"ssid": ssid, "password": ""}
            if ssid not in seen:
                seen.add(ssid)
                out.append(cur)
            continue
        m = re.match(r'^ {10}password:\s*"((?:[^"\\]|\\.)*)"\s*$', line)
        if m and cur is not None:
            cur["password"] = m.group(1).replace('\\"', '"').replace("\\\\", "\\")
        elif re.match(r"^ {10}hidden:\s*true", line) and cur is not None:
            cur["hidden"] = True
    return out


def parse_scan(text):
    """`iw dev X scan` -> [{ssid, signal_dbm, freq, secure}], strongest first, one row per SSID."""
    nets, cur = {}, None
    for line in text.splitlines():
        if line.startswith("BSS "):
            cur = {"ssid": "", "signal_dbm": None, "freq": None, "secure": False}
            nets.setdefault(id(cur), cur)
            continue
        if cur is None:
            continue
        s = line.strip()
        if s.startswith("SSID:"):
            cur["ssid"] = s[5:].strip()
        elif s.startswith("signal:"):
            m = re.search(r"(-?\d+(?:\.\d+)?)", s)
            cur["signal_dbm"] = float(m.group(1)) if m else None
        elif s.startswith("freq:"):
            m = re.search(r"(\d+(?:\.\d+)?)", s)
            cur["freq"] = int(float(m.group(1))) if m else None
        elif s.startswith("RSN:") or s.startswith("WPA:") or ("capability:" in s and "Privacy" in s):
            cur["secure"] = True
    best = {}
    for n in nets.values():
        if not n["ssid"] or "\\x00" in n["ssid"]:
            continue
        o = best.get(n["ssid"])
        if o is None or (n["signal_dbm"] or -999) > (o["signal_dbm"] or -999):
            best[n["ssid"]] = n
    return sorted(best.values(), key=lambda n: -(n["signal_dbm"] or -999))


def parse_link(text):
    """`iw dev X link` -> {connected, ssid, signal_dbm, freq}."""
    if "Not connected" in text or not text.strip():
        return {"connected": False, "ssid": "", "signal_dbm": None, "freq": None}
    d = {"connected": True, "ssid": "", "signal_dbm": None, "freq": None}
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("SSID:"):
            d["ssid"] = s[5:].strip()
        elif s.startswith("signal:"):
            m = re.search(r"(-?\d+)", s)
            d["signal_dbm"] = int(m.group(1)) if m else None
        elif s.startswith("freq:"):
            m = re.search(r"(\d+)", s)
            d["freq"] = int(m.group(1)) if m else None
    return d


class Wifi:
    def __init__(self, log=print, netplan=NETPLAN, store=STORE, run=None, env=ENV):
        self.log, self.netplan, self.store, self.env = log, netplan, store, env
        self.run = run or self._run
        self.lock = threading.Lock()

    @staticmethod
    def _run(cmd, timeout=20):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr

    def ifaces(self):
        try:
            return [n for n in sorted(os.listdir("/sys/class/net"))
                    if os.path.isdir(f"/sys/class/net/{n}/wireless") or os.path.isdir(f"/sys/class/net/{n}/phy80211")]
        except OSError:
            return []

    def iface(self):
        f = self.ifaces()
        return f[0] if f else None

    # ---- known networks ----
    def known(self):
        try:
            with open(self.store, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
        try:                                                      # first use: what boot.d/20-wifi.sh used
            env = {}
            with open(self.env, encoding="utf-8") as f:
                for line in f:
                    k, _, v = line.strip().partition("=")
                    env[k] = v.strip().strip("'\"")
            if env.get("WIFI_SSID"):
                return [{"ssid": env["WIFI_SSID"], "password": env.get("WIFI_PASS", "")}]
        except OSError:
            pass
        try:                                                      # ... or the networks the image was built with
            with open(self.netplan, encoding="utf-8") as f:
                return netplan_networks(f.read())
        except OSError:
            return []

    def _save(self, nets):
        os.makedirs(os.path.dirname(self.store), exist_ok=True)
        for path, data in ((self.store, json.dumps(nets, indent=1)), (self.netplan, netplan_text(nets, self.ifaces()))):
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(data)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        d = os.path.dirname(self.netplan)                        # boot.d/20-wifi.sh's files: one owner only
        try:
            for f in os.listdir(d):
                if f.startswith("60-wifi-") and f.endswith(".yaml"):
                    os.remove(os.path.join(d, f))
        except OSError:
            pass

    def ensure(self):
        """A Wi-Fi interface the netplan file does not name yet (dongle plugged in later): rewrite the
        file for it and apply. Only once the Network page owns Wi-Fi (wifi.json exists)."""
        if not os.path.exists(self.store):
            return False
        try:
            with open(self.netplan, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            text = ""
        missing = [d for d in self.ifaces() if f"    {d}:" not in text]
        if not missing:
            return False
        with self.lock:
            self._save(self.known())
        self.log(f"wifi: new adapter {', '.join(missing)}: netplan rewritten")
        self._apply_later()
        return True

    def _apply_later(self, delay=1.0):
        def go():
            time.sleep(delay)
            code, out = self.run(["netplan", "apply"], 60)
            self.log(f"wifi: netplan apply -> {code} {out.strip()[:200]}")
        threading.Thread(target=go, daemon=True).start()

    # ---- API ----
    def status(self, q=None):
        self.ensure()
        dev = self.iface()
        d = {"iface": dev, "connected": False, "ssid": "", "signal_dbm": None, "freq": None, "ip": None,
             "known": [n["ssid"] for n in self.known()]}
        if not dev:
            d["note"] = "no Wi-Fi adapter (plug in a USB Wi-Fi dongle that Linux supports)"
            return d
        code, out = self.run(["iw", "dev", dev, "link"])
        d.update(parse_link(out))
        code, out = self.run(["ip", "-4", "-o", "addr", "show", "dev", dev])
        m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
        d["ip"] = m.group(1) if m else None
        return d

    def scan(self, q=None):
        dev = self.iface()
        if not dev:
            raise RuntimeError("no Wi-Fi adapter (plug in a USB Wi-Fi dongle that Linux supports)")
        self.run(["ip", "link", "set", dev, "up"])
        code, out = self.run(["iw", "dev", dev, "scan"], 30)
        if code != 0 and "busy" in out.lower():               # a scan is already running: take its results
            time.sleep(2)
            code, out = self.run(["iw", "dev", dev, "scan", "dump"], 15)
        if code != 0:
            raise RuntimeError(f"scan failed: {out.strip()[:200]}")
        known = {n["ssid"] for n in self.known()}
        link = parse_link(self.run(["iw", "dev", dev, "link"])[1])
        nets = parse_scan(out)
        for n in nets:
            n["known"] = n["ssid"] in known
            n["connected"] = link["connected"] and n["ssid"] == link["ssid"]
        return {"iface": dev, "networks": nets}

    def connect(self, ssid, password="", hidden=False):
        ssid = (ssid or "").strip()
        if not ssid or len(ssid.encode()) > 32:
            raise ValueError("SSID: 1 to 32 bytes")
        if password and not 8 <= len(password) <= 63:
            raise ValueError("WPA password: 8 to 63 characters (empty for an open network)")
        with self.lock:
            nets = [n for n in self.known() if n["ssid"] != ssid]
            old = next((n for n in self.known() if n["ssid"] == ssid), None)
            if not password and old and old.get("password"):
                password = old["password"]                    # reconnect to a known network without retyping
            nets.insert(0, {"ssid": ssid, "password": password, "hidden": bool(hidden)})
            self._save(nets)
        self.log(f"wifi: connect to {ssid!r} (netplan applied in a moment)")
        self._apply_later()
        note = ("applying: the machine joins the network in a few seconds; wired Ethernet is not touched" if self.ifaces()
                else "saved; no Wi-Fi adapter now: it connects when a dongle is plugged in")
        return {"ssid": ssid, "known": [n["ssid"] for n in nets], "note": note}

    def forget(self, ssid):
        with self.lock:
            nets = [n for n in self.known() if n["ssid"] != ssid]
            self._save(nets)
        self.log(f"wifi: forgot {ssid!r}")
        self._apply_later()
        return {"known": [n["ssid"] for n in nets]}
