"""A headless stand-in for tkinter, so the KFG Pick & Place Tool's logic can run without a
window (inside the machine software, in tests, on a PC without a display).

Widgets accept everything; methods that are not modelled return a falsy, empty, zero-like
result. What the tool's logic reads back from widgets IS modelled, in memory:

  - tk.StringVar / IntVar / DoubleVar / BooleanVar keep their value and fire their traces;
  - Entry and Text keep their text (or read/write their textvariable);
  - ttk.Treeview keeps its items, their order, tags and the selection -- and hands values
    back re-typed the way Tcl does (``item(iid)["values"]`` turns "0603" into 603);
  - ttk.Combobox keeps its values and current choice;
  - every widget knows its parent and children (winfo_children / winfo_class / cget),
    Button / Checkbutton / Radiobutton ``invoke()`` run their command;
  - Toplevel.title() is kept.

Dialogs go to a Dialogs object the caller controls: messages are collected, file dialogs
return the path set by the caller, yes/no questions return the caller's answer.

Nothing here imports the real tkinter.
"""
import sys
import types


class _R:
    """Result of any widget method: empty, falsy, zero, chainable."""

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return _call

    def __call__(self, *a, **k):
        return _R()

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def __bool__(self):
        return False

    def __str__(self):
        return ""

    def __int__(self):
        return 0

    def __float__(self):
        return 0.0

    def __index__(self):
        return 0

    def __getitem__(self, k):
        return _R()

    def __contains__(self, k):
        return False

    def __eq__(self, o):
        return isinstance(o, _R)

    def __hash__(self):
        return 0

    def _num(self, *a):
        return 0
    __add__ = __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = _num
    __truediv__ = __rtruediv__ = __floordiv__ = __rfloordiv__ = __mod__ = __neg__ = _num

    def __lt__(self, o):
        return False
    __le__ = __gt__ = __ge__ = __lt__


def _call(*a, **k):
    return _R()


class TclError(Exception):
    pass


def _tcl_retype(value):
    """A value as Tcl hands it back from a Treeview: its text, made an int where int()
    accepts it (tkinter's _convert_stringval)."""
    text = str(value)
    try:
        return int(text)
    except (TypeError, ValueError):
        return text


class Widget:
    """Any widget, window or ttk object."""
    _class = "Widget"

    def __init__(self, master=None, *a, **k):
        self._opts = dict(k)
        self._children = []
        self._master = master if isinstance(master, Widget) else None
        self._title = ""
        if self._master is not None:
            self._master._children.append(self)

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return _call

    def __getitem__(self, k):
        return self._opts.get(k, "")

    def __setitem__(self, k, v):
        self._opts[k] = v

    def configure(self, *a, **k):
        self._opts.update(k)
        return _R()
    config = configure

    def cget(self, k):
        return self._opts.get(k, "")

    def get(self, *a, **k):
        return ""

    def winfo_children(self):
        return list(self._children)

    def winfo_class(self):
        return self._class

    def winfo_toplevel(self):
        w = self
        while w._master is not None and not isinstance(w, (Toplevel, Tk)):
            w = w._master
        return w

    def winfo_exists(self):
        return 1

    def title(self, *a):
        if a:
            self._title = str(a[0])
            return ""
        return self._title

    def destroy(self):
        if self._master is not None and self in self._master._children:
            self._master._children.remove(self)

    def invoke(self):
        cmd = self._opts.get("command")
        return cmd() if callable(cmd) else ""

    def __bool__(self):
        return True


def _widget(name, base=Widget):
    return type(name, (base,), {"_class": name})


class Tk(Widget):
    _class = "Tk"


class Toplevel(Widget):
    _class = "Toplevel"


class Entry(Widget):
    """Keeps its text, or reads and writes its textvariable."""
    _class = "Entry"

    def __init__(self, master=None, *a, **k):
        Widget.__init__(self, master, *a, **k)
        self._text = ""

    def _var(self):
        v = self._opts.get("textvariable")
        return v if isinstance(v, _Var) else None

    def get(self, *a, **k):
        v = self._var()
        return str(v.get()) if v is not None else self._text

    def _put(self, text):
        v = self._var()
        if v is not None:
            v.set(text)
        else:
            self._text = text

    @staticmethod
    def _index(text, i):
        if i in ("end", "END") or i is None:
            return len(text)
        try:
            return max(0, min(len(text), int(i)))
        except (TypeError, ValueError):
            return len(text)

    def insert(self, index, text):
        cur = self.get()
        i = self._index(cur, index)
        self._put(cur[:i] + str(text) + cur[i:])

    def delete(self, first, last=None):
        cur = self.get()
        i = self._index(cur, first)
        j = i + 1 if last is None else self._index(cur, last)
        self._put(cur[:i] + cur[j:])


