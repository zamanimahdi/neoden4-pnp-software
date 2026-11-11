"""Config.ini compatibility with NeoDen4 (Qt QSettings): read, write, byte-exact re-encode.

Uses the machine's real Config.ini when present (NEODEN_CONFIG or the RE folder), otherwise a
small sample with the tricky escapes.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from neocam.config import NeoConfig, format_value, read_ini  # noqa: E402

REAL = os.environ.get("NEODEN_CONFIG", os.path.join(os.environ.get("NEODEN_DATA", ""), "Config.ini"))
SAMPLE = """[Default]
bLighting=false
nRunSpeed=80
fAngleCamLookUp=@Variant(\\0\\0\\0\\x87>\\xcc\\xcc\\xcd)
fBrightness=@Variant(\\0\\0\\0\\x87@@\\0\\0)

[Position]
dPosSubMouses0.x=@Variant(\\0\\0\\0\\x87\\x43!\\0\\0)
dPosCamLookDownMouse1.x=@Variant(\\0\\0\\0\\x87\\xc1!\\x1e\\xb8)

[Stack]
lstStack4.pos.y=@Variant(\\0\\0\\0\\x87\\x43\\xfI$)
lstStack14.pos.y="@Variant(\\0\\0\\0\\x87\\x43\\xab=\\xb8)"
lstStack0.nFeedId=1
"""


def _file():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "Config.ini")
    if os.path.exists(REAL):
        shutil.copy(REAL, p)
    else:
        open(p, "w", encoding="latin-1").write(SAMPLE)
    return p


def test_reencode_byte_exact():
    p = _file()
    v = read_ini(p)
    sec, n = "General", 0
    for line in open(p, encoding="latin-1"):
        line = line.rstrip("\r\n")
        if line.startswith("["):
            sec = line[1:-1]
        elif "=" in line:
            k, x = line.split("=", 1)
            assert format_value(v[f"{sec}/{k}"]) == x, (k, x)
            n += 1
    assert n > 5


def test_values():
    c = NeoConfig.load(_file())
    assert c.brightness == 3.0 and abs(c.cam_angle - 0.4) < 1e-6
    assert c.sub_mouses[0][0] == 161.0
    assert abs(c.head_cam_offset[0] + 10.07) < 1e-3


def test_write_keeps_everything_else():
    p = _file()
    before = read_ini(p)
    c = NeoConfig.load(p)
    c.set("Default/fBrightness", 2.5)
    c.set_pt("Position/dPosCamLookDownMouse1", (-10.125, 36.5))
    c.set("Default/newKey", 7)
    c.set("NewGroup/x", 1.5)
    c.save()
    after = read_ini(p)
    assert after["Default/fBrightness"] == 2.5 and after["Default/newKey"] == 7 and after["NewGroup/x"] == 1.5
    assert after["Position/dPosCamLookDownMouse1.x"] == -10.125
    changed = {"Default/fBrightness", "Position/dPosCamLookDownMouse1.x", "Position/dPosCamLookDownMouse1.y"}
    assert all(after[k] == before[k] for k in before if k not in changed)


if __name__ == "__main__":
    for name, f in list(globals().items()):
        if name.startswith("test_"):
            f()
            print("ok", name)
