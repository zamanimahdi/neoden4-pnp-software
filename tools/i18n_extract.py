"""List the UI's English texts for the Persian dictionary (neoagent/www/js/fa.js).

    python tools/i18n_extract.py            # texts in js/*.js and index.html not in fa.js yet
    python tools/i18n_extract.py --all      # every text found
    python tools/i18n_extract.py --json F   # write them as a JSON list to F
    python tools/i18n_extract.py --py       # the server's texts (neoserver, neojob, ...) instead

Texts are the string literals of the page code that look like words for people (not class
names, URLs, keys or code). Template strings `...${x}...` become patterns with {0}, {1}, ...
in place of the ${} parts; js/i18n.js matches those against what is on the screen.
"""
import glob
import json
import os
import re
import sys

WWW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "neoagent", "www")
SKIP_FILES = {"i18n.js", "fa.js"}


def literals(src):
    """the string literals of a JS source: (quote, text) with ${...} kept as written"""
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == "/" and src[i:i + 2] == "//":
            i = src.find("\n", i)
            i = n if i < 0 else i
            continue
        if c == "/" and src[i:i + 2] == "/*":
            i = src.find("*/", i + 2)
            i = n if i < 0 else i + 2
            continue
        if c in "'\"`":
            j, out, depth = i + 1, [], 0
            while j < n:
                d = src[j]
                if d == "\\" and j + 1 < n:
                    out.append(src[j:j + 2]); j += 2; continue
                if c == "`" and d == "$" and src[j + 1:j + 2] == "{":
                    k, depth = j + 2, 1
                    while k < n and depth:
                        depth += {"{": 1, "}": -1}.get(src[k], 0)
                        k += 1
                    out.append(src[j:k]); j = k; continue
                if d == c:
                    break
                if d == "\n" and c != "`":
                    break
                out.append(d); j += 1
            yield c, "".join(out)
            i = j + 1
            continue
        i += 1


def unescape(t):
    """the text as the page shows it: the escapes \\n, \\t, \\', \\" as their characters"""
    return t.replace("\\n", "\n").replace("\\t", "\t").replace("\\'", "'").replace('\\"', '"').replace("\\`", "`")


CODE = re.compile(r"^[\w\-.#:/\\%=&?@*+,;()\[\]{}<>|^~$!'\"]*$")


def wordy(t):
    """looks like text for people: letters and a space, or a capitalised word"""
    s = t.strip()
    if len(s) < 2 or not re.search(r"[A-Za-z]{2}", s):
        return False
    if re.match(r"^(https?:|/api/|#[a-z]|\.|[a-z]+(-[a-z]+)+$)", s):
        return False
    if re.search(r"[{}]|;(?!\s+[A-Za-z0-9(])|=>|\bfunction\b|\breturn\b|\bconst\b|document\.|\.(js|css|png|csv|ini|json|py|bin)\b", s):
        return False
    if " " not in s:
        if CODE.match(s) and not re.match(r"^[A-Z][a-z]+$|^[A-Z]{2,}$|^[A-Z][a-z]+[.:…]?$", s):
            return False
    if re.match(r"^[a-z0-9_]+( [a-z0-9_]+)*$", s) and len(s.split()) <= 2 and not re.search(r"[a-z]{3} [a-z]{3}", s):
        return False                        # "btn small", "row", "on"
    if re.match(r"^[a-z]+( [a-z\-]+)+$", s) and all(w in CLASSES for w in s.split()):
        return False
    return True


CLASSES = set()


