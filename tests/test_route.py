"""neojob.route: the job's list re-ordered for less head travel, lower parts first.

    python tests/test_route.py
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from neocam.config import NeoConfig  # noqa: E402
from neojob import load_job, route as R  # noqa: E402

HERE = os.path.join(os.path.dirname(__file__), "data", "sup1214")


def test_sup1214_sorted():
    if not os.path.exists(os.path.join(os.path.dirname(__file__), "data", "sup1214", "job.csv")):
        import pytest
        pytest.skip("the board's data is not in this copy")
    job, cfg = load_job(os.path.join(HERE, "job.csv")), NeoConfig.load(os.path.join(HERE, "Config.ini"))
    d0, c0 = R.travel(job, cfg)
    order = R.sort(job, cfg)
    idx = R.placeable(job)
    assert sorted(order) == sorted(idx)                               # every placeable part once
    m = R.Model(job, cfg)
    hs = [m.height(i) for i in order]
    low = [h for h in hs if h < 2.0]
    # height tiers: a part is never placed before one more than TIER_MM lower than it
    for k, h in enumerate(low):
        assert all(h - x <= R.TIER_MM + 1e-9 for x in low[k + 1:]), (k, h)
    assert all(h >= 2.0 for h in hs[len(low):])                        # the tall ones last
    raw = open(os.path.join(HERE, "job.csv"), encoding="utf-8", newline="").read()
    lines = raw.split("\r\n")
    new = R.reorder_lines(lines, job, order)
    assert sorted(new) == sorted(lines) and len(new) == len(lines)     # the same lines, only moved
    assert [l for l in new if not l.startswith("comp,")] == [l for l in lines if not l.startswith("comp,")]
    assert all((a.startswith("comp,")) == (b.startswith("comp,")) for a, b in zip(lines, new))
    import tempfile
    p = os.path.join(tempfile.mkdtemp(), "job.csv")
    open(p, "w", encoding="utf-8", newline="").write("\r\n".join(new))
    d1, c1 = R.travel(load_job(p), cfg)
    assert d1 < 0.95 * d0, (d0, d1)
    return d0, c0, d1, c1


if __name__ == "__main__":
    d0, c0, d1, c1 = test_sup1214_sorted()
    print(f"ok BOARD-A: {d0 / 1000:.1f} m in {c0} cycles -> {d1 / 1000:.1f} m in {c1} ({(1 - d1 / d0) * 100:.0f} % less)")
