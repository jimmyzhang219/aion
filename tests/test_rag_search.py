"""Tests for MemorySearchTool (双通道检索)."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class TestMemorySearchTool:
    def test_search_no_chroma_no_fts_no_crash(self, tmp_path):
        from aion.rag.search import MemorySearchTool

        tool = MemorySearchTool(tmp_path, agent_id="main")
        results = tool.search("test query")
        assert results == []

    def test_search_fts_with_content(self, tmp_path):
        from aion.rag.search import MemorySearchTool
        from aion.memory.fts5 import FTSIndexer

        agent_dir = tmp_path / "agents" / "main"
        fts_dir = agent_dir / "fts5"
        fts_dir.mkdir(parents=True)
        fts = FTSIndexer(fts_dir / "memory_search.db")
        fts.add(
            "test_1",
            "用户喜欢TypeScript",
            path="memory/test.md",
            source="daily",
            date="2026-06-16",
        )
        fts.close()

        tool = MemorySearchTool(tmp_path, agent_id="main")
        results = tool.search("TypeScript")
        assert len(results) > 0

    def test_time_decay_session(self, tmp_path):
        from aion.rag.search import MemorySearchTool

        tool = MemorySearchTool(tmp_path, agent_id="main")
        score = tool._time_decay(1.0, "2026-06-06", source="sessions")
        assert score < 0.8

    def test_time_decay_memory(self, tmp_path):
        from aion.rag.search import MemorySearchTool

        tool = MemorySearchTool(tmp_path, agent_id="main")
        score = tool._time_decay(1.0, "2025-01-01", source="memory")
        assert score == 1.0

    def test_fusion_ranking(self, tmp_path):
        from aion.rag.search import MemorySearchTool

        tool = MemorySearchTool(tmp_path, agent_id="main")
        dense_results = [
            {
                "chunk_id": "a.md_0",
                "path": "a.md",
                "vectorScore": 0.9,
                "content": "a",
                "source": "daily",
                "date": "2026-06-16",
            },
            {
                "chunk_id": "b.md_0",
                "path": "b.md",
                "vectorScore": 0.5,
                "content": "b",
                "source": "daily",
                "date": "2026-06-16",
            },
        ]
        fts_results = [
            {
                "chunk_id": "b.md_0",
                "path": "b.md",
                "textScore": 0.8,
                "content": "b",
                "source": "daily",
                "date": "2026-06-16",
            },
            {
                "chunk_id": "c.md_0",
                "path": "c.md",
                "textScore": 0.9,
                "content": "c",
                "source": "daily",
                "date": "2026-06-16",
            },
        ]
        fused = tool._fuse_results(dense_results, fts_results)
        paths = [r["path"] for r in fused]
        assert len(paths) == len(set(paths))
        assert "c.md" in paths