def collect():
    css = open(os.path.join(WWW, "style.css"), encoding="utf-8").read()
    CLASSES.update(re.findall(r"\.([a-z][a-z0-9\-]*)", css))
    texts = {}
    for f in sorted(glob.glob(os.path.join(WWW, "js", "*.js"))):
        if os.path.basename(f) in SKIP_FILES:
            continue
        for q, t in literals(open(f, encoding="utf-8").read()):
            if q == "`" and "${" in t:
                parts = re.split(r"\$\{(?:[^{}]|\{[^{}]*\})*\}", t)
                pat = "".join(p + ("{%d}" % i if i < len(parts) - 1 else "") for i, p in enumerate(parts))
                if wordy(re.sub(r"\{\d+\}", " ", pat)):
                    texts.setdefault(unescape(pat).strip(), os.path.basename(f))
            elif wordy(t):
                texts.setdefault(unescape(t).strip(), os.path.basename(f))
    html = open(os.path.join(WWW, "index.html"), encoding="utf-8").read()
    for t in re.findall(r">([^<>]+)<", html) + re.findall(r'title="([^"]+)"', html):
        if wordy(t):
            texts.setdefault(t.strip(), "index.html")
    return texts


PY_DIRS = ("neoserver", "neojob", "neomach", "neocalib", "neosetup")


def py_texts():
    """the server's messages: Python string literals that look like words for people, f-strings
    as patterns ({0} for each replacement field); docstrings left out"""
    import ast
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    texts = {}
    for d in PY_DIRS:
        for f in sorted(glob.glob(os.path.join(base, d, "**", "*.py"), recursive=True)):
            if os.sep + "tests" + os.sep in f or "test_" in os.path.basename(f) or f.endswith("_tkstub.py"):
                continue
            try:
                tree = ast.parse(open(f, encoding="utf-8").read())
            except SyntaxError:
                continue
            docs = set()
            for n in ast.walk(tree):
                if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body:
                    b = n.body[0]
                    if isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant):
                        docs.add(id(b.value))
            inner = set()
            for n in ast.walk(tree):
                if isinstance(n, ast.JoinedStr):
                    for v in ast.walk(n):
                        if v is not n:
                            inner.add(id(v))
            for n in ast.walk(tree):
                if id(n) in docs or id(n) in inner:
                    continue
                if isinstance(n, ast.JoinedStr):
                    k, parts = 0, []
                    for v in n.values:
                        if isinstance(v, ast.Constant):
                            parts.append(str(v.value))
                        else:
                            parts.append("{%d}" % k); k += 1
                    t = "".join(parts)
                    if wordy(re.sub(r"\{\d+\}", " ", t)):
                        texts.setdefault(t.strip(), os.path.relpath(f, base))
                elif isinstance(n, ast.Constant) and isinstance(n.value, str):
                    t = n.value
                    if "%" in t and re.search(r"%[-0-9.]*[sdfgr]", t):
                        k = [0]
                        def sub(m):
                            k[0] += 1
                            return "{%d}" % (k[0] - 1)
                        t = re.sub(r"%[-0-9.]*[sdfgr]", sub, t).replace("%%", "%")
                    elif re.search(r"\{[^{}]*\}", t):
                        k = [0]
                        def sub2(m):
                            k[0] += 1
                            return "{%d}" % (k[0] - 1)
                        t = re.sub(r"\{[^{}]*\}", sub2, t)
                    if wordy(re.sub(r"\{\d+\}", " ", t)) and len(t) < 400:
                        texts.setdefault(t.strip(), os.path.relpath(f, base))
    return texts


def known():
    p = os.path.join(WWW, "js", "fa.js")
    if not os.path.exists(p):
        return set()
    src = open(p, encoding="utf-8").read()
    return {t for q, t in literals(src)}


def main():
    texts = py_texts() if "--py" in sys.argv else collect()
    if "--all" not in sys.argv:
        k = known()
        texts = {t: f for t, f in texts.items() if t not in k}
    if "--json" in sys.argv:
        out = sys.argv[sys.argv.index("--json") + 1]
        json.dump([{"en": t, "file": f} for t, f in texts.items()], open(out, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=0)
        print(len(texts), "texts ->", out)
    else:
        for t, f in texts.items():
            print(f"{f}: {t!r}")
        print(len(texts), "texts", file=sys.stderr)


if __name__ == "__main__":
    main()
