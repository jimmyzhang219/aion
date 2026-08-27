"""dispatch_message 多模态能力检测测试"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from unittest.mock import AsyncMock, MagicMock, patch


class TestDispatchMultimodalCheck:
    """dispatch_message 多模态能力检测测试"""

    @patch("aion.gateway.dispatch.load_config")
    @patch("aion.gateway.dispatch.check_modality_support")
    @patch("aion.gateway.dispatch.SessionBinder")
    @patch("aion.gateway.dispatch.generate_traceid", return_value="test-trace")
    @patch("aion.gateway.dispatch.set_traceid")
    @patch("aion.gateway.dispatch.reset_traceid")
    @patch("aion.gateway.dispatch.enqueue", new_callable=AsyncMock)
    async def test_multimodal_unsupported_returns_hint(
        self,
        mock_enqueue,
        mock_reset,
        mock_set,
        mock_traceid,
        mock_binder,
        mock_check,
        mock_config,
    ):
        """content list 包含 image 但模型不支持 → 返回提示"""
        from aion.channels.types import MessageContext
        from aion.gateway.dispatch import dispatch_message

        # Mock: 模型不支持 image
        mock_check.return_value = (False, {"image"})

        # Mock config
        mock_cfg = MagicMock()
        mock_ws = MagicMock()
        mock_ws.get_leader.return_value = "main"
        mock_ws.get_agent_config.return_value = {"provider": "deepseek"}
        mock_ws.execution_mode = "react"
        mock_cfg.get_workspace.return_value = mock_ws
        mock_cfg.get_model_config.return_value = {"model": "deepseek-v4-flash"}
        mock_config.return_value = mock_cfg

        # Mock SessionBinder
        mock_binder_instance = AsyncMock()
        mock_binder_instance.get_or_create_session_id.return_value = "test-session"
        mock_binder.return_value = mock_binder_instance

        ctx = MessageContext(
            channel_id="test",
            chat_id="chat1",
            message_id="msg1",
            sender_id="user1",
            content=[{"type": "image", "data": "/tmp/img.jpg", "mimeType": "image/jpeg"}],
            workspace_dir=Path("/tmp/ws"),
        )

        result = await dispatch_message(ctx, MagicMock())

        # 应返回提示且不进队列
        assert result.command_response != ""
        assert "不支持" in result.command_response
        mock_enqueue.assert_not_called()

    @patch("aion.gateway.dispatch.load_config")
    @patch("aion.gateway.dispatch.check_modality_support")
    @patch("aion.gateway.dispatch.SessionBinder")
    @patch("aion.gateway.dispatch.generate_traceid", return_value="test-trace")
    @patch("aion.gateway.dispatch.set_traceid")
    @patch("aion.gateway.dispatch.reset_traceid")
    @patch("aion.gateway.dispatch.enqueue", new_callable=AsyncMock)
    async def test_multimodal_supported_passes_through(
        self,
        mock_enqueue,
        mock_reset,
        mock_set,
        mock_traceid,
        mock_binder,
        mock_check,
        mock_config,
    ):
        """content list 包含 image 且模型支持 → 正常入队"""
        from aion.channels.types import MessageContext
        from aion.gateway.dispatch import dispatch_message

        # Mock: 模型支持 image
        mock_check.return_value = (True, set())

        mock_cfg = MagicMock()
        mock_ws = MagicMock()
        mock_ws.get_leader.return_value = "main"
        mock_ws.get_agent_config.return_value = {"provider": "qwen"}
        mock_ws.execution_mode = "react"
        mock_cfg.get_workspace.return_value = mock_ws
        mock_cfg.get_model_config.return_value = {"model": "qwen3.7-plus"}
        mock_config.return_value = mock_cfg

        mock_binder_instance = AsyncMock()
        mock_binder_instance.get_or_create_session_id.return_value = "test-session"
        mock_binder.return_value = mock_binder_instance

        ctx = MessageContext(
            channel_id="test",
            chat_id="chat1",
            message_id="msg1",
            sender_id="user1",
            content=[{"type": "image", "data": "/tmp/img.jpg", "mimeType": "image/jpeg"}],
            workspace_dir=Path("/tmp/ws"),
        )

        result = await dispatch_message(ctx, MagicMock())

        # 应正常入队
        mock_enqueue.assert_called_once()
        assert result.error is None or result.error == ""

    @patch("aion.gateway.dispatch.load_config")
    @patch("aion.gateway.dispatch.generate_traceid", return_value="test-trace")
    @patch("aion.gateway.dispatch.set_traceid")
    @patch("aion.gateway.dispatch.reset_traceid")
    @patch("aion.gateway.dispatch.enqueue", new_callable=AsyncMock)
    @patch("aion.gateway.dispatch.handle_slash_command", new_callable=AsyncMock)
    @patch("aion.gateway.dispatch.SessionBinder")
    async def test_text_message_skips_check(
        self,
        mock_binder,
        mock_slash,
        mock_enqueue,
        mock_reset,
        mock_set,
        mock_traceid,
        mock_config,
    ):
        """纯文本 str content 跳过能力检测"""
        from aion.channels.types import MessageContext
        from aion.gateway.dispatch import dispatch_message

        mock_cfg = MagicMock()
        mock_ws = MagicMock()
        mock_ws.execution_mode = "react"
        mock_ws.get_leader.return_value = "main"
        mock_cfg.get_workspace.return_value = mock_ws
        mock_config.return_value = mock_cfg

        mock_slash.return_value = None

        mock_binder_instance = AsyncMock()
        mock_binder_instance.get_or_create_session_id.return_value = "test-session"
        mock_binder.return_value = mock_binder_instance

        ctx = MessageContext(
            channel_id="test",
            chat_id="chat1",
            message_id="msg1",
            sender_id="user1",
            content="hello",
            workspace_dir=Path("/tmp/ws"),
        )

        await dispatch_message(ctx, MagicMock())

        # 正常入队
        mock_enqueue.assert_called_once()
