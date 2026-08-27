"""execute_subagent 单元测试"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


@pytest.mark.asyncio
async def test_execute_subagent_creates_session():
    """execute_subagent 应创建 SubagentSession 并写入初始消息"""
    from aion.agent.loop import AgentLoop

    mock_llm = MagicMock()
    mock_llm.__class__.__name__ = "BaseChatOpenAI"

    loop = AgentLoop.__new__(AgentLoop)
    loop.llm = mock_llm
    loop.agent_id = "main"
    loop.session_id = "parent-session"
    loop.workspace_dir = Path("/tmp")
    loop.memory_config = {}
    loop._mcp_servers = []
    loop._max_tool_rounds = 20
    loop.is_subagent = False
    loop.subagent_depth = 0
    loop.parent_session_id = None
    loop._current_trace_id = ""
    loop.tool_registry = MagicMock()
    loop.tool_registry.build_langchain_tools.return_value = []

    with (
        patch("aion.agent.subagent.session.SubagentSession") as mock_session_cls,
        patch("aion.agent.agent_runner.AgentRunner") as mock_runner_cls,
    ):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        mock_runner = MagicMock()
        mock_runner.run = AsyncMock(return_value=MagicMock(response="done"))
        mock_runner_cls.return_value = mock_runner

        result = await loop.execute_subagent(task="test task")

        mock_session_cls.assert_called_once()
        assert mock_session.append_messages.call_count >= 1
        mock_runner_cls.assert_called_once()
        mock_session.mark_deleted.assert_called_once()
        assert result == "done"


@pytest.mark.asyncio
async def test_execute_subagent_calls_agent_runner():
    """execute_subagent 应调用 AgentRunner 并返回结果"""
    from aion.agent.loop import AgentLoop

    mock_llm = MagicMock()
    mock_llm.__class__.__name__ = "BaseChatOpenAI"

    loop = AgentLoop.__new__(AgentLoop)
    loop.llm = mock_llm
    loop.agent_id = "main"
    loop.session_id = "parent-session"
    loop.workspace_dir = Path("/tmp")
    loop.memory_config = {}
    loop._mcp_servers = []
    loop._max_tool_rounds = 20
    loop.is_subagent = False
    loop.subagent_depth = 0
    loop.parent_session_id = None
    loop._current_trace_id = ""
    loop.tool_registry = MagicMock()
    loop.tool_registry.build_langchain_tools.return_value = []

    with (
        patch("aion.agent.subagent.session.SubagentSession"),
        patch("aion.agent.agent_runner.AgentRunner") as mock_runner_cls,
    ):
        mock_runner = MagicMock()
        mock_runner.run = AsyncMock(return_value=MagicMock(response="ok"))
        mock_runner_cls.return_value = mock_runner

        result = await loop.execute_subagent(task="test")
        assert result == "ok"
