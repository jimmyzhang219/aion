"""tests/test_dispatch_queue.py"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from aion.channels.types import MessageContext


@pytest.mark.asyncio
@patch("aion.gateway.dispatch.enqueue", new_callable=AsyncMock)
async def test_dispatch_message_enqueue_and_wait(mock_enqueue):
    """验证 dispatch_message 入队后返回轻量 ack（不再阻塞等待 Worker 结果）。"""
    mock_config = MagicMock()
    mock_config.workspaces.current = "default"
    mock_config.get_workspace.return_value.get_leader.return_value = "main"

    mock_binder = MagicMock()
    mock_binder.get_or_create_session_id.return_value = "test-sess-id"

    mock_channel = MagicMock()
    mock_channel.build_session_key.return_value = "test-key"

    ctx = MessageContext(
        channel_id="test",
        chat_id="chat1",
        message_id="mid-0011223344556677",
        sender_id="user1",
        content="hello",
        chat_type="p2p",
        workspace_dir=Path("/fake/workspaces/default"),
    )

    import aion.gateway.dispatch as dispatch_mod

    with (
        patch.object(dispatch_mod, "load_config", return_value=mock_config),
        patch.object(dispatch_mod, "SessionBinder", return_value=mock_binder),
    ):
        from aion.gateway.dispatch import dispatch_message

        result = await dispatch_message(ctx=ctx, channel=mock_channel)

    assert result is not None, "Should return DispatchResult"
    assert result.session_id == "test-sess-id"
    # result.response should NOT be set (only lightweight ack)
    assert result.response == ""
    mock_enqueue.assert_called_once()
