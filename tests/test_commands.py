"""Slash 命令单元测试"""

from unittest.mock import MagicMock, patch

import pytest

from aion.gateway.commands import handle_slash_command


@pytest.mark.asyncio
async def test_cmd_mode_shows_current():
    """无参数时显示当前模式"""
    ctx = MagicMock()
    ctx.content = "/mode"
    channel = MagicMock()
    config = MagicMock()
    ws_config = MagicMock()
    ws_config.execution_mode = "react"
    config.get_workspace.return_value = ws_config

    result = await handle_slash_command(
        ctx=ctx,
        channel=channel,
        config=config,
        workspace_name="default",
        agent_id="main",
        session_loops={},
    )
    assert result is not None
    assert result.command_handled
    assert "react" in (result.command_response or "") or "react" in (result.response or "")


@pytest.mark.asyncio
async def test_cmd_mode_switch_to_plan():
    """/mode plan 切换模式并持久化"""
    ctx = MagicMock()
    ctx.content = "/mode plan"
    channel = MagicMock()
    config = MagicMock()
    ws_config = MagicMock()
    ws_config.execution_mode = "react"
    config.get_workspace.return_value = ws_config

    with patch("aion.gateway.commands.save_config") as mock_save:
        result = await handle_slash_command(
            ctx=ctx,
            channel=channel,
            config=config,
            workspace_name="default",
            agent_id="main",
            session_loops={},
        )
    assert result is not None
    assert result.command_handled
    assert ws_config.execution_mode == "plan"
    mock_save.assert_called_once()


@pytest.mark.asyncio
async def test_cmd_mode_switch_to_react():
    """/mode react 切换回 ReAct 模式"""
    ctx = MagicMock()
    ctx.content = "/mode react"
    channel = MagicMock()
    config = MagicMock()
    ws_config = MagicMock()
    ws_config.execution_mode = "plan"
    config.get_workspace.return_value = ws_config

    with patch("aion.gateway.commands.save_config") as mock_save:
        result = await handle_slash_command(
            ctx=ctx,
            channel=channel,
            config=config,
            workspace_name="default",
            agent_id="main",
            session_loops={},
        )
    assert result is not None
    assert result.command_handled
    assert ws_config.execution_mode == "react"
    mock_save.assert_called_once()
