"""M8 会话压缩（Compaction）单元测试

测试 token 阈值判断 should_compact、compact 缩减消息条数、
保留 system/最近消息，以及检查点元数据记录。
"""

import pytest
from pathlib import Path
import sys

# 将项目 src 加入导入路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aion.session.compaction import Compaction


from langchain_core.messages import AIMessage


class MockLLM:
    """用于压缩测试的 Mock LLM

    ainvoke 始终返回固定的 summary_response 文本，供 Compaction 摘要流程验证。
    """

    def __init__(self, summary_response="这是一段对话摘要"):
        """初始化固定摘要响应文本"""
        self.summary_response = summary_response
        self.chat_count = 0

    async def ainvoke(self, messages, **kwargs):
        """返回预设摘要"""
        self.chat_count += 1
        return AIMessage(content=self.summary_response)


class TestCompaction:
    """Compaction 阈值与压缩行为测试"""

    def test_should_compact_below_threshold(self, tmp_path):
        """当前 token 低于 trigger_ratio 时不应压缩

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        llm = MockLLM()
        compaction = Compaction(llm, session_id="test", sessions_dir=tmp_path, trigger_ratio=0.8)

        assert compaction.should_compact(50000, 100000) is False
        assert compaction.should_compact(79000, 100000) is False

    def test_should_compact_at_threshold(self, tmp_path):
        """达到 80% 阈值时应触发压缩

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        llm = MockLLM()
        compaction = Compaction(llm, session_id="test", sessions_dir=tmp_path, trigger_ratio=0.8)

        assert compaction.should_compact(80000, 100000) is True

    def test_should_compact_above_threshold(self, tmp_path):
        """超过阈值时应触发压缩

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        llm = MockLLM()
        compaction = Compaction(llm, session_id="test", sessions_dir=tmp_path, trigger_ratio=0.8)

        assert compaction.should_compact(90000, 100000) is True

    def test_should_compact_zero_max(self, tmp_path):
        """max_tokens 为 0 时不应压缩（避免除零）

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        llm = MockLLM()
        compaction = Compaction(llm, session_id="test", sessions_dir=tmp_path, trigger_ratio=0.8)

        assert compaction.should_compact(50000, 0) is False

    @pytest.mark.asyncio
    async def test_compact_reduces_messages(self, tmp_path):
        """compact 后消息条数应少于原始并写入元数据

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        llm = MockLLM("压缩后的摘要：用户和张三讨论了工作")
        compaction = Compaction(llm, session_id="test-compact", sessions_dir=tmp_path)

        messages = [{"role": "user", "content": f"消息 {i}"} for i in range(20)]

        compressed, metadata = await compaction.compact(messages)

        assert len(compressed) < len(messages)
        assert metadata["original_count"] == 20
        assert metadata["compressed_count"] == len(compressed)
        assert "checkpoint_uuid" in metadata

    @pytest.mark.asyncio
    async def test_compact_preserves_system_and_recent(self, tmp_path):
        """压缩后应保留 system 首条并含 is_compaction 标记消息

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        llm = MockLLM("摘要内容")
        compaction = Compaction(llm, session_id="test", sessions_dir=tmp_path)

        messages = [{"role": "system", "content": "System prompt"}]
        messages.extend([{"role": "user", "content": f"消息 {i}"} for i in range(10)])

        compressed, _ = await compaction.compact(messages)

        assert compressed[0]["role"] == "system"
        has_marker = any(m.get("is_compaction") for m in compressed)
        assert has_marker

    @pytest.mark.asyncio
    async def test_compact_with_system_message(self, tmp_path):
        """含 system 的短对话压缩后首条仍为 system

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        llm = MockLLM("摘要")
        compaction = Compaction(llm, session_id="test", sessions_dir=tmp_path)

        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]

        compressed, metadata = await compaction.compact(messages)

        assert metadata["original_count"] == 3
        assert compressed[0]["role"] == "system"

    @pytest.mark.asyncio
    async def test_compact_records_checkpoint(self, tmp_path):
        """压缩后 compaction_checkpoints 应记录 checkpoint_uuid

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        llm = MockLLM("摘要内容")
        compaction = Compaction(llm, session_id="test", sessions_dir=tmp_path)

        messages = [{"role": "user", "content": "test"}]
        await compaction.compact(messages)

        assert len(compaction.compaction_checkpoints) == 1
        assert "checkpoint_uuid" in compaction.compaction_checkpoints[0]
