"""Re-copy the KFG Pick & Place Tool into neosetup/engine/ and record where it came from.

    python sync_engine.py [path to csv_flip_tool.py]

The tool keeps evolving in its own repository; neosetup runs an unchanged copy of it
(engine/csv_flip_tool.py). This script copies it again, and writes engine/SOURCE.txt with
the source path, the git commit of the source folder (and whether the file had
uncommitted changes), the file's SHA-256 and line count. Run the neosetup tests afterwards
(tests/run_all.py): a change in the tool's behaviour shows up as a golden-test difference.

The default source path is the one in engine/SOURCE.txt, or the KFG_TOOL environment
variable.
"""
import datetime
import hashlib
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_DIR = os.path.join(HERE, "engine")
ENGINE = os.path.join(ENGINE_DIR, "csv_flip_tool.py")
SOURCE_TXT = os.path.join(ENGINE_DIR, "SOURCE.txt")


def read_source_info(path=SOURCE_TXT):
    """The key: value lines of SOURCE.txt as a dict ({} if there is none)."""
    info = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if ":" in line and not line.startswith("#"):
                    k, v = line.split(":", 1)
                    info[k.strip()] = v.strip()
    except OSError:
        pass
    return info


def _git(folder, *args):
    try:
        p = subprocess.run(["git", "-C", folder] + list(args), capture_output=True,
                           text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def sync(source):
    source = os.path.abspath(source)
    if not os.path.isfile(source):
        raise SystemExit("not found: %s" % source)
    folder = os.path.dirname(source)
    commit = _git(folder, "rev-parse", "HEAD") or "unknown (not a git checkout)"
    dirty = _git(folder, "status", "--porcelain", "--", os.path.basename(source))
    old = sha256(ENGINE) if os.path.exists(ENGINE) else None
    os.makedirs(ENGINE_DIR, exist_ok=True)
    shutil.copyfile(source, ENGINE)
    digest = sha256(ENGINE)
    with open(ENGINE, "rb") as fh:
        lines = fh.read().count(b"\n")
    with open(SOURCE_TXT, "w", encoding="utf-8") as fh:
        fh.write("# Where engine/csv_flip_tool.py came from. Written by sync_engine.py --\n"
                 "# do not edit the copy; change the tool in its own repository and re-sync.\n")
        fh.write("source: %s\n" % source)
        fh.write("commit: %s\n" % commit)
        fh.write("uncommitted changes: %s\n" % ("yes" if dirty else "no"))
        fh.write("sha256: %s\n" % digest)
        fh.write("lines: %d\n" % lines)
        fh.write("synced: %s\n" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return {"source": source, "commit": commit, "changed": old != digest, "sha256": digest,
            "dirty": bool(dirty)}


def main(argv):
    source = (argv[1] if len(argv) > 1 else os.environ.get("KFG_TOOL")
              or read_source_info().get("source"))
    if not source:
        raise SystemExit(__doc__)
    r = sync(source)
    print("engine %s from %s" % ("updated" if r["changed"] else "unchanged", r["source"]))
    print("commit %s%s" % (r["commit"], "  (file has uncommitted changes)" if r["dirty"] else ""))
    print("sha256 %s" % r["sha256"])
    if r["changed"]:
        print("Run the tests: python %s" % os.path.join(HERE, "tests", "run_all.py"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
