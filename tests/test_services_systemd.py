"""tests/test_services_systemd.py"""

from __future__ import annotations

import sys

import pytest


@pytest.mark.skipif(sys.platform != "linux", reason="Linux only")
class TestSystemdManager:
    def test_find_pids_returns_set(self):
        from aion.cli.services.systemd import SystemdManager

        mgr = SystemdManager()
        pids = mgr.find_pids()
        assert isinstance(pids, set)

    def test_generate_unit_content(self):
        from aion.cli.services.systemd import SystemdManager

        content = SystemdManager._generate_unit_content()
        assert "[Unit]" in content
        assert "Description=Aion Gateway" in content
        assert "[Service]" in content
        assert "[Install]" in content
        assert "WantedBy=default.target" in content
