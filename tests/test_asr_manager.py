"""ASRManager 测试

覆盖：
- 初始状态（未运行、session_id 为空）
- 未 start 直接 run 抛 ASRError
- 未 start 直接 stop 安全
- session_id 格式
- _snapshot_config 降级行为
- 前台模式 run 格式化输出
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aion.audio.manager import ASRManager, ASRError, _next_session_id


class TestASRManager:
    def test_not_running_initially(self):
        mgr = ASRManager()
        assert mgr.is_running is False
        assert mgr.session_id == ""

    @pytest.mark.asyncio
    async def test_run_without_start_raises(self):
        mgr = ASRManager()
        with pytest.raises(ASRError, match="call start_*"):
            async for _ in mgr.run():
                pass

    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        mgr = ASRManager()
        await mgr.stop()  # should not raise

    def test_foreground_mode_defaults_to_false(self):
        mgr = ASRManager()
        assert mgr._foreground is False

    def test_foreground_mode_explicit(self):
        mgr = ASRManager(foreground=True)
        assert mgr._foreground is True

    def test_recorder_is_none_initially(self):
        mgr = ASRManager()
        assert mgr.recorder is None


class TestNextSessionId:
    def test_format(self):
        sid = _next_session_id()
        # asr-YYYYMMDD-HHMMSS-N
        parts = sid.split("-")
        assert len(parts) == 4
        assert parts[0] == "asr"
        assert len(parts[1]) == 8  # YYYYMMDD
        assert parts[1].isdigit()
        assert len(parts[2]) == 6  # HHMMSS
        assert parts[2].isdigit()
        assert parts[3].isdigit()  # N

    def test_monotonic_counter(self):
        ids = [_next_session_id() for _ in range(5)]
        counters = [int(sid.split("-")[3]) for sid in ids]
        # 验证严格单调递增（跨测试的初始值不确定）
        for i in range(1, len(counters)):
            assert counters[i] == counters[i - 1] + 1


class TestSnapshotConfig:
    """_snapshot_config 降级行为测试。"""

    def test_fallback_when_no_config(self):
        """load_config 失败时使用缺省值。"""
        with patch("aion.audio.manager.load_config", side_effect=FileNotFoundError("no config")):
            workspace_dir, provider_name, provider_config = ASRManager._snapshot_config()
        assert provider_name == "aliyun"
        assert provider_config == {}
        assert workspace_dir.endswith("recordings")

    def test_fallback_when_config_no_asr(self):
        """Config 存在但没有 asr 字段时使用缺省值。"""
        fake_config = MagicMock()
        fake_config.asr = None
        with patch("aion.audio.manager.load_config", return_value=fake_config):
            workspace_dir, provider_name, provider_config = ASRManager._snapshot_config()
        assert provider_name == "aliyun"
        assert provider_config == {}
        assert workspace_dir.endswith("recordings")


class TestRunForeground:
    """前台模式 run() 的格式化输出测试（使用 mock provider）。"""

    @pytest.mark.asyncio
    async def test_yields_formatted_text(self):
        """前台模式下 yield [mm:ss] 格式文本。"""
        from aion.audio.provider import TranscriptChunk

        chunks = [
            TranscriptChunk(begin_time=0, end_time=3200, text="你好", is_final=True, confidence=0.95),
            TranscriptChunk(begin_time=3200, end_time=6800, text="世界", is_final=True, confidence=0.92),
        ]

        mock_source = AsyncMock()
        mock_source.read_chunks.return_value = aiter_bytes()

        mock_provider = MagicMock()
        mock_provider.name = "test"
        mock_provider.transcribe_stream.return_value = async_iter(chunks)

        mgr = ASRManager(foreground=True)
        mgr._source = mock_source
        mgr._provider = mock_provider
        mgr._recorder = MagicMock()
        mgr._running = True

        results = []
        async for line in mgr.run():
            results.append(line)

        assert results == ["[00:00] 你好", "[00:03] 世界"]

    @pytest.mark.asyncio
    async def test_foreground_skips_non_final(self):
        """前台模式跳过中间结果。"""
        from aion.audio.provider import TranscriptChunk

        chunks = [
            TranscriptChunk(begin_time=0, end_time=0, text="你好", is_final=False, confidence=0.0),
            TranscriptChunk(begin_time=0, end_time=3200, text="你好世界", is_final=True, confidence=0.95),
        ]

        mock_source = AsyncMock()
        mock_source.read_chunks.return_value = aiter_bytes()

        mock_provider = MagicMock()
        mock_provider.name = "test"
        mock_provider.transcribe_stream.return_value = async_iter(chunks)

        mgr = ASRManager(foreground=True)
        mgr._source = mock_source
        mgr._provider = mock_provider
        mgr._recorder = MagicMock()
        mgr._running = True

        results = []
        async for line in mgr.run():
            results.append(line)

        assert results == ["[00:00] 你好世界"]

    @pytest.mark.asyncio
    async def test_background_no_yield(self):
        """后台模式不 yield 任何内容。"""
        from aion.audio.provider import TranscriptChunk

        chunks = [
            TranscriptChunk(begin_time=0, end_time=3200, text="你好", is_final=True, confidence=0.95),
        ]

        mock_source = AsyncMock()
        mock_source.read_chunks.return_value = aiter_bytes()

        mock_provider = MagicMock()
        mock_provider.name = "test"
        mock_provider.transcribe_stream.return_value = async_iter(chunks)

        mgr = ASRManager(foreground=False)
        mgr._source = mock_source
        mgr._provider = mock_provider
        mgr._recorder = MagicMock()
        mgr._running = True

        results = []
        async for line in mgr.run():
            results.append(line)

        assert results == []


# ── 辅助工具 ──────────────────────────────────────────────────


async def aiter_bytes():
    """产生空字节块的异步迭代器。"""
    yield b""
    return


async def async_iter(items):
    """将可迭代对象转为异步迭代器。"""
    for item in items:
        yield item
