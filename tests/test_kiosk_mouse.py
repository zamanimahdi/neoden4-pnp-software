"""The machine screen's mouse speed: neokiosk writes the mouse DPI for libinput (udev hwdb), the
server edits kiosk.ini (comments kept, values clamped)."""
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import neokiosk.__main__ as K  # noqa: E402
import neoserver.__main__ as M  # noqa: E402


def test_mouse_speed_hwdb(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setattr(K, "HWDB", os.path.join(d, "hwdb.d", "71-neoden-mouse.hwdb"))
    assert K.mouse_speed("2") == 500                    # twice as fast: every mouse as 500 dpi
    t = open(K.HWDB).read()
    assert "mouse:*:name:*:" in t and "MOUSE_DPI=500@125" in t
    assert K.mouse_speed("0.5") == 2000
    assert K.mouse_speed("9") == 200                    # clamped to 5x
    assert K.mouse_speed("1.0") is None and not os.path.exists(K.HWDB)   # as the mouse is: ours removed
    assert K.DEFAULTS["mouse_speed"] == "1.0"


def test_kiosk_ini_edit(monkeypatch):
    d = tempfile.mkdtemp()
    ini = os.path.join(d, "kiosk.ini")
    open(ini, "w").write("; Kiosk\n[kiosk]\nurl = http://localhost/#job\n; page zoom\nzoom = 0.85\n")
    written, said = {}, []

    def write(p, b):
        written[p] = b
        open(p, "wb").write(b)
    monkeypatch.setattr(M, "S", types.SimpleNamespace(files=types.SimpleNamespace(write=write), log=lambda *a: said.append(a)))
    h = types.SimpleNamespace(KIOSK_INI=ini, KIOSK_KEYS=M.Handler.KIOSK_KEYS, ok=lambda **kw: kw)
    r = M.Handler.kiosk(h, "GET", {})
    assert r["installed"] and r["zoom"] == 0.85 and r["mouse_speed"] == 1.0
    r = M.Handler.kiosk(h, "POST", {"mouse_speed": "1.6", "zoom": "9", "restart": "0"})
    t = open(ini).read()
    assert "mouse_speed = 1.6" in t and "zoom = 2" in t and "; page zoom" in t and "url = http://localhost/#job" in t
    assert r["restarted"] is False
    r = M.Handler.kiosk(h, "GET", {})
    assert r["mouse_speed"] == 1.6 and r["zoom"] == 2.0


def test_kiosk_theme(monkeypatch):
    d = tempfile.mkdtemp()
    ini = os.path.join(d, "kiosk.ini")
    open(ini, "w").write("[kiosk]\nzoom = 0.85\n")
    monkeypatch.setattr(M, "S", types.SimpleNamespace(files=types.SimpleNamespace(write=lambda p, b: open(p, "wb").write(b)), log=lambda *a: None))
    h = types.SimpleNamespace(KIOSK_INI=ini, KIOSK_KEYS=M.Handler.KIOSK_KEYS, ok=lambda **kw: kw)
    assert M.Handler.kiosk(h, "GET", {})["theme"] == "auto"
    M.Handler.kiosk(h, "POST", {"theme": "dark", "restart": "0"})
    assert M.Handler.kiosk(h, "GET", {})["theme"] == "dark"
    M.Handler.kiosk(h, "POST", {"theme": "purple", "restart": "0"})      # not a theme: ignored
    assert M.Handler.kiosk(h, "GET", {})["theme"] == "dark" and "zoom = 0.85" in open(ini).read()
    assert K.DEFAULTS["theme"] == "auto"
