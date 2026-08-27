"""检索召回工具：memory_search 和 memory_get

基于三层记忆存储，提供多路搜索能力：
- Chroma Dense Vector（语义通道）
- SQLite FTS5 BM25（关键词通道）
- 应用层融合加权 + 时间衰减 + 排序
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional, TYPE_CHECKING

from aion.memory.embeddings import create_embeddings
from aion.memory.fts5 import FTSIndexer

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from langchain_chroma import Chroma as LCChroma
else:
    try:
        from langchain_chroma import Chroma as LCChroma
    except ImportError:
        try:
            from langchain_community.vectorstores import Chroma as LCChroma
        except ImportError:
            LCChroma = None

DEFAULT_WEIGHT_DENSE = 0.6
DEFAULT_WEIGHT_BM25 = 0.4
DEFAULT_DECAY_LAMBDA_SESSION = 0.3
DEFAULT_DECAY_LAMBDA_DAILY = 0.15
DEFAULT_DECAY_LAMBDA_MEMORY = 0.0
DEFAULT_TOP_K = 10
DEFAULT_N_RESULTS_PER_CHANNEL = 200
DEFAULT_MIN_SCORE = 0.2


def _normalize_field(results: list[dict], key: str) -> list[dict]:
    """将 results 中指定字段归一化到 [0, 1]。"""
    if not results:
        return results
    scores = [r.get(key, 0) for r in results]
    max_score = max(scores)
    if max_score <= 0:
        return results
    for r in results:
        r[key] = r.get(key, 0) / max_score
    return results


class MemorySearchTool:
    """记忆搜索工具

    双通道检索：
    - Chroma Dense: 语义搜索全部来源
    - FTS5 BM25: 关键词搜索全部来源（trigram 分词）
    - 应用层融合：分数归一化 → 去重 → 时间衰减 → 加权融合 → 排序
    """

    def __init__(
        self,
        workspace_dir: Path | str,
        agent_id: str | None = None,
        top_k: int = DEFAULT_TOP_K,
        min_score: float = DEFAULT_MIN_SCORE,
        weight_dense: float = DEFAULT_WEIGHT_DENSE,
        weight_bm25: float = DEFAULT_WEIGHT_BM25,
        decay_lambda_session: float = DEFAULT_DECAY_LAMBDA_SESSION,
        decay_lambda_daily: float = DEFAULT_DECAY_LAMBDA_DAILY,
        decay_lambda_memory: float = DEFAULT_DECAY_LAMBDA_MEMORY,
        n_results_per_channel: int = DEFAULT_N_RESULTS_PER_CHANNEL,
        embedding_config: dict | None = None,
        # backward-compat aliases (mapped below)
        max_results: int | None = None,
        daily_memory_days: int | None = None,
    ):
        self.workspace_dir = Path(workspace_dir)
        self.agent_id = agent_id or "main"

        # backward compat: max_results overrides top_k
        if max_results is not None:
            top_k = max_results
        self.top_k = top_k
        self.max_results = top_k  # backward compat
        self.min_score = min_score
        self.weight_dense = weight_dense
        self.weight_bm25 = weight_bm25
        self.decay_lambda = {
            "sessions": decay_lambda_session,
            "daily": decay_lambda_daily,
            "memory": decay_lambda_memory,
        }
        self.n_results_per_channel = n_results_per_channel

        # daily_memory_days is silently ignored — no longer used for scanning

        chroma_dir = self.workspace_dir / "agents" / self.agent_id / "chroma"
        fts5_dir = self.workspace_dir / "agents" / self.agent_id / "fts5"
        self.chroma_dir = chroma_dir
        ws_name = self.workspace_dir.name
        self.collection_name = f"{ws_name}_{self.agent_id}_memories"

        self._fts = FTSIndexer(fts5_dir / "memory_search.db")
        self._embedding_config = embedding_config
        self._embeddings_override: Any | None = None
        self._chroma_dir_ready = False

    def _ensure_chroma_dir(self) -> None:
        if not self._chroma_dir_ready:
            self.chroma_dir.mkdir(parents=True, exist_ok=True)
            self._chroma_dir_ready = True

    def _resolve_embeddings(self) -> Optional[Any]:
        if self._embeddings_override is not None:
            return self._embeddings_override
        emb = create_embeddings(self._embedding_config)
        if emb is not None:
            return emb
        if os.environ.get("OPENAI_API_KEY"):
            try:
                from langchain_openai import OpenAIEmbeddings
            except ImportError:
                return None
            model = os.environ.get("AION_EMBEDDING_MODEL", "text-embedding-3-small")
            return OpenAIEmbeddings(model=model)
        return None

    def _open_vectorstore(self) -> Any | None:
        if LCChroma is None:
            return None
        emb = self._resolve_embeddings()
        if emb is None:
            return None
        self._ensure_chroma_dir()
        try:
            return LCChroma(
                collection_name=self.collection_name,
                embedding_function=emb,
                persist_directory=str(self.chroma_dir),
            )
        except Exception as e:
            logger.debug("[MemorySearch] 打开 Chroma 集合失败: %s", e)
            return None

    def _distance_to_score(self, raw: float) -> float:
        x = float(raw)
        if 0.0 <= x <= 1.0:
            return x
        return 1.0 / (1.0 + max(x, 0.0))

    def _semantic_search(self, query: str) -> list[dict]:
        vs = self._open_vectorstore()
        if vs is None:
            return []
        matches: list[dict] = []
        k = self.n_results_per_channel
        try:
            if hasattr(vs, "similarity_search_with_relevance_scores"):
                pairs = vs.similarity_search_with_relevance_scores(query, k=k)
                for doc, rel in pairs:
                    meta = doc.metadata or {}
                    path = meta.get("path", "") or ""
                    if not path:
                        continue
                    score = float(rel)
                    matches.append(
                        {
                            "chunk_id": meta.get("id", f"{path}_{meta.get('seq', 0)}"),
                            "path": path,
                            "vectorScore": score,
                            "content": (doc.page_content or ""),
                            "source": meta.get("source", "daily"),
                            "date": meta.get("date", ""),
                        }
                    )
            else:
                pairs = vs.similarity_search_with_score(query, k=k)
                for doc, dist in pairs:
                    meta = doc.metadata or {}
                    path = meta.get("path", "") or ""
                    if not path:
                        continue
                    score = self._distance_to_score(dist)
                    matches.append(
                        {
                            "chunk_id": meta.get("id", f"{path}_{meta.get('seq', 0)}"),
                            "path": path,
                            "vectorScore": score,
                            "content": (doc.page_content or ""),
                            "source": meta.get("source", "daily"),
                            "date": meta.get("date", ""),
                        }
                    )
        except Exception as e:
            logger.debug("[MemorySearch] Chroma 查询失败: %s", e)
        return matches

    def _keyword_search(self, query: str) -> list[dict]:
        try:
            return self._fts.search(query, top_k=self.n_results_per_channel)
        except Exception as e:
            logger.debug("[MemorySearch] FTS5 查询失败: %s", e)
            return []

    def _time_decay(self, score: float, date_str: str, source: str) -> float:
        if not date_str:
            return score
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            try:
                dt = datetime.fromisoformat(date_str)
            except (ValueError, TypeError):
                return score
        days_diff = (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
        lambda_ = self.decay_lambda.get(source, 0.1)
        if lambda_ <= 0:
            return score
        decay = max(0.5, 1.0 / (1.0 + lambda_ * days_diff))
        return score * decay

    def _fuse_results(self, dense_results: list[dict], fts_results: list[dict]) -> list[dict]:
        """Chunk-level 融合：按 chunk_id 合并加权，不做 path 级别去重。"""
        # 各通道分数归一化
        dense_results = _normalize_field(dense_results, "vectorScore")
        fts_results = _normalize_field(fts_results, "textScore")

        # 时间衰减
        for r in dense_results:
            r["vectorScore"] = self._time_decay(r["vectorScore"], r.get("date", ""), r.get("source", "daily"))
        for r in fts_results:
            r["textScore"] = self._time_decay(r["textScore"], r.get("date", ""), r.get("source", "daily"))

        # Step 1: Union by chunk_id
        merged: dict[str, dict] = {}
        for r in dense_results:
            cid = r.get("chunk_id", r["path"])
            merged[cid] = {
                "chunk_id": cid,
                "path": r["path"],
                "vectorScore": r["vectorScore"],
                "textScore": 0.0,
                "content": r.get("content", ""),
                "source": r.get("source", "daily"),
                "date": r.get("date", ""),
            }

        for r in fts_results:
            cid = r.get("chunk_id", r["path"])
            if cid in merged:
                merged[cid]["textScore"] = r["textScore"]
                if r.get("content"):
                    merged[cid]["content"] = r["content"]
            else:
                merged[cid] = {
                    "chunk_id": cid,
                    "path": r["path"],
                    "vectorScore": 0.0,
                    "textScore": r["textScore"],
                    "content": r.get("content", ""),
                    "source": r.get("source", "daily"),
                    "date": r.get("date", ""),
                }

        # Step 2: Weighted score fusion
        for v in merged.values():
            v["score"] = self.weight_dense * v["vectorScore"] + self.weight_bm25 * v["textScore"]

        # Step 3: Sort + filter + top-k
        sorted_results = sorted(merged.values(), key=lambda x: x["score"], reverse=True)
        return [r for r in sorted_results if r["score"] >= self.min_score][: self.top_k]

    def search(
        self,
        query: str,
        sources: Optional[list[str]] = None,
        max_results: Optional[int] = None,
    ) -> list[dict]:
        limit = max_results if max_results is not None else self.top_k
        # temporarily override top_k for this call
        saved_top_k = self.top_k
        self.top_k = limit
        try:
            dense_results = self._semantic_search(query)
            fts_results = self._keyword_search(query)
            fused = self._fuse_results(dense_results, fts_results)
            if sources:
                fused = [r for r in fused if r.get("source") in sources]
            return fused
        finally:
            self.top_k = saved_top_k

    def get(self, path: str, from_line: int = 1, lines: int = 100) -> str:
        """按文件路径和行号读取记忆内容。"""
        if os.path.isabs(path):
            file_path = Path(path)
        else:
            file_path = self.workspace_dir / path
        if not file_path.exists():
            return f"[文件不存在: {path}]"
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            return f"[读取错误: {e}]"
        file_lines = content.split("\n")
        total_lines = len(file_lines)
        if from_line > total_lines:
            return ""
        end_line = min(from_line + lines - 1, total_lines)
        selected = file_lines[from_line - 1 : end_line]
        result_lines = [f"[{file_path.name} 第 {from_line}-{end_line} 行，共 {total_lines} 行]"]
        result_lines.extend(selected)
        result = "\n".join(result_lines)
        if end_line < total_lines:
            result += f"\n... [还有 {total_lines - end_line} 行未显示]"
        return result