class Spinbox(Entry):
    _class = "Spinbox"


class Text(Widget):
    """Keeps its text: insert / get / delete with "1.0" / "end" style indices (whole-text)."""
    _class = "Text"

    def __init__(self, master=None, *a, **k):
        Widget.__init__(self, master, *a, **k)
        self._text = ""

    def insert(self, index, text, *tags):
        if index in ("1.0", 1.0):
            self._text = str(text) + self._text
        else:
            self._text += str(text)

    def get(self, first="1.0", last="end"):
        return self._text

    def delete(self, first="1.0", last=None):
        self._text = ""


class _Var:
    default = ""

    def __init__(self, master=None, value=None, name=None):
        self._v = self.default if value is None else value
        self._traces = []

    def get(self):
        return self._v

    def set(self, v):
        self._v = v
        for cb in list(self._traces):
            try:
                cb()
            except TypeError:
                cb("", "", "w")

    def trace_add(self, mode, cb):
        self._traces.append(cb)
        return str(len(self._traces))

    def trace(self, mode, cb):
        return self.trace_add(mode, cb)

    def trace_remove(self, *a):
        pass


class StringVar(_Var):
    default = ""

    def get(self):
        return "" if self._v is None else str(self._v)


class IntVar(_Var):
    default = 0

    def get(self):
        try:
            return int(self._v)
        except (TypeError, ValueError):
            return 0


class DoubleVar(_Var):
    default = 0.0

    def get(self):
        try:
            return float(self._v)
        except (TypeError, ValueError):
            return 0.0


class BooleanVar(_Var):
    default = False

    def get(self):
        return bool(self._v)


class Checkbutton(Widget):
    _class = "Checkbutton"

    def invoke(self):
        v = self._opts.get("variable")
        if isinstance(v, _Var):
            on = self._opts.get("onvalue", True)
            off = self._opts.get("offvalue", False)
            v.set(off if v.get() == on else on)
        return Widget.invoke(self)

    def select(self):
        v = self._opts.get("variable")
        if isinstance(v, _Var):
            v.set(self._opts.get("onvalue", True))

    def deselect(self):
        v = self._opts.get("variable")
        if isinstance(v, _Var):
            v.set(self._opts.get("offvalue", False))


class Radiobutton(Widget):
    _class = "Radiobutton"

    def invoke(self):
        v = self._opts.get("variable")
        if isinstance(v, _Var):
            v.set(self._opts.get("value", ""))
        return Widget.invoke(self)

    def select(self):
        v = self._opts.get("variable")
        if isinstance(v, _Var):
            v.set(self._opts.get("value", ""))


class Combobox(Entry):
    """ttk.Combobox: text (or textvariable), its values, and current()."""
    _class = "TCombobox"

    def set(self, value):
        self._put(str(value))

    def current(self, index=None):
        values = list(self._opts.get("values") or ())
        if index is None:
            text = self.get()
            for i, v in enumerate(values):
                if str(v) == text:
                    return i
            return -1
        self._put(str(values[index]))
        return ""


