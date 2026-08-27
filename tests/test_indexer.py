"""Tests for VectorIndexer FIFO consumer."""

import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aion.memory.indexer import _is_tokenize_400_error


class TestIsTokenize400Error:
    """_is_tokenize_400_error 识别 Chroma tokenize 400 错误测试"""

    def test_matches_tokenize_400(self):
        """包含 tokenize 和 400 的异常消息应返回 True"""
        exc = Exception('Post "http://127.0.0.1:65426/tokenize": EOF (status code: 400)')
        assert _is_tokenize_400_error(exc) is True

    def test_ignores_connection_refused(self):
        """connection refused 不应匹配"""
        exc = Exception("Connection refused: POST /tokenize")
        assert _is_tokenize_400_error(exc) is False

    def test_ignores_generic_400(self):
        """400 但不含 tokenize 不应匹配"""
        exc = Exception("HTTP 400 Bad Request")
        assert _is_tokenize_400_error(exc) is False

    def test_ignores_random_exception(self):
        """随机异常不应匹配"""
        exc = Exception("something else went wrong")
        assert _is_tokenize_400_error(exc) is False

    def test_matches_tokenize_400_variation(self):
        """不同端口的 tokenize 400 也应匹配"""
        exc = Exception('Post "http://127.0.0.1:9999/tokenize": error (status code: 400)')
        assert _is_tokenize_400_error(exc) is True


class TestVectorIndexerChunking:
    def test_short_content_stays_whole(self, tmp_path):
        from aion.memory.indexer import VectorIndexer

        indexer = VectorIndexer(tmp_path, agent_id="main")
        chunks = indexer._chunk("这是一条短记忆")
        assert len(chunks) == 1
        assert chunks[0] == "这是一条短记忆"

    def test_long_content_is_split(self, tmp_path):
        from aion.memory.indexer import VectorIndexer

        indexer = VectorIndexer(tmp_path, agent_id="main", chunk_size=50, chunk_overlap=10)
        chunks = indexer._chunk("A" * 200)
        assert len(chunks) >= 4

    def test_chunk_size_and_overlap_defaults(self, tmp_path):
        from aion.memory.indexer import VectorIndexer

        indexer = VectorIndexer(tmp_path, agent_id="main")
        assert indexer.chunk_size == 400
        assert indexer.chunk_overlap == 80


@pytest.mark.asyncio
async def test_handle_append_session(tmp_path):
    from aion.memory.indexer import VectorIndexer
    from aion.memory.indexer import WriteTask

    indexer = VectorIndexer(tmp_path, agent_id="main")
    task = WriteTask(
        type="append_session",
        text="用户喜欢TypeScript",
        metadata={"path": "sessions/test.jsonl", "source": "sessions", "date": "2026-06-16"},
    )
    await indexer.handle_task(task)
    # 检查 FTS5 中是否有数据（Chroma 降级不阻断）
    fts_results = indexer._fts.search("TypeScript", top_k=10)
    assert len(fts_results) >= 1
    indexer.close()
