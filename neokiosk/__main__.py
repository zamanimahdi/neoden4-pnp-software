"""neokiosk: a full-screen WebKit2GTK window on the web UI, run inside the cage compositor.

    python3 -m neokiosk [--config /etc/neoden/kiosk.ini] [--cage | --screens]

--cage: set up the compositor's environment and exec `cage -- python3 -m neokiosk`
(this is what neoden-kiosk.service runs on tty1). Without it: only the window.
--screens: switch off the outputs not used (no monitor behind them; root, before cage).

The window waits for neoserver (an "IP / starting" page, then retries every 2 s),
passes every key to the page (arrow keys jog on the Manual page) except:
  F5 / Ctrl+R  reload       Ctrl+plus / Ctrl+minus / Ctrl+0  zoom
"""
import argparse
import configparser
import glob
import os
import socket
import subprocess
import sys
import time

CONFIG = "/etc/neoden/kiosk.ini"
DEFAULTS = {
    "url": "http://localhost/#job",   # the mount screen after every start
    "zoom": "1.0",
    "output": "auto",          # auto: only outputs with a monitor (EDID); all; or names: VGA-1
    "fallback_mode": "",       # monitor gives no EDID and none saved: 1280x1024, 1024x768 or 1920x1080
    "rotate": "normal",        # normal, 90, 180, 270 (clockwise), flipped...: wlr-randr --transform
    "hide_cursor": "auto",     # auto: hide only with a touch screen and no mouse; yes; no
    "renderer": "pixman",      # cage/wlroots renderer: pixman (CPU, safest on the GMA 3150) or gles2
    "gpu": "no",               # yes: WebKit draws with the GPU (OpenGL); no: CPU only
    "lite": "yes",             # yes: no shadows / transitions (each repaint is cheaper on the CPU)
    "watch": "auto",           # reload when the UI files change: auto (neoagent/www next to this), a folder, no
    "mouse_speed": "1.0",      # mouse pointer speed: 1 = as the mouse is, 2 = twice as fast, 0.5 = half
    "theme": "auto",           # the pages' colours on this screen: dark, light, auto (as the system: light here)
}
HWDB = "/etc/udev/hwdb.d/71-neoden-mouse.hwdb"
RETRY_S = 2
WATCH_S = 2


def stamp(folder):
    """(sum of mtimes, file count, total size) of the web UI files: changes when an update lands
    (a sum, not the newest: copied files can carry times ahead of this PC's clock)"""
    times = n = size = 0
    for root, _dirs, files in os.walk(folder):
        for f in files:
            try:
                st = os.stat(os.path.join(root, f))
            except OSError:
                continue
            times, n, size = times + st.st_mtime_ns, n + 1, size + st.st_size
    return times, n, size


def watched(cfg):
    """the web UI folder to watch, when the kiosk shows this PC's own server"""
    w = cfg["watch"].strip()
    if w.lower() in ("no", "off", "0", "false"):
        return None
    if w.lower() in ("", "auto"):
        host = (cfg["url"].split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]).lower()
        if host not in ("localhost", "127.0.0.1", "::1", "[::1]", socket.gethostname().lower()):
            return None
        w = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "neoagent", "www")
    return w if os.path.isdir(w) else None


def settings(path):
    c = configparser.ConfigParser()
    c.read_dict({"kiosk": DEFAULTS})
    c.read(path)
    return c["kiosk"]


def yes(v):
    return str(v).strip().lower() in ("1", "yes", "true", "on")


def run_cage(cfg, config_path):
    env = dict(os.environ)
    env.setdefault("XDG_SESSION_TYPE", "wayland")
    if cfg["renderer"] not in ("", "auto"):
        env["WLR_RENDERER"] = cfg["renderer"]
    env["NO_AT_BRIDGE"] = "1"                       # no accessibility bus (no desktop here)
    args = ["cage", "-d", "-s", "--", sys.executable, "-m", "neokiosk", "--config", config_path]
    os.execvpe("cage", args, env)


EDID_DIR = "/var/lib/neoden-kiosk/edid"         # the last EDID read from each monitor
INTERNAL = ("LVDS", "eDP", "DSI")                # panel connectors (a phantom LVDS on the D525)


def _put(path, data):
    with open(path, "wb" if isinstance(data, bytes) else "w") as f:
        f.write(data)


def _edid(d):
    try:
        return open(d + "/edid", "rb").read()
    except OSError:
        return b""


