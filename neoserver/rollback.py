"""Put back the files of the last update that changed neoserver itself.

Run by systemd (neoserver-rollback.service, OnFailure= of neoserver.service) when the new
version does not start; then neoserver is started again with the old files.

    python3 -m neoserver.rollback [--config /etc/neoden/neoserver.ini]
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from neoserver.core import Files, Log, Settings  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="/etc/neoden/neoserver.ini")
    a = ap.parse_args()
    st = Settings(a.config)
    log = Log(path=os.path.join(st.backup, "neoserver.log"))
    state = os.path.join(st.backup, "last-update.json")
    if not os.path.exists(state):
        log("rollback: no server update to undo", "rollback")
        return 1
    info = json.load(open(state))
    files = Files(st.backup, log)
    log(files.restore(info["backup"], "failed server update") or "nothing to restore", "rollback")
    os.rename(state, state + ".done")
    subprocess.run(["systemctl", "reset-failed", "neoserver"])
    subprocess.run(["systemctl", "start", "neoserver"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
