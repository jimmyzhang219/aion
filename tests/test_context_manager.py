"""ContextManager 单元测试"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from aion.agent.context_manager import ContextManager, _extract_indexable_text


class TestContextManager:
    @pytest.mark.asyncio
    async def test_add_user_and_get_messages(self, tmp_path):
        mock_llm = MagicMock()
        cm = ContextManager(
            llm=mock_llm,
            session_id="test",
            workspace_dir=tmp_path,
            agent_id="main",
            context_window_tokens=200000,
        )
        cm.add_user("Hello")
        msgs = cm.get_messages()
        assert len(msgs) >= 1
        user_msgs = [m for m in msgs if m.get("role") == "user"]
        assert any("Hello" in m.get("content", "") for m in user_msgs)

    @pytest.mark.asyncio
    async def test_add_assistant(self, tmp_path):
        mock_llm = MagicMock()
        cm = ContextManager(
            llm=mock_llm,
            session_id="test",
            workspace_dir=tmp_path,
            agent_id="main",
            context_window_tokens=200000,
        )
        cm.add_assistant("Hi there")
        msgs = cm.get_messages()
        assistant_msgs = [m for m in msgs if m.get("role") == "assistant"]
        assert any("Hi there" in m.get("content", "") for m in assistant_msgs)

    def test_append_usage(self, tmp_path):
        mock_llm = MagicMock()
        cm = ContextManager(
            llm=mock_llm,
            session_id="test",
            workspace_dir=tmp_path,
            agent_id="main",
            context_window_tokens=200000,
        )
        cm.append_usage({"input_tokens": 10, "output_tokens": 20, "total_tokens": 30})
        cm.append_usage({"input_tokens": 5, "output_tokens": 10, "total_tokens": 15})
        assert cm.accumulated_usage["input_tokens"] == 15
        assert cm.accumulated_usage["output_tokens"] == 30
        assert cm.accumulated_usage["total_tokens"] == 45

    def test_reset_clears_messages(self, tmp_path):
        mock_llm = MagicMock()
        cm = ContextManager(
            llm=mock_llm,
            session_id="test",
            workspace_dir=tmp_path,
            agent_id="main",
            context_window_tokens=200000,
        )
        cm.add_user("hello")
        assert len(cm.get_messages()) >= 1
        cm.reset()
        msgs = cm.get_messages()
        # reset 应保留 system 消息
        assert len(msgs) >= 1
        assert msgs[0].get("role") == "system"

    @patch("aion.agent.context_manager.count_message_tokens")
    @pytest.mark.asyncio
    async def test_compact_if_needed_noop_when_below_threshold(self, mock_count, tmp_path):
        mock_count.return_value = 1000
        mock_llm = AsyncMock()
        mock_llm.model = "mock"
        cm = ContextManager(
            llm=mock_llm,
            session_id="test",
            workspace_dir=tmp_path,
            agent_id="main",
            context_window_tokens=200000,
        )
        messages = cm.get_messages()
        result, meta = await cm.compact_if_needed(messages)
        assert meta is None  # 未触发

    @pytest.mark.asyncio
    async def test_prune_returns_list(self, tmp_path):
        mock_llm = MagicMock()
        cm = ContextManager(
            llm=mock_llm,
            session_id="test",
            workspace_dir=tmp_path,
            agent_id="main",
            context_window_tokens=200000,
        )
        messages = cm.get_messages()
        result = cm.prune(messages)
        assert isinstance(result, list)

    def test_session_id_property(self, tmp_path):
        mock_llm = MagicMock()
        cm = ContextManager(
            llm=mock_llm,
            session_id="test-session",
            workspace_dir=tmp_path,
            agent_id="main",
            context_window_tokens=200000,
        )
        assert cm.session_id == "test-session"


class TestExtractIndexableText:
    """_extract_indexable_text 过滤非文本内容测试"""

    def test_str_passthrough(self):
        """纯字符串应原样返回"""
        result = _extract_indexable_text("hello world")
        assert result == "hello world"

    def test_empty_str(self):
        """空字符串返回空字符串"""
        result = _extract_indexable_text("")
        assert result == ""

    def test_text_only_blocks(self):
        """仅含 text 块时应拼接"""
        content = [
            {"type": "text", "text": "分析一下"},
            {"type": "text", "text": "这张图片"},
        ]
        result = _extract_indexable_text(content)
        assert result == "分析一下 这张图片"

    def test_mixed_text_and_image(self):
        """混合 text 和 image 时只保留 text"""
        content = [
            {"type": "text", "text": "分析一下"},
            {"type": "image", "data": "/9j/4AAQ...", "mimeType": "image/jpeg"},
        ]
        result = _extract_indexable_text(content)
        assert result == "分析一下"

    def test_image_only(self):
        """仅有 image 块时应返回空字符串"""
        content = [
            {"type": "image", "data": "/9j/4AAQ...", "mimeType": "image/jpeg"},
        ]
        result = _extract_indexable_text(content)
        assert result == ""

    def test_mixed_with_video_and_audio(self):
        """video/audio 等非 text 块应被过滤"""
        content = [
            {"type": "text", "text": "看这个视频"},
            {"type": "video", "data": "AAAA...", "mimeType": "video/mp4"},
            {"type": "audio", "data": "BBBB...", "mimeType": "audio/mp3"},
        ]
        result = _extract_indexable_text(content)
        assert result == "看这个视频"

    def test_empty_list(self):
        """空列表返回空字符串"""
        result = _extract_indexable_text([])
        assert result == ""

    def test_text_block_with_extra_keys(self):
        """text 块有其他额外字段时应能正确提取"""
        content = [
            {"type": "text", "text": "hello", "extra": "ignored"},
        ]
        result = _extract_indexable_text(content)
        assert result == "hello"