NEO_ID = ((14 << 10) | (5 << 5) | 15).to_bytes(2, "big")     # EDID maker "NEO": make_edid()
# VESA DMT timings: pixel clock kHz, h active/front/sync/back, v active/front/sync/back, +h/+v sync
DMT = {"1024x768": (65000, 1024, 24, 136, 160, 768, 3, 6, 29, 0, 0),
       "1280x1024": (108000, 1280, 48, 112, 248, 1024, 1, 3, 38, 1, 1),
       "1920x1080": (148500, 1920, 88, 44, 148, 1080, 4, 5, 36, 1, 1)}


def make_edid(mode):
    """a plain EDID 1.3 for an analog monitor with one mode (DMT), for monitors that do not
    answer on the cable: the kernel then offers that mode instead of 1024x768"""
    clk, ha, hf, hs, hb, va, vf, vs, vb, hp, vp = DMT[mode]
    hbl, vbl = hf + hs + hb, vf + vs + vb
    e = bytearray(128)
    e[0:8] = bytes.fromhex("00ffffffffffff00")
    e[8:10] = NEO_ID
    e[10:12] = (1).to_bytes(2, "little")
    e[16], e[17], e[18], e[19] = 1, 2026 - 1990, 1, 3
    e[20], e[21], e[22], e[23], e[24] = 0x08, 38, 30, 120, 0x0A        # analog, 38x30 cm, gamma 2.2
    e[25:35] = bytes.fromhex("ee91a3544c99260f5054")                  # sRGB-like colours
    e[35], e[36] = 0x21, 0x08                                           # 640x480, 800x600, 1024x768
    e[38:54] = bytes([1]) * 16                                         # no standard timings
    d = bytearray(18)
    d[0:2] = (clk // 10).to_bytes(2, "little")
    d[2], d[3], d[4] = ha & 0xFF, hbl & 0xFF, ((ha >> 8) << 4) | (hbl >> 8)
    d[5], d[6], d[7] = va & 0xFF, vbl & 0xFF, ((va >> 8) << 4) | (vbl >> 8)
    d[8], d[9], d[10] = hf & 0xFF, hs & 0xFF, ((vf & 15) << 4) | (vs & 15)
    d[11] = ((hf >> 8) << 6) | ((hs >> 8) << 4) | ((vf >> 4) << 2) | (vs >> 4)
    d[12], d[13], d[14] = 380 & 0xFF, 300 & 0xFF, ((380 >> 8) << 4) | (300 >> 8)
    d[17] = 0x18 | (vp << 2) | (hp << 1)                                # digital separate sync
    e[54:72] = d
    e[72:90] = bytes.fromhex("000000fc00") + b"NeoDen VGA" + bytes.fromhex("0a2020")
    e[90:108] = bytes.fromhex("000000fd00") + bytes([50, 76, 30, 83, 15, 0, 10]) + b" " * 6
    e[108:126] = bytes.fromhex("0000001000") + bytes(13)
    e[127] = (-sum(e[:127])) & 0xFF
    return bytes(e)


def screens(want, fallback=""):
    """pick the monitor before cage starts (root: neoden-kiosk.service runs this as ExecStartPre=+).
    cage spreads the window over every connected output. The Atom D525 boards report an LVDS
    panel that is not there (connected, no EDID), and the VGA monitor's EDID (its name and
    modes, read over the cable) is sometimes not read at boot: then the kernel gives VGA
    1024x768 and both outputs looked alike, so the page was spread over 2048 px.
    - a connected output without EDID is probed again a few times; if it still has none
      and a monitor's EDID was saved from an earlier boot, that is used (debugfs
      edid_override), so the monitor keeps its own resolution;
    - EDIDs read are saved in EDID_DIR;
    - kept: outputs with EDID; else outside connectors (VGA, DVI, HDMI, DP) over panels; else all.
    The others are switched off (sysfs status "off") until the next boot. (Turning an output
    off in cage with wlr-randr crashes cage 0.1.5, and wlroots 0.17 has no connector filter.)"""
    want = want.strip()
    if want == "all":
        return
    names = want.replace(",", " ").split() if want not in ("", "auto") else None

    def connected():
        out = {}
        for d in sorted(glob.glob("/sys/class/drm/card*-*")):
            try:
                if open(d + "/status").read().strip() == "connected":
                    out[d] = _edid(d)
            except OSError:
                continue
        return out

    found = connected()
    os.makedirs(EDID_DIR, exist_ok=True)
    for d, edid in found.items():
        name = d.split("-", 1)[1]
        if edid[8:10] == NEO_ID:
            continue                                 # our own fallback (edid_override), not the monitor's
        if edid:
            try:
                if open(os.path.join(EDID_DIR, name + ".bin"), "rb").read() != edid:
                    raise OSError
            except OSError:
                _put(os.path.join(EDID_DIR, name + ".bin"), edid)
            continue
        if name.startswith(INTERNAL):
            continue
        for _ in range(3):                           # read the monitor again
            try:
                _put(d + "/status", "detect")
            except OSError:
                break
            time.sleep(1)
            if _edid(d):
                print("neokiosk: screen", name, "EDID read on a retry", file=sys.stderr)
                break
        else:
            saved = os.path.join(EDID_DIR, name + ".bin")
            card = d.rsplit("/", 1)[1].split("-", 1)[0][4:]       # card1-VGA-1 -> 1
            over = f"/sys/kernel/debug/dri/{card}/{name}/edid_override"
            edid, why = None, ""
            if os.path.isfile(saved):
                edid, why = open(saved, "rb").read(), "the one saved from an earlier boot"
            elif fallback.strip() in DMT:
                edid, why = make_edid(fallback.strip()), "a standard one for " + fallback.strip()
            if edid and os.path.exists(over):
                try:
                    _put(over, edid)
                    _put(d + "/status", "detect")
                    print("neokiosk: screen", name, "gives no EDID, using", why, file=sys.stderr)
                except OSError as e:
                    print("screens:", e, file=sys.stderr)
    found = connected()

    if names is not None:
        keep = [d for d in found if d.split("-", 1)[1] in names]
    else:
        keep = ([d for d, edid in found.items() if edid]
                or [d for d in found if not d.split("-", 1)[1].startswith(INTERNAL)])
    keep = keep or list(found)
    for d in found:
        if d not in keep:
            try:
                _put(d + "/status", "off")
                print("neokiosk: screen", d.split("-", 1)[1], "off", file=sys.stderr)
            except OSError as e:
                print("screens:", e, file=sys.stderr)


def mouse_speed(v):
    """the pointer speed (root, before cage): cage has no pointer setting, but libinput scales every
    mouse's motion to 1000 dpi by its MOUSE_DPI (udev hwdb): a lower DPI given = a faster pointer.
    1.0 removes ours (the mouse as it is). Returns the DPI set, or None"""
    import subprocess
    try:
        f = min(5.0, max(0.2, float(v)))
    except (TypeError, ValueError):
        f = 1.0
    dpi = None
    if abs(f - 1.0) < 0.01:
        if not os.path.exists(HWDB):
            return None
        os.remove(HWDB)
    else:
        dpi = max(100, int(round(1000 / f)))
        text = ("# neokiosk (kiosk.ini mouse_speed = %g): every mouse as %d dpi\n"
                "mouse:*:name:*:\n MOUSE_DPI=%d@125\n" % (f, dpi, dpi))
        try:
            if open(HWDB).read() == text:
                return dpi
        except OSError:
            pass
        os.makedirs(os.path.dirname(HWDB), exist_ok=True)
        _put(HWDB, text.encode())
    for cmd in (["systemd-hwdb", "update"], ["udevadm", "trigger", "--subsystem-match=input", "--action=change"],
                ["udevadm", "settle", "--timeout=3"]):
        try:
            subprocess.run(cmd, timeout=20, check=False)
        except (OSError, subprocess.SubprocessError) as e:
            print("neokiosk: mouse speed:", " ".join(cmd), e, file=sys.stderr)
    print(f"neokiosk: mouse speed {f:g}" + (f" ({dpi} dpi)" if dpi else ""), file=sys.stderr)
    return dpi


def rotate(cfg):
    """turn the screen with wlr-randr (cage supports output management)"""
    t = cfg["rotate"].strip()
    if t in ("", "normal", "0"):
        return
    try:
        out = subprocess.run(["wlr-randr"], capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            if line and not line[0].isspace():     # "HDMI-A-1 "maker model"" starts an output
                subprocess.run(["wlr-randr", "--output", line.split()[0], "--transform", t], timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        print("rotate:", e, file=sys.stderr)


def touch_only():
    """True when the udev database shows a touch screen and no mouse (or touchpad)"""
    touch = mouse = False
    for f in glob.glob("/run/udev/data/c13:*"):
        try:
            s = open(f, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        touch |= "E:ID_INPUT_TOUCHSCREEN=1" in s
        mouse |= "E:ID_INPUT_MOUSE=1" in s or "E:ID_INPUT_TOUCHPAD=1" in s
    return touch and not mouse


def addresses():
    """this PC's LAN addresses, for the waiting page"""
    try:
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5).stdout
        return out.split()
    except (OSError, subprocess.SubprocessError):
        return []


# the same look as the boot screen (splash/neoden.script): logo, running dots, dark background;
# the technical reason shows only after a while, small
WAITING = """<!doctype html><meta charset="utf-8"><title>NeoDen</title>
<style>html,body{{margin:0;height:100%;background:#1b1f24;color:#eceff4;font-family:sans-serif;cursor:default}}
body{{display:flex;flex-direction:column;align-items:center;justify-content:center}}
.logo{{font-weight:bold;font-size:96px;line-height:1}} .logo b{{color:#3b82f6}}
.sub{{color:#8b949e;font-size:30px;margin-top:14px}}
.dots{{display:flex;gap:12px;margin-top:34px}} .dots i{{width:14px;height:14px;border-radius:50%;background:#373e48}}
.dots i.on{{background:#3b82f6}} .msg{{color:#8b949e;font-size:18px;margin-top:40px}}
.why{{position:fixed;bottom:16px;left:0;right:0;text-align:center;color:#5c6670;font-size:14px}}</style>
<div class="logo">NeoDen<b>4</b></div><div class="sub">pick and place</div>
<div class="dots"><i></i><i></i><i></i><i></i><i></i></div>
<div class="msg">{msg}</div><div class="why">{why}</div>
<script>let n=0;const d=document.querySelectorAll(".dots i");
setInterval(()=>{{d.forEach((e,i)=>e.classList.toggle("on",i==n%5));n++;}},160);</script>"""
SLOW_S = 30         # after this long without the server, the page says why and shows the addresses



def window(cfg):
    gpu = yes(cfg["gpu"])
    if not gpu:
        os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")
        os.environ.setdefault("WEBKIT_SKIA_ENABLE_CPU_RENDERING", "1")
        os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
    for kv in cfg.get("env", "").split():           # extra environment, e.g. WEBKIT_...=1
        k, _, v = kv.partition("=")
        os.environ[k] = v
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import Gdk, GLib, Gtk, WebKit2

    url = cfg["url"]
    hide = cfg["hide_cursor"].strip().lower()
    hide = touch_only() if hide == "auto" else yes(hide)

    ucm = WebKit2.UserContentManager()
    # WebKitGTK draws number fields with wide GTK spin buttons (Chrome: narrow ones), which cut
    # the numbers in the job tables ("408.9"): the kiosk hides them (arrow keys still step)
    ucm.add_style_sheet(WebKit2.UserStyleSheet(
        "input[type=number]::-webkit-inner-spin-button,input[type=number]::-webkit-outer-spin-button"
        "{-webkit-appearance:none!important;display:none!important;margin:0!important}"
        "input[type=number]{-webkit-appearance:textfield!important}",
        WebKit2.UserContentInjectedFrames.ALL_FRAMES, WebKit2.UserStyleLevel.USER, None, None))
    if yes(cfg["lite"]):
        ucm.add_style_sheet(WebKit2.UserStyleSheet(
            "*,*::before,*::after{box-shadow:none!important;text-shadow:none!important;"
            "transition:none!important;animation:none!important}",
            WebKit2.UserContentInjectedFrames.ALL_FRAMES, WebKit2.UserStyleLevel.USER, None, None))
    if hide:
        ucm.add_style_sheet(WebKit2.UserStyleSheet(
            "*{cursor:none!important}", WebKit2.UserContentInjectedFrames.ALL_FRAMES,
            WebKit2.UserStyleLevel.USER, None, None))
    # this screen's theme, for js/i18n.js (it wins over the page's own stored choice here; the theme
    # button on this screen writes kiosk.ini through /api/kiosk)
    theme = cfg.get("theme", "auto").strip().lower()
    theme = theme if theme in ("dark", "light") else "auto"
    ucm.add_script(WebKit2.UserScript(
        "window.KIOSK = { theme: %r };" % theme, WebKit2.UserContentInjectedFrames.TOP_FRAME,
        WebKit2.UserScriptInjectionTime.START, None, None))
    view = WebKit2.WebView.new_with_user_content_manager(ucm)
    rgba = Gdk.RGBA()
    rgba.parse("#1b1f24")
    view.set_background_color(rgba)                 # no white flash before the first page
    st = view.get_settings()
    st.set_enable_developer_extras(False)
    st.set_javascript_can_open_windows_automatically(True)
    if not gpu:
        try:
            st.set_hardware_acceleration_policy(WebKit2.HardwareAccelerationPolicy.NEVER)
        except (AttributeError, TypeError):
            pass
    try:
        zoom = float(cfg["zoom"])
    except ValueError:
        zoom = 1.0
    view.set_zoom_level(zoom)

    state = {"retry": None}
    # the UI's own pages: any path or #page on the server of `url` (url may point at #job)
    site = "/".join(url.split("/", 3)[:3]) + "/"

    def load():
        state["retry"] = None
        view.load_uri(url)
        return False

    def failed(_v, _event, failing, err):
        if not failing.startswith(site):
            return False                            # a link elsewhere: WebKit's own error page
        net, policy = (GLib.quark_to_string(q) for q in (WebKit2.network_error_quark(),
                                                          WebKit2.policy_error_quark()))
        if (err.domain == net and err.code == WebKit2.NetworkError.CANCELLED) or err.domain == policy:
            return False                            # navigation replaced / a download: not an outage
        now = time.monotonic()
        if now - state.get("failed", -1e9) > 3 * RETRY_S + 5:  # a new outage, not the next retry
            state["since"] = now
        state["failed"] = now
        if now - state["since"] < SLOW_S:
            msg, why = "starting the machine software&hellip;", ""
        else:
            msg = "still waiting for the machine software&hellip;"
            why = GLib.markup_escape_text(f"{url}: {err.message or ''} · this PC: {' '.join(addresses())}")
        view.load_alternate_html(WAITING.format(msg=msg, why=why), failing, None)
        if state["retry"] is None:
            state["retry"] = GLib.timeout_add_seconds(RETRY_S, load)
        return True

    def new_window(_v, action):                     # target=_blank / window.open: same view
        req = action.get_request()
        if req is not None and req.get_uri():
            view.load_uri(req.get_uri())
        return None

    def key(_w, ev):
        ctrl = bool(ev.state & Gdk.ModifierType.CONTROL_MASK)
        k = ev.keyval
        if k == Gdk.KEY_F5 or (ctrl and k in (Gdk.KEY_r, Gdk.KEY_R)):
            if (view.get_uri() or "").startswith(site):
                view.reload()
            else:
                load()
            return True
        if ctrl and k in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
            view.set_zoom_level(min(3.0, view.get_zoom_level() + 0.1))
            return True
        if ctrl and k in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
            view.set_zoom_level(max(0.3, view.get_zoom_level() - 0.1))
            return True
        if ctrl and k in (Gdk.KEY_0, Gdk.KEY_KP_0):
            view.set_zoom_level(zoom)
            return True
        return False                                # everything else goes to the page

    # an update of the web UI (files written on this PC) reloads the page, like F5 in a browser:
    # nobody presses F5 on the machine, so it would keep running the old pages until a restart
    folder = watched(cfg)
    if folder:
        seen = {"stamp": stamp(folder), "new": None}

        def check():
            now = stamp(folder)
            if now == seen["stamp"]:
                seen["new"] = None
            elif now != seen["new"]:
                seen["new"] = now                   # still being written: wait for a quiet check
            else:
                seen["stamp"], seen["new"] = now, None
                print("neokiosk: web UI changed, reload", file=sys.stderr)
                if (view.get_uri() or "").startswith(site):
                    view.reload()
                else:
                    load()
            return True
        GLib.timeout_add_seconds(WATCH_S, check)

    view.connect("load-failed", failed)
    view.connect("create", new_window)
    view.connect("context-menu", lambda *a: True)   # no Back / Inspect menu
    view.connect("web-process-terminated", lambda *a: GLib.timeout_add_seconds(1, load))

    css = Gtk.CssProvider()                          # dark until the page draws, like the boot screen
    css.load_from_data(b"window{background-color:#1b1f24}")
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    win = Gtk.Window(title="NeoDen")
    win.set_decorated(False)
    win.add(view)
    win.connect("key-press-event", key)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    win.fullscreen()
    if hide:
        gw = win.get_window()
        gw.set_cursor(Gdk.Cursor.new_from_name(gw.get_display(), "none"))
    view.grab_focus()
    load()
    Gtk.main()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=CONFIG)
    ap.add_argument("--cage", action="store_true", help="start cage with this window in it")
    ap.add_argument("--screens", action="store_true", help="switch off unused screens (root)")
    a = ap.parse_args()
    cfg = settings(a.config)
    if a.screens:
        try:
            mouse_speed(cfg["mouse_speed"])          # root, before cage: libinput reads it when cage starts
        except Exception as e:
            print("neokiosk: mouse speed:", e, file=sys.stderr)
        screens(cfg["output"], cfg["fallback_mode"])
        return
    if a.cage:
        run_cage(cfg, a.config)
    if os.environ.get("WAYLAND_DISPLAY"):
        rotate(cfg)
    print("neokiosk:", cfg["url"], socket.gethostname(), file=sys.stderr)
    window(cfg)


if __name__ == "__main__":
    main()
