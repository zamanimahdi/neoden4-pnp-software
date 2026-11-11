"""Tests that need the original machine data ($NEODEN_DATA: Config.ini, Proc/*.csv) or a real
board's files are skipped when those are not on this PC."""
import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    out = yield
    e = out.excinfo[1] if out.excinfo else None
    if isinstance(e, FileNotFoundError):
        out.force_exception(pytest.skip.Exception(f"needs data not in this repository: {e.filename}"))
