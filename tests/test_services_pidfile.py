"""tests/test_services_pidfile.py"""

from __future__ import annotations

from aion.cli.services.pidfile import PidfileManager


def test_find_pids_returns_set():
    mgr = PidfileManager()
    pids = mgr.find_pids()
    assert isinstance(pids, set)


def test_is_running_returns_bool():
    mgr = PidfileManager()
    assert isinstance(mgr.is_running(), bool)
