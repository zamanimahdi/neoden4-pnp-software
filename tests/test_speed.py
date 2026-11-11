"""Caches for the machine's slow PC (Atom D525): Config.ini parsed once per change, each load its own copy."""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from neocam.config import NeoConfig  # noqa: E402


def test_config_cache():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "Config.ini")
    open(p, "w").write("[Vision]\nfHeadPxPerMm=115.2\n")
    a = NeoConfig.load(p)
    a.raw["Vision/fHeadPxPerMm"] = 1.0                        # one load's change stays its own
    assert NeoConfig.load(p).raw["Vision/fHeadPxPerMm"] == 115.2
    time.sleep(0.01)
    open(p, "w").write("[Vision]\nfHeadPxPerMm=116.80\n")     # the file changed: read again
    assert NeoConfig.load(p).raw["Vision/fHeadPxPerMm"] == 116.8
