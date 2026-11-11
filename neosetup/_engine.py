"""Loads the vendored KFG Pick & Place Tool (engine/csv_flip_tool.py) headless.

One load = one engine: a fresh module namespace (a dict) holding the tool's globals --
`rows`, `machine_model`, `LEARNED_SLOTS`, the settings constants -- and every function of
the tool. Nothing is shared between two loads.

The headless tkinter stub is put into sys.modules only while the tool's top level runs,
under a process-wide lock, and taken out again before load() returns, so it never leaks
into the rest of the process (e.g. a web server). The tool keeps references to the stub
modules it imported; nothing else sees them. The real tkinter is never imported.

The source is compiled once per process (and again only if the file changes): exec of the
cached code object is all a new engine costs.
"""
import builtins
import os
import threading

from . import _tkstub

ENGINE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine",
                           "csv_flip_tool.py")

_lock = threading.RLock()
_cache = {}          # path -> (mtime, size, code object)


def _compiled(path):
    st = os.stat(path)
    hit = _cache.get(path)
    if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2]
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    code = compile(src, path, "exec")
    _cache[path] = (st.st_mtime, st.st_size, code)
    return code


def load(dialogs, path=None):
    """Runs the tool's module top level with the stub; returns its globals dict.

    The tool reads its Options file while it loads. KFG_SETTINGS is set to "" for that
    moment, so the user's own settings file is never read; the caller decides afterwards
    where settings come from (see Project).
    """
    path = path or ENGINE_PATH
    code = _compiled(path)
    g = {"__name__": "neosetup_engine", "__file__": path, "__builtins__": builtins}
    with _lock:
        saved = _tkstub.install(dialogs)
        env = os.environ.get("KFG_SETTINGS")
        os.environ["KFG_SETTINGS"] = ""
        try:
            exec(code, g)
        finally:
            if env is None:
                os.environ.pop("KFG_SETTINGS", None)
            else:
                os.environ["KFG_SETTINGS"] = env
            _tkstub.restore(saved)
    return g
