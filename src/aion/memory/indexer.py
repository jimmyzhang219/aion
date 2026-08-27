"""向量索引器：FIFO 消费者 — 将记忆内容分块 → 嵌入 → 写入 ChromaDB + FTS5。

由 ContextManager 驱动，单协程顺序处理写任务。
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any, TYPE_CHECKING

from chromadb.config import Settings as ChromaSettings

from aion.memory.embeddings import create_embeddings
from aion.memory.fts5 import FTSIndexer
from aion.memory._chroma_telemetry import NoopTelemetryClient
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def _is_tokenize_400_error(exc: Exception) -> bool:
    """判断 Chroma 异常是否为 /tokenize 400 EOF。

    Chroma 内部 Rust HTTP server 在处理不可索引内容（如 base64 编码的图片数据）
    时返回 HTTP 400，匹配此模式后可将日志降级为 debug。
    """
    msg = str(exc)
    return "tokenize" in msg and "status code: 400" in msg


@dataclass
class WriteTask:
    """向量索引写任务"""

    type: str
    text: str
    metadata: dict
    chunks: list[str] = field(default_factory=list)
    ids: list[str] = field(default_factory=list)


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


class VectorIndexer:
    """FIFO 消费者 — 记忆内容分块后写入 ChromaDB + FTS5。"""

    def __init__(
        self,
        workspace_dir: Path | str,
        agent_id: str | None = None,
        embedding_config: dict | None = None,
        chunk_size: int = 400,
        chunk_overlap: int = 80,
    ):
        self.workspace_dir = Path(workspace_dir)
        self.agent_id = agent_id or "main"
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

        chroma_dir = self.workspace_dir / "agents" / self.agent_id / "chroma"
        fts5_dir = self.workspace_dir / "agents" / self.agent_id / "fts5"
        fts5_dir.mkdir(parents=True, exist_ok=True)

        self.chroma_dir = chroma_dir
        ws_name = self.workspace_dir.name
        self.collection_name = f"{ws_name}_{self.agent_id}_memories"
        self._embedding_config = embedding_config
        self._embeddings: Any | None = None
        self._chroma_dir_ready = False
        self._fts = FTSIndexer(fts5_dir / "memory_search.db")

    def _ensure_chroma_dir(self) -> None:
        if not self._chroma_dir_ready:
            self.chroma_dir.mkdir(parents=True, exist_ok=True)
            self._chroma_dir_ready = True

    def _resolve_embeddings(self) -> Any | None:
        if self._embeddings is not None:
            return self._embeddings
        self._embeddings = create_embeddings(self._embedding_config)
        if self._embeddings is None and os.environ.get("OPENAI_API_KEY"):
            try:
                from langchain_openai import OpenAIEmbeddings
            except ImportError:
                return None
            model = os.environ.get("AION_EMBEDDING_MODEL", "text-embedding-3-small")
            self._embeddings = OpenAIEmbeddings(model=model)
        return self._embeddings

    def _chunk(self, content: str) -> list[str]:
        """段落感知文本分块。"""
        if not content:
            return []
        if len(content) <= self.chunk_size:
            return [content]

        paragraphs = content.split("\n")
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0

        for para in paragraphs:
            para_len = len(para) + 1

            if para_len > self.chunk_size:
                if current:
                    chunks.append("\n".join(current))
                    current = []
                    current_len = 0
                step = self.chunk_size - self.chunk_overlap
                for i in range(0, len(para), step):
                    chunks.append(para[i : i + self.chunk_size])
                continue

            if current_len + para_len > self.chunk_size and current:
                chunks.append("\n".join(current))
                overlap_paras: list[str] = []
                overlap_len = 0
                if self.chunk_overlap > 0:
                    for p in reversed(current):
                        pl = len(p) + 1
                        if overlap_len + pl > self.chunk_overlap:
                            break
                        overlap_paras.insert(0, p)
                        overlap_len += pl
                current = overlap_paras
                current_len = overlap_len

            current.append(para)
            current_len += para_len

        if current:
            chunks.append("\n".join(current))

        return chunks

    async def handle_task(self, task) -> None:
        """处理一个 WriteTask，尝试写 Chroma + FTS5。"""
        if not isinstance(task, WriteTask):
            return

        metadata = task.metadata
        path = metadata.get("path", "")
        source = metadata.get("source", "sessions")
        date = metadata.get("date", "")

        if task.type == "append_session":
            chunks = self._chunk(task.text)
            # 用内容哈希做唯一标识，避免多轮 session 的 chunk ID 碰撞
            turn_key = hashlib.md5(task.text.encode()).hexdigest()[:8]
            ids = [f"{path}_{turn_key}_{i}" for i in range(len(chunks))]
            self._write_chunks(chunks, ids, path, source, date)
        elif task.type == "overwrite_daily":
            self._delete_by_date(date, source)
            chunks = self._chunk(task.text)
            ids = [f"{path}_{i}" for i in range(len(chunks))]
            self._write_chunks(chunks, ids, path, source, date)
        elif task.type == "overwrite_memory":
            self._delete_by_path(path)
            chunks = self._chunk(task.text)
            ids = [f"{path}_{i}" for i in range(len(chunks))]
            self._write_chunks(chunks, ids, path, source, date)

    def _delete_by_date(self, date: str, source: str) -> None:
        try:
            if LCChroma is not None:
                import chromadb

                client = chromadb.PersistentClient(
                    path=str(self.chroma_dir),
                    settings=ChromaSettings(
                        anonymized_telemetry=False,
                        chroma_product_telemetry_impl=NoopTelemetryClient.fqn(),
                    ),
                )
                try:
                    collection = client.get_collection(name=self.collection_name)
                    collection.delete(where={"$and": [{"date": date}, {"source": source}]})
                except Exception:
                    pass
        except Exception as e:
            logger.debug("[VectorIndexer] Chroma delete_by_date 失败: %s", e)
        self._fts.delete_by_date(date, source)

    def _delete_by_path(self, path: str) -> None:
        try:
            if LCChroma is not None:
                import chromadb

                client = chromadb.PersistentClient(
                    path=str(self.chroma_dir),
                    settings=ChromaSettings(
                        anonymized_telemetry=False,
                        chroma_product_telemetry_impl=NoopTelemetryClient.fqn(),
                    ),
                )
                try:
                    collection = client.get_collection(name=self.collection_name)
                    existing = collection.get(where={"path": path})
                    count = len(existing.get("ids", []))
                    if count > 0:
                        collection.delete(ids=existing["ids"])
                except Exception:
                    pass
        except Exception as e:
            logger.debug("[VectorIndexer] Chroma delete_by_path 失败: %s", e)
        self._fts.delete_by_path(path)

    def _write_chunks(
        self,
        chunks: list[str],
        ids: list[str],
        path: str,
        source: str,
        date: str,
    ) -> None:
        fts_ok = True
        chroma_ok = True
        try:
            for i, (chunk_text, cid) in enumerate(zip(chunks, ids)):
                self._fts.add(
                    id=cid,
                    text=chunk_text,
                    path=path,
                    source=source,
                    date=date,
                    seq=i,
                )
        except Exception as e:
            fts_ok = False
            logger.warning("[VectorIndexer] FTS5 写入失败: %s", e)
        try:
            if LCChroma is not None:
                emb = self._resolve_embeddings()
                if emb is not None:
                    self._ensure_chroma_dir()
                    import chromadb

                    client = chromadb.PersistentClient(
                        path=str(self.chroma_dir),
                        settings=ChromaSettings(
                            anonymized_telemetry=False,
                            chroma_product_telemetry_impl=NoopTelemetryClient.fqn(),
                        ),
                    )
                    collection = client.get_or_create_collection(
                        name=self.collection_name,
                        metadata={"hnsw:space": "cosine"},
                    )
                    embeddings = emb.embed_documents(chunks)
                    metadatas = [
                        {"id": ids[i], "path": path, "source": source, "date": date, "seq": i}
                        for i in range(len(chunks))
                    ]
                    collection.add(
                        ids=ids,
                        documents=chunks,
                        embeddings=embeddings,
                        metadatas=metadatas,  # type: ignore[arg-type]
                    )
        except Exception as e:
            chroma_ok = False
            if _is_tokenize_400_error(e):
                logger.debug("[VectorIndexer] Chroma 跳过不可索引内容: %s", e)
            else:
                logger.warning("[VectorIndexer] Chroma 写入失败: %s", e)
        if not fts_ok and not chroma_ok:
            logger.error("[VectorIndexer] Chroma 和 FTS5 均写入失败")

    def index_document(self, file_path: str) -> str:
        """读取文档文件 → 分块 → 写入 Chroma+FTS5。用于 RAG 文档索引。"""
        path = Path(file_path).expanduser()
        if not path.exists():
            return f"处理失败: 文件不存在: {file_path}"
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            return f"处理失败: 读取错误: {e}"
        chunks = self._chunk(text)
        if not chunks:
            return "文档为空，无需索引"
        source = "rag_doc"
        date = ""
        ids = [f"{file_path}_{i}" for i in range(len(chunks))]
        self._write_chunks(chunks, ids, file_path, source, date)
        return f"文档已索引: {len(chunks)} 块写入 Chroma+FTS5"

    def close(self) -> None:
        self._fts.close()