class Treeview(Widget):
    """ttk.Treeview kept in memory: items in order, values, tags, selection.

    Values come back as Tcl gives them: ``item(iid)["values"]`` is a list with each value's
    text made an int where possible ('' for none); ``item(iid, "values")`` a tuple of text.
    ``raw_values(iid)`` returns exactly what was inserted (not part of ttk).
    """
    _class = "Treeview"

    def __init__(self, master=None, *a, **k):
        Widget.__init__(self, master, *a, **k)
        self._items = {}          # iid -> {"values", "tags", "text", "parent", "children"}
        self._top = []
        self._sel = []
        self._auto = 0

    # ---- items ----
    def insert(self, parent, index, iid=None, **kw):
        if iid is None:
            self._auto += 1
            iid = "I%03X" % self._auto
        iid = str(iid)
        if iid in self._items:
            raise TclError("Item %s already exists" % iid)
        siblings = self._top if parent in ("", None) else self._items[str(parent)]["children"]
        tags = kw.get("tags", ())
        if isinstance(tags, str):
            tags = (tags,) if tags else ()
        self._items[iid] = {"values": list(kw.get("values", ()) or ()), "tags": list(tags),
                            "text": kw.get("text", ""), "children": [],
                            "parent": "" if parent in ("", None) else str(parent)}
        if index in ("end", "END"):
            siblings.append(iid)
        else:
            siblings.insert(int(index), iid)
        return iid

    def _siblings(self, iid):
        p = self._items[iid]["parent"]
        return self._top if p == "" else self._items[p]["children"]

    def delete(self, *items):
        for iid in items:
            iid = str(iid)
            if iid not in self._items:
                raise TclError("Item %s not found" % iid)
            for c in list(self._items[iid]["children"]):
                self.delete(c)
            self._siblings(iid).remove(iid)
            del self._items[iid]
            if iid in self._sel:
                self._sel.remove(iid)

    def get_children(self, item=None):
        if item in (None, ""):
            return tuple(self._top)
        return tuple(self._items[str(item)]["children"])

    def exists(self, iid):
        return str(iid) in self._items

    def index(self, iid):
        return self._siblings(str(iid)).index(str(iid))

    def parent(self, iid):
        return self._items[str(iid)]["parent"]

    def move(self, iid, parent, index):
        iid = str(iid)
        self._siblings(iid).remove(iid)
        self._items[iid]["parent"] = "" if parent in ("", None) else str(parent)
        sib = self._siblings(iid)
        if index in ("end", "END"):
            sib.append(iid)
        else:
            sib.insert(int(index), iid)

    def item(self, iid, option=None, **kw):
        it = self._items[str(iid)]
        if kw:
            if "values" in kw:
                it["values"] = list(kw["values"] or ())
            if "tags" in kw:
                t = kw["tags"]
                it["tags"] = [t] if isinstance(t, str) and t else list(t or ())
            if "text" in kw:
                it["text"] = kw["text"]
            return ""
        if option == "values":
            return tuple(str(v) for v in it["values"])
        if option == "tags":
            return tuple(it["tags"])
        if option == "text":
            return it["text"]
        if option is not None:
            return ""
        return {"text": it["text"], "image": "",
                "values": [_tcl_retype(v) for v in it["values"]] if it["values"] else "",
                "open": 0, "tags": list(it["tags"]) if it["tags"] else ""}

    def set(self, iid, column=None, value=None):
        return ""

    def raw_values(self, iid):
        return list(self._items[str(iid)]["values"])

    def tags_of(self, iid):
        return list(self._items[str(iid)]["tags"])

    # ---- selection ----
    @staticmethod
    def _flat(items):
        out = []
        for i in items:
            if isinstance(i, (list, tuple)):
                out += [str(x) for x in i]
            else:
                out.append(str(i))          # one item, spaces and all (as tkinter 3.8+)
        return out

    def _ordered(self, ids):
        order = []

        def walk(seq):
            for i in seq:
                order.append(i)
                walk(self._items[i]["children"])
        walk(self._top)
        pos = {i: n for n, i in enumerate(order)}
        return sorted(ids, key=lambda i: pos.get(i, 0))

    def selection(self):
        return tuple(self._ordered([i for i in self._sel if i in self._items]))

    def selection_set(self, *items):
        ids = self._flat(items)
        for i in ids:
            if i not in self._items:
                raise TclError("Item %s not found" % i)
        self._sel = list(dict.fromkeys(ids))

    def selection_add(self, *items):
        for i in self._flat(items):
            if i not in self._items:
                raise TclError("Item %s not found" % i)
            if i not in self._sel:
                self._sel.append(i)

    def selection_remove(self, *items):
        for i in self._flat(items):
            if i in self._sel:
                self._sel.remove(i)

    def focus(self, iid=None):
        return "" if iid is None else ""


class Dialogs:
    """Where the tool's dialogs go. Set `open_path` / `save_path` before calling a function
    that asks for a file, `answer` for yes/no questions; read `messages` afterwards."""

    def __init__(self):
        self.messages = []          # (kind, title, text)
        self.open_path = ""
        self.save_path = ""
        self.directory = ""
        self.answer = True

    def _msg(self, kind):
        def f(title="", message="", *a, **k):
            self.messages.append((kind, str(title), str(message or k.get("message", ""))))
            if kind.startswith("ask"):
                return self.answer
            return "ok"
        return f

    def take(self, kind=None):
        """Messages since the last take(), optionally of one kind ("info", "warning", "error")."""
        out = [m for m in self.messages if kind is None or m[0] == kind]
        self.messages = [m for m in self.messages if not (kind is None or m[0] == kind)]
        return out


