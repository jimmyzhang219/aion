"""BootstrapMonitor 单元测试"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from aion.agent.bootstrap_monitor import BootstrapMonitor


class TestBootstrapMonitor:
    def test_parse_audit_yes_line_yes_prefix(self):
        assert BootstrapMonitor._parse_audit_yes_line("YES the response is misleading")
        assert BootstrapMonitor._parse_audit_yes_line("YES")
        assert BootstrapMonitor._parse_audit_yes_line("  YES something")

    def test_parse_audit_yes_line_no_prefix(self):
        assert not BootstrapMonitor._parse_audit_yes_line("NO it's fine")
        assert not BootstrapMonitor._parse_audit_yes_line("Maybe")
        assert not BootstrapMonitor._parse_audit_yes_line("")
        assert not BootstrapMonitor._parse_audit_yes_line("  NO definitely not")

    def test_check_output_for_refresh_delete_bootstrap(self):
        monitor = BootstrapMonitor(Path("/tmp"), "main", MagicMock())
        assert monitor.check_output_for_refresh("delete", "已删除: WORKSPACE_BOOTSTRAP.md")
        assert monitor.check_output_for_refresh("delete", "已删除: AGENT_BOOTSTRAP.md")

    def test_check_output_for_refresh_non_bootstrap(self):
        monitor = BootstrapMonitor(Path("/tmp"), "main", MagicMock())
        assert not monitor.check_output_for_refresh("delete", "已删除: README.md")
        assert not monitor.check_output_for_refresh("delete", "已删除: BOOTSTRAP.md")
        assert not monitor.check_output_for_refresh("write", "已写入 WORKSPACE_BOOTSTRAP.md")
        assert not monitor.check_output_for_refresh("trash", "已移到回收站: Some file")
        assert not monitor.check_output_for_refresh("read", "file content")

    @patch("aion.agent.bootstrap_monitor.get_bootstrap_file_status")
    @pytest.mark.asyncio
    async def test_audit_misclaim_no_pending_skips(self, mock_pending):
        mock_pending.return_value = {"workspace_pending": False, "agent_pending": False}
        mock_llm = MagicMock()
        monitor = BootstrapMonitor(Path("/tmp"), "main", mock_llm)
        result = await monitor.audit_misclaim("一切正常")
        assert result == "一切正常"
        mock_llm.ainvoke.assert_not_called()

    @patch("aion.agent.bootstrap_monitor.get_bootstrap_file_status")
    @pytest.mark.asyncio
    async def test_audit_misclaim_with_pending_yes(self, mock_pending):
        mock_pending.return_value = {"workspace_pending": True, "agent_pending": False}
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="YES"))
        monitor = BootstrapMonitor(Path("/tmp/test_ws"), "main", mock_llm)
        result = await monitor.audit_misclaim("好，初始化完成")
        assert "好，初始化完成" in result
        assert "轻提示" in result
        assert "引导" in result

    @patch("aion.agent.bootstrap_monitor.get_bootstrap_file_status")
    @pytest.mark.asyncio
    async def test_audit_misclaim_with_pending_no(self, mock_pending):
        mock_pending.return_value = {"workspace_pending": True, "agent_pending": False}
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="NO"))
        monitor = BootstrapMonitor(Path("/tmp"), "main", mock_llm)
        result = await monitor.audit_misclaim("今天聊天气")
        assert result == "今天聊天气"
