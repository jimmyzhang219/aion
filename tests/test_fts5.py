"""tests/test_fts5.py"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aion.memory.fts5 import FTSIndexer


class TestFTSIndexer:
    def test_create_db(self, tmp_path):
        indexer = FTSIndexer(tmp_path / "test.db")
        assert indexer.db_path.exists()
        import sqlite3

        conn = sqlite3.connect(str(indexer.db_path))
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        names = {r[0] for r in tables}
        assert "memory_content" in names
        assert "memory_fts" in names
        conn.close()

    def test_add_and_search(self, tmp_path):
        indexer = FTSIndexer(tmp_path / "test.db")
        indexer.add("id1", "用户喜欢TypeScript", path="memory/test.md", source="daily", date="2026-06-16")
        indexer.add("id2", "讨论了React状态管理", path="memory/test.md", source="daily", date="2026-06-16")
        results = indexer.search("TypeScript", top_k=10)
        assert len(results) >= 1
        assert results[0]["chunk_id"] == "id1"

    def test_search_chinese(self, tmp_path):
        indexer = FTSIndexer(tmp_path / "test.db")
        indexer.add("id1", "用户喜欢TypeScript", path="sessions/test.jsonl", source="sessions", date="2026-06-16")
        results = indexer.search("TypeScript", top_k=10)
        assert len(results) >= 1

    def test_delete_by_path(self, tmp_path):
        indexer = FTSIndexer(tmp_path / "test.db")
        indexer.add("id1", "测试内容", path="memory/test.md", source="daily", date="2026-06-16")
        indexer.delete_by_path("memory/test.md")
        results = indexer.search("测试内容", top_k=10)
        assert len(results) == 0

    def test_delete_by_date(self, tmp_path):
        indexer = FTSIndexer(tmp_path / "test.db")
        indexer.add("id1", "测试内容", path="test.md", source="daily", date="2026-06-16")
        indexer.delete_by_date("2026-06-16", "daily")
        results = indexer.search("测试内容", top_k=10)
        assert len(results) == 0

    def test_bm25_ranking(self, tmp_path):
        indexer = FTSIndexer(tmp_path / "test.db")
        indexer.add("id1", "今天讨论了Python装饰器", path="sessions/a.jsonl", source="sessions", date="2026-06-16")
        indexer.add(
            "id2",
            "用户喜欢Python、Python框架和Python工具链",
            path="sessions/b.jsonl",
            source="sessions",
            date="2026-06-16",
        )
        results = indexer.search("Python", top_k=10)
        assert results[0]["chunk_id"] == "id2"  # id2 有更多 Python 匹配

    def test_add_replaces_existing_id(self, tmp_path):
        indexer = FTSIndexer(tmp_path / "test.db")
        indexer.add("id1", "旧内容", path="test.md", source="daily", date="2026-06-16")
        indexer.add("id1", "新内容", path="test.md", source="daily", date="2026-06-16")
        results = indexer.search("新内容", top_k=10)
        assert len(results) == 1
        old_results = indexer.search("旧内容", top_k=10)
        assert len(old_results) == 0