_TK_PLAIN = ("Frame", "LabelFrame", "Label", "Button", "Canvas", "Listbox", "Scrollbar",
             "Menu", "Menubutton", "Scale", "PanedWindow", "Message", "OptionMenu",
             "PhotoImage", "Event", "Misc")
_TTK_PLAIN = ("Notebook", "Style", "Scrollbar", "Frame", "Label", "Button", "Progressbar",
              "Separator", "LabelFrame", "Scale", "Sizegrip", "PanedWindow")


def install(dialogs):
    """Put stub tkinter modules into sys.modules; returns the previous entries to restore.
    PIL is hidden too, so the tool runs without it (its Gerber underlay image is UI only)."""
    names = ["tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox", "tkinter.font",
             "tkinter.simpledialog", "PIL", "PIL.ImageTk"]
    saved = {n: sys.modules.get(n) for n in names}
    tk = types.ModuleType("tkinter")
    for n in _TK_PLAIN:
        setattr(tk, n, _widget(n))
    tk.Tk, tk.Toplevel, tk.Entry, tk.Text, tk.Spinbox = Tk, Toplevel, Entry, Text, Spinbox
    tk.Checkbutton, tk.Radiobutton = Checkbutton, Radiobutton
    tk.Widget = Widget
    tk.StringVar, tk.IntVar, tk.DoubleVar, tk.BooleanVar, tk.TclError = StringVar, IntVar, DoubleVar, BooleanVar, TclError
    for n in ("END", "INSERT", "LEFT", "RIGHT", "TOP", "BOTTOM", "BOTH", "X", "Y", "W", "E", "N", "S",
              "NW", "NE", "SW", "SE", "CENTER", "HORIZONTAL", "VERTICAL", "DISABLED", "NORMAL", "ACTIVE",
              "SINGLE", "EXTENDED", "BROWSE", "MULTIPLE", "YES", "NO", "WORD", "NONE", "RIDGE", "FLAT",
              "SUNKEN", "RAISED", "GROOVE", "SOLID", "ALL", "HIDDEN"):
        setattr(tk, n, n.lower())
    ttk = types.ModuleType("tkinter.ttk")
    for n in _TTK_PLAIN:
        setattr(ttk, n, _widget("T" + n if n not in ("Style",) else n))
    ttk.Treeview, ttk.Combobox, ttk.Entry, ttk.Spinbox = Treeview, Combobox, Entry, Spinbox
    ttk.Checkbutton, ttk.Radiobutton = Checkbutton, Radiobutton
    fd = types.ModuleType("tkinter.filedialog")
    fd.askopenfilename = lambda *a, **k: dialogs.open_path
    fd.asksaveasfilename = lambda *a, **k: dialogs.save_path
    fd.askdirectory = lambda *a, **k: dialogs.directory
    fd.askopenfilenames = lambda *a, **k: (dialogs.open_path,) if dialogs.open_path else ()
    mb = types.ModuleType("tkinter.messagebox")
    for n, kind in (("showinfo", "info"), ("showwarning", "warning"), ("showerror", "error"),
                    ("askyesno", "askyesno"), ("askokcancel", "askokcancel"),
                    ("askyesnocancel", "askyesnocancel"), ("askquestion", "askquestion"),
                    ("askretrycancel", "askretrycancel")):
        setattr(mb, n, dialogs._msg(kind))
    font = types.ModuleType("tkinter.font")
    font.Font = Widget
    font.families = lambda *a, **k: ()
    font.nametofont = lambda *a, **k: Widget()
    sd = types.ModuleType("tkinter.simpledialog")
    sd.askstring = lambda *a, **k: None
    sd.askfloat = lambda *a, **k: None
    sd.askinteger = lambda *a, **k: None
    tk.ttk, tk.filedialog, tk.messagebox, tk.font, tk.simpledialog = ttk, fd, mb, font, sd
    sys.modules.update({"tkinter": tk, "tkinter.ttk": ttk, "tkinter.filedialog": fd,
                        "tkinter.messagebox": mb, "tkinter.font": font, "tkinter.simpledialog": sd,
                        "PIL": None,          # no Pillow: the tool falls back to canvas items
                        "PIL.ImageTk": None})
    return saved


def restore(saved):
    for n, m in saved.items():
        if m is None:
            sys.modules.pop(n, None)
        else:
            sys.modules[n] = m
