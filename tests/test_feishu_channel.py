"""FeishuChannel 单元测试

覆盖消息处理路径，尤其是斜杠命令的同步响应和常规消息的异步路径。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.aion.channels.types import DispatchResult


@pytest.fixture
def mock_config():
    """创建一个最小可用的 FeishuConfig mock。"""
    cfg = MagicMock()
    cfg.app_id = "test_app"
    cfg.app_secret = "test_secret"
    cfg.domain = "feishu"
    cfg.connection_mode = "websocket"
    cfg.get_active_account.return_value = MagicMock()
    return cfg


@pytest.fixture
def channel(mock_config):
    """创建一个 FeishuChannel 实例，用 mock 替代真实依赖。"""
    from src.aion.channels.feishu.channel import FeishuChannel

    ch = FeishuChannel(config=mock_config)
    # 设置 gateway（_handle_message 检查 self._gateway）
    ch._gateway = MagicMock()
    # mock send_message 以验证响应发送
    ch.send_message = AsyncMock(return_value=MagicMock(message_id="mock-msg-id"))
    return ch


@pytest.fixture
def feishu_ctx():
    """创建一个最小 FeishuMessageContext。"""
    from src.aion.channels.feishu.types import FeishuMessageContext

    return FeishuMessageContext(
        chat_id="chat_xxx",
        message_id="msg_xxx",
        sender_id="user_xxx",
        sender_open_id="open_id_xxx",
        content="/workspaces",
        chat_type="p2p",
    )


@pytest.mark.asyncio
async def test_slash_command_returns_response(channel, feishu_ctx):
    """验证斜杠命令返回的 DispatchResult 通过 channel.respond() 发送。"""
    from src.aion.channels.feishu.channel import FeishuChannel

    # mock dispatch_message 返回一个模拟的斜杠命令响应
    cmd_result = DispatchResult(
        command_handled=True,
        command_response="**可用工作空间：**\n  • test (当前)",
        session_id="test-sess",
    )

    with patch(
        "src.aion.gateway.dispatch.dispatch_message",
        new_callable=AsyncMock,
        return_value=cmd_result,
    ) as mock_dispatch:
        await channel._handle_message(feishu_ctx)

        # dispatch_message 应被调用
        mock_dispatch.assert_awaited_once()

        # send_message 应被调用（通过 respond）
        channel.send_message.assert_awaited_once()
        # 验证发送内容包含命令响应文本
        call_args = channel.send_message.await_args
        assert call_args is not None
        assert call_args.kwargs.get("content") == cmd_result.command_response


@pytest.mark.asyncio
async def test_normal_message_does_not_respond(channel, feishu_ctx):
    """验证非命令消息不会在 _handle_message 中触发 respond。"""
    # 模拟常规消息（非斜杠命令）
    feishu_ctx.content = "hello"
    ack_result = DispatchResult(session_id="test-sess")

    with patch(
        "src.aion.gateway.dispatch.dispatch_message",
        new_callable=AsyncMock,
        return_value=ack_result,
    ) as mock_dispatch:
        await channel._handle_message(feishu_ctx)

        mock_dispatch.assert_awaited_once()
        # 非命令消息不应在 _handle_message 中发送响应
        channel.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_error_returns_none(channel, feishu_ctx):
    """验证当 gateway 未设置时 _handle_message 返回 None。"""
    channel._gateway = None
    result = await channel._handle_message(feishu_ctx)
    assert result is None
