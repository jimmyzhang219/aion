"""tests/test_services_launchd.py"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, mock_open, patch

import pytest


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
class TestLaunchdManager:
    def test_find_pids_returns_set(self):
        from aion.cli.services.launchd import LaunchdManager

        mgr = LaunchdManager()
        pids = mgr.find_pids()
        assert isinstance(pids, set)
        # 即使无运行进程也返回空 set，不抛异常

    def test_stop_without_pids_returns_false(self):
        """空 PID 集合调用 stop 应返回 False"""
        from aion.cli.services.launchd import LaunchdManager
        import asyncio

        mgr = LaunchdManager()
        result = asyncio.run(mgr.stop(set()))
        assert result is False

    def test_is_running_returns_bool(self):
        """is_running 应返回 bool"""
        from aion.cli.services.launchd import LaunchdManager

        mgr = LaunchdManager()
        result = mgr.is_running()
        assert isinstance(result, bool)

    @patch("aion.cli.services.launchd.os.makedirs")
    @patch("aion.cli.services.launchd.subprocess.run")
    @patch("aion.cli.services.launchd.PLIST_PATH")
    def test_start_calls_launchctl_bootstrap(self, mock_plist_path, mock_run, mock_makedirs):
        """start() 应调用 launchctl bootstrap（当服务未加载时）"""
        from aion.cli.services.launchd import LaunchdManager
        import asyncio
        import json

        # Mock plist path -- make __fspath__ work so os.path.dirname doesn't fail
        mock_plist_path.exists.return_value = False
        mock_plist_path.__fspath__.return_value = "/tmp/com.user.aion.gateway.plist"
        mock_plist_path.parent = MagicMock()

        # Mock launchctl list returns non-zero (not loaded)
        mock_fail = MagicMock()
        mock_fail.returncode = 1
        # Mock launchctl bootstrap returns 0 (success)
        mock_ok = MagicMock()
        mock_ok.returncode = 0

        def mock_subprocess_run(cmd, **kwargs):
            if "list" in cmd:
                return mock_fail
            return mock_ok

        mock_run.side_effect = mock_subprocess_run

        config_json = json.dumps({"gateway": {"port": 19527}})
        with patch("builtins.open", mock_open(read_data=config_json)):
            mgr = LaunchdManager()
            result = asyncio.run(mgr.start())

        assert result is True
        # 应调用 bootstrap
        bootstrap_calls = [c for c in mock_run.call_args_list if "bootstrap" in str(c)]
        assert len(bootstrap_calls) >= 1
