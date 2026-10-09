"""CI must not pass by silently skipping native dependencies or tests."""
import os
from pathlib import Path

import pytest

_strict_skips = []


def pytest_sessionstart(session):
    _strict_skips.clear()
    if os.environ.get("TAILTRAY_TEST_INSTALLED") == "1":
        import tailtray

        if Path(tailtray.__file__).resolve().parent != Path("/usr/lib/tailtray/tailtray"):
            raise pytest.UsageError("Package tests imported checkout code instead of the installed package")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_logreport(report):
    if os.environ.get("TAILTRAY_TEST_STRICT") == "1" and report.skipped:
        _strict_skips.append(report.nodeid)
        # Pytest will count this as failure and emit its reason in JUnit, even
        # for unittest.skipTest. Missing GTK/Qt/KDE/D-Bus is a CI failure.
        report.outcome = "failed"
        report.longrepr = f"Unexpected CI skip in {report.nodeid}: {report.longrepr}"


def pytest_sessionfinish(session, exitstatus):
    # A rewritten report alone is insufficient: unittest/pytest can still
    # return success because the original call was skipped. Enforce the gate.
    if _strict_skips:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
