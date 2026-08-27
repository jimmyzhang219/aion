"""ToolRegistry 单元测试"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aion.agent.tool_registry import ToolRegistry


class TestToolRegistry:
    """ToolRegistry 基础功能测试"""

    def test_register_all_returns_dict(self):
        """register_all 应返回 dict"""
        mock_loop = MagicMock()
        mock_loop.is_subagent = False
        registry = ToolRegistry(
            agent_loop=mock_loop,
            is_subagent=False,
        )
        tools = registry.register_all()
        assert isinstance(tools, dict)

    def test_register_all_includes_basic_tools(self):
        """register_all 返回的 tools 应包含 read/write/edit/exec"""
        mock_loop = MagicMock()
        mock_loop.is_subagent = False
        registry = ToolRegistry(
            agent_loop=mock_loop,
            is_subagent=False,
        )
        tools = registry.register_all()
        for name in ("read", "write", "edit", "exec"):
            assert name in tools, f"Missing tool: {name}"

    def test_trash_delete_wrapped_with_bootstrap_guard(self):
        """trash/delete 应被包装（检查函数引用是否改变）"""
        mock_loop = MagicMock()
        mock_loop.is_subagent = False
        from aion.tools.builtin.trash import trash as _trash_tool
        from aion.tools.builtin.delete import delete as _delete_tool

        registry = ToolRegistry(
            agent_loop=mock_loop,
            is_subagent=False,
        )
        tools = registry.register_all()
        assert tools["trash"] is not _trash_tool.func
        assert tools["delete"] is not _delete_tool.func

    def test_subagent_excludes_main_agent_tools(self):
        """subagent 的 build_langchain_tools 不应包含 sessions_spawn"""
        mock_loop = MagicMock()
        mock_loop.is_subagent = True
        registry = ToolRegistry(
            agent_loop=mock_loop,
            is_subagent=True,
        )
        registry.register_all()
        tools = registry.build_langchain_tools()
        tool_names = [t.name for t in tools]
        assert "sessions_spawn" not in tool_names

    def test_main_agent_includes_sessions_spawn(self):
        """主 agent 的 build_langchain_tools 应包含 sessions_spawn（async tool）"""
        mock_loop = MagicMock()
        mock_loop.is_subagent = False
        registry = ToolRegistry(
            agent_loop=mock_loop,
            is_subagent=False,
        )
        registry.register_all()
        tools = registry.build_langchain_tools()
        tool_names = [t.name for t in tools]
        assert "sessions_spawn" in tool_names, "sessions_spawn 不在工具列表中（async tool 可能被 func is None 跳过）"

    def test_subagent_tools_not_registered_when_is_subagent(self):
        """subagent 模式 sessions_spawn 不可见"""
        mock_loop = MagicMock()
        mock_loop.is_subagent = True
        registry = ToolRegistry(
            agent_loop=mock_loop,
            is_subagent=True,
        )
        registry.register_all()
        tools = registry.build_langchain_tools()
        names = [t.name for t in tools]
        assert "sessions_spawn" not in names

    def test_build_langchain_tools(self):
        """build_langchain_tools 应返回 StructuredTool 列表"""
        mock_loop = MagicMock()
        mock_loop.is_subagent = False
        registry = ToolRegistry(
            agent_loop=mock_loop,
            is_subagent=False,
        )
        registry.register_all()
        from langchain_core.tools import StructuredTool

        result = registry.build_langchain_tools()
        assert isinstance(result, list)
        assert len(result) > 0
        assert isinstance(result[0], StructuredTool)

    def test_register_mcp_tools(self):
        """register_mcp_tools 应更新 _extra_tools"""
        mock_loop = MagicMock()
        mock_loop.is_subagent = False
        registry = ToolRegistry(
            agent_loop=mock_loop,
            is_subagent=False,
        )
        registry.register_mcp_tools({"mcp_read": lambda: "mcp"})
        tools = registry.get_all_tools()
        assert "mcp_read" in tools

    def test_register_mcp_structured_tools(self):
        """注册 MCP StructuredTool 后 build_langchain_tools 应包含它们"""
        from langchain_core.tools import StructuredTool

        async def _dummy_call(**kwargs) -> str:
            return "result"

        mock_loop = MagicMock()
        mock_loop.is_subagent = False
        registry = ToolRegistry(
            agent_loop=mock_loop,
            is_subagent=False,
        )
        registry.register_all()
        tool = StructuredTool.from_function(
            name="test_mcp_tool",
            description="test",
            coroutine=_dummy_call,
        )
        registry.register_mcp_structured_tools([tool])
        result = registry.build_langchain_tools()
        names = [t.name for t in result]
        assert "test_mcp_tool" in names
