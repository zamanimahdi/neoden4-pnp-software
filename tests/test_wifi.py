"""neoserver.wifi: iw output parsing, the netplan file it writes, connect / forget with a fake shell.

    python tests/test_wifi.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from neoserver.wifi import Wifi, netplan_networks, netplan_text, parse_link, parse_scan  # noqa: E402

SCAN = """BSS aa:bb:cc:dd:ee:01(on wlx00c0ca123456)
\tfreq: 2437
\tcapability: ESS Privacy ShortSlotTime (0x0411)
\tsignal: -48.00 dBm
\tSSID: Workshop
\tRSN:\t * Version: 1
BSS aa:bb:cc:dd:ee:02(on wlx00c0ca123456)
\tfreq: 2462
\tcapability: ESS ShortSlotTime (0x0401)
\tsignal: -71.00 dBm
\tSSID: Guest
BSS aa:bb:cc:dd:ee:03(on wlx00c0ca123456)
\tfreq: 5180
\tcapability: ESS Privacy (0x0011)
\tsignal: -60.00 dBm
\tSSID: Workshop
\tRSN:\t * Version: 1
BSS aa:bb:cc:dd:ee:04(on wlx00c0ca123456)
\tfreq: 2412
\tsignal: -80.00 dBm
\tSSID:
"""

LINK = """Connected to aa:bb:cc:dd:ee:01 (on wlx00c0ca123456)
\tSSID: Workshop
\tfreq: 2437
\tsignal: -47 dBm
\ttx bitrate: 72.2 MBit/s
"""

IMAGE_NETPLAN = """network:
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
        "Office 2G":
          password: "pa$$word"
"""


def test_parse():
    nets = parse_scan(SCAN)
    assert [n["ssid"] for n in nets] == ["Workshop", "Guest"], nets          # one row per SSID, hidden one dropped
    assert nets[0]["signal_dbm"] == -48 and nets[0]["secure"] and nets[0]["freq"] == 2437
    assert not nets[1]["secure"]
    link = parse_link(LINK)
    assert link == {"connected": True, "ssid": "Workshop", "signal_dbm": -47, "freq": 2437}
    assert not parse_link("Not connected.\n")["connected"]
    print("parse ok")


def test_netplan():
    assert netplan_networks(IMAGE_NETPLAN) == [{"ssid": "Office 2G", "password": "pa$$word"}]
    nets = [{"ssid": 'say "hi"', "password": "back\\slash1"}, {"ssid": "Open", "password": ""}, {"ssid": "Hid", "password": "12345678", "hidden": True}]
    t = netplan_text(nets, ["wlx002e2d50334f"])
    assert t.startswith(IMAGE_NETPLAN.split("  wifis:")[0]) and "regulatory-domain: IR" in t
    assert "    wlx002e2d50334f:\n" in t and "match" not in t.split("wifis:")[1]     # real name, no match: (networkd)
    back = netplan_networks(t)
    assert back == [nets[0], nets[1], nets[2]], back
    two = netplan_text(nets, ["wlan0", "wlx1"])
    assert two.count("access-points:") == 2 and netplan_networks(two) == back      # same list on each adapter
    assert "wifis" not in netplan_text([], ["wlan0"]) and "wifis" not in netplan_text(nets, [])
    print("netplan ok")


def test_connect_forget():
    d = tempfile.mkdtemp()
    plan, store = os.path.join(d, "50-neoden.yaml"), os.path.join(d, "neoden", "wifi.json")
    open(plan, "w").write(IMAGE_NETPLAN)
    calls = []

    def run(cmd, timeout=20):
        calls.append(cmd)
        return 0, ""
    open(os.path.join(d, "60-wifi-wlx1.yaml"), "w").write("old")             # boot.d/20-wifi.sh's file
    w = Wifi(log=lambda t: None, netplan=plan, store=store, run=run, env=os.path.join(d, "none.env"))
    w.ifaces = lambda: ["wlx1"]
    assert [n["ssid"] for n in w.known()] == ["Office 2G"]                   # first use: from the image's netplan
    r = w.connect("Workshop", "secret123")
    assert r["known"] == ["Workshop", "Office 2G"]
    assert not os.path.exists(os.path.join(d, "60-wifi-wlx1.yaml"))         # one owner of Wi-Fi
    assert "    wlx1:" in open(plan).read()
    nets = netplan_networks(open(plan).read())
    assert nets[0] == {"ssid": "Workshop", "password": "secret123", "hidden": False} or nets[0]["password"] == "secret123"
    assert nets[1]["password"] == "pa$$word"
    # reconnect to a known network without the password: the stored one is kept, it moves to the top
    w.connect("Office 2G")
    assert [n["ssid"] for n in w.known()] == ["Office 2G", "Workshop"] and w.known()[0]["password"] == "pa$$word"
    for bad in (("", "x"), ("a" * 33, ""), ("ok", "short")):
        try:
            w.connect(*bad)
            raise AssertionError(f"accepted {bad}")
        except ValueError:
            pass
    w.forget("Workshop")
    assert [n["ssid"] for n in w.known()] == ["Office 2G"]
    time.sleep(1.5)                                                          # netplan apply runs after the reply
    assert ["netplan", "apply"] in calls
    if os.name != "nt":
        assert oct(os.stat(plan).st_mode & 0o777) == "0o600"
    # a second dongle plugged in later: ensure() rewrites the file for it (status calls it)
    w.ifaces = lambda: ["wlx1", "wlx2"]
    assert w.ensure() and "    wlx2:" in open(plan).read() and not w.ensure()
    # no adapter: the networks stay saved, the file has Ethernet only
    w.ifaces = lambda: []
    assert "no Wi-Fi adapter" in w.connect("Later", "abcdefgh")["note"]
    assert "wifis" not in open(plan).read() and w.known()[0]["ssid"] == "Later"
    # first use from boot.d/20-wifi.sh's wifi.env
    envf = os.path.join(d, "wifi.env")
    open(envf, "w").write("WIFI_SSID='Shop Floor'\nWIFI_PASS='p4ssw0rd!'\n")
    w2 = Wifi(log=lambda t: None, netplan=plan, store=os.path.join(d, "x", "wifi.json"), run=run, env=envf)
    assert w2.known() == [{"ssid": "Shop Floor", "password": "p4ssw0rd!"}], w2.known()
    # status / scan without an adapter: clear message
    w.iface = lambda: None
    assert w.status()["iface"] is None and "dongle" in w.status()["note"]
    print("connect / forget ok")


def test_scan_status_with_fake_iw():
    def run(cmd, timeout=20):
        if cmd[-1] == "scan":
            return 0, SCAN
        if cmd[-1] == "link":
            return 0, LINK
        if cmd[:2] == ["ip", "-4"]:
            return 0, "4: wlx00c0ca123456    inet 192.168.1.57/24 brd 192.168.1.255 scope global dynamic\n"
        return 0, ""
    d = tempfile.mkdtemp()
    w = Wifi(log=lambda t: None, netplan=os.path.join(d, "n.yaml"), store=os.path.join(d, "w.json"), run=run)
    w.iface = lambda: "wlx00c0ca123456"
    w.ifaces = lambda: ["wlx00c0ca123456"]
    s = w.status()
    assert s["connected"] and s["ssid"] == "Workshop" and s["ip"] == "192.168.1.57", s
    sc = w.scan()
    assert sc["networks"][0]["connected"] and not sc["networks"][1]["connected"]
    print("scan / status ok")


if __name__ == "__main__":
    test_parse()
    test_netplan()
    test_connect_forget()
    test_scan_status_with_fake_iw()
    print("all ok")
