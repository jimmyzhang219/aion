"""FTS5 全文索引引擎

封装 SQLite FTS5 的增删改查操作，提供 BM25 中文全文检索能力。
使用 trigram tokenizer 天然支持中文，无需额外依赖。
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


class FTSIndexer:
    """SQLite FTS5 全文索引封装，提供 BM25 中文全文检索能力。

    Parameters
    ----------
    db_path : str | Path
        SQLite 数据库文件路径。自动创建数据库和表结构。
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        """初始化数据库表结构。"""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS memory_content (
                id      TEXT PRIMARY KEY,
                path    TEXT NOT NULL,
                source  TEXT NOT NULL,
                date    TEXT NOT NULL,
                seq     INTEGER NOT NULL DEFAULT 0,
                text    TEXT NOT NULL
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                text,
                content=memory_content,
                tokenize='trigram',
                content_rowid='rowid'
            );

            CREATE INDEX IF NOT EXISTS idx_mc_date   ON memory_content(date);
            CREATE INDEX IF NOT EXISTS idx_mc_source ON memory_content(source);
            CREATE INDEX IF NOT EXISTS idx_mc_path   ON memory_content(path);
        """)
        self.conn.commit()

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def add(
        self,
        id: str,
        text: str,
        path: str,
        source: str,
        date: str,
        seq: int = 0,
    ) -> None:
        """添加/覆盖一条文档到 FTS5 索引。

        使用 UPSERT 语义：若 id 已存在则全量覆盖；否则新建。
        FTS5 索引随内容表的变更同步更新。
        """
        # 若 id 已存在，先清除旧的 FTS 索引条目
        cur = self.conn.execute("SELECT rowid FROM memory_content WHERE id = ?", (id,))
        existing = cur.fetchone()
        if existing is not None:
            self.conn.execute("DELETE FROM memory_fts WHERE rowid = ?", (existing[0],))

        # UPSERT 到内容表（保留 rowid 不变）
        cur = self.conn.execute(
            """
            INSERT INTO memory_content (id, path, source, date, seq, text)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                path   = excluded.path,
                source = excluded.source,
                date   = excluded.date,
                seq    = excluded.seq,
                text   = excluded.text
            """,
            (id, path, source, date, seq, text),
        )
        new_rowid: int = cur.lastrowid  # type: ignore[assignment]

        # 重建 FTS 索引条目
        self.conn.execute(
            "INSERT INTO memory_fts(rowid, text) VALUES (?, ?)",
            (new_rowid, text),
        )
        self.conn.commit()

    def delete_by_path(self, path: str) -> int:
        """按 path 删除全部索引。返回 FTS + content 两表实际删除的行数之和。"""
        cur_fts = self.conn.execute(
            """
            DELETE FROM memory_fts WHERE rowid IN (
                SELECT rowid FROM memory_content WHERE path = ?
            )
            """,
            (path,),
        )
        cur_content = self.conn.execute("DELETE FROM memory_content WHERE path = ?", (path,))
        deleted = (cur_fts.rowcount or 0) + (cur_content.rowcount or 0)
        self.conn.commit()
        return deleted

    def delete_by_date(self, date: str, source: str) -> int:
        """按日期 + 来源删除全部索引。返回 FTS + content 两表实际删除的行数之和。"""
        cur_fts = self.conn.execute(
            """
            DELETE FROM memory_fts WHERE rowid IN (
                SELECT rowid FROM memory_content WHERE date = ? AND source = ?
            )
            """,
            (date, source),
        )
        cur_content = self.conn.execute(
            "DELETE FROM memory_content WHERE date = ? AND source = ?",
            (date, source),
        )
        deleted = (cur_fts.rowcount or 0) + (cur_content.rowcount or 0)
        self.conn.commit()
        return deleted

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query: str, top_k: int = 200) -> list[dict]:
        """BM25 全文搜索。

        按 BM25 分数降序返回，分数归一化到 [0, 1]。
        当 FTS5 查询因语法 / 特殊字符失败时，记录 debug 日志并返回空列表。

        Parameters
        ----------
        query : str
            搜索关键词。
        top_k : int
            最多返回条数，默认 200。

        Returns
        -------
        list[dict]
            每项含 id / path / source / date / seq / content / score。
        """
        try:
            cur = self.conn.execute(
                """
                SELECT
                    mc.id,
                    mc.path,
                    mc.source,
                    mc.date,
                    mc.seq,
                    mc.text,
                    -bm25(memory_fts) AS score
                FROM memory_fts
                JOIN memory_content mc ON mc.rowid = memory_fts.rowid
                WHERE memory_fts MATCH ?
                ORDER BY score DESC
                LIMIT ?
                """,
                (query, top_k),
            )
        except sqlite3.OperationalError as exc:
            logger.debug("FTS5 search query failed: %s (query=%r)", exc, query)
            return []

        rows = cur.fetchall()
        results = [
            {
                "chunk_id": row[0],
                "path": row[1],
                "source": row[2],
                "date": row[3],
                "seq": row[4],
                "content": row[5],
                "textScore": row[6],
            }
            for row in rows
        ]

        # 归一化到 [0, 1]
        if results:
            max_score = max(r["textScore"] for r in results)
            if max_score > 0:
                for r in results:
                    r["textScore"] = round(r["textScore"] / max_score, 4)

        return results

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """关闭数据库连接。"""
        self.conn.close()

    def __enter__(self) -> FTSIndexer:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
