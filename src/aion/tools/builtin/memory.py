"""memory 工具模块 — 工具函数通过 ContextVar 读取 workspace + agent_id。"""

from pathlib import Path

from langchain_core.tools import tool

from aion.core.context import current_workspace, current_agent_id
from aion.log import get_logger

logger = get_logger(__name__)


@tool(parse_docstring=True)
def memory_write(content: str) -> str:
    """写入永久记忆 MEMORY.md，用于保存长期保留的个人信息、偏好、决定等。
    用于需要 Agent 长期记住的信息。如需写入今日日记，使用 daily_memory_write。
    如需搜索已有记忆，使用 memory_search。

    Args:
        content: 要写入的记忆内容
    """
    from ...memory.long import LongTermStore

    ws = current_workspace.get()
    agent_id = current_agent_id.get()

    store = LongTermStore(
        workspace_dir=ws,
        agent_id=agent_id,
        on_write=lambda fp, c: _sync_index(ws, agent_id, str(fp.relative_to(ws)), c, "memory"),
    )
    try:
        store.overwrite(content)
        return f"已更新永久记忆 ({len(content)} 字符)"
    except Exception as e:
        logger.error("[memory_write] 写入失败: %s", e)
        return f"写入失败: {e}"


@tool(parse_docstring=True)
def daily_memory_write(content: str) -> str:
    """写入今日记忆摘要 YYYY-MM-DD.md，记录今日发生的重要事件和决定。
    当需要保存当天的工作记录、决策或事件时使用。
    如需保存长期永久记忆，使用 memory_write。

    Args:
        content: 要写入的摘要内容
    """
    from ...memory.mid import DailyFileStore

    ws = current_workspace.get()
    agent_id = current_agent_id.get()

    store = DailyFileStore(
        workspace_dir=ws,
        agent_id=agent_id,
        on_write=lambda fp, c: _sync_index(ws, agent_id, str(fp.relative_to(ws)), c, "daily"),
    )
    try:
        store.append(content)
        return f"已记录今日记忆 ({len(content)} 字符)"
    except Exception as e:
        logger.error("[daily_memory_write] 写入失败: %s", e)
        return f"写入失败: {e}"


@tool(parse_docstring=True)
def memory_search(query: str) -> str:
    """搜索永久记忆 MEMORY.md、每日记忆 memory/*.md 和会话历史记录。
    在回答涉及过往工作、技术决策、用户偏好、人员信息、日期、待办事项的问题之前，
    应执行此搜索获取上下文。
    搜索结果包含完整内容文本，可直接使用，无需额外调用 memory_get。

    Args:
        query: 搜索查询关键词
    """
    from ...rag.search import MemorySearchTool
    from ...config.loader import load_config as _load_config

    ws = current_workspace.get()
    agent_id = current_agent_id.get()
    config = _load_config()
    embedding_config = config.memory.model_dump().get("embedding") if config.memory else None

    tool = MemorySearchTool(
        workspace_dir=ws,
        agent_id=agent_id,
        top_k=6,
        embedding_config=embedding_config,
    )
    try:
        results = tool.search(query)
        if not results:
            return "[未找到相关记忆]"
        lines = ["[记忆搜索结果（包含完整内容，可直接使用）]"]
        for r in results:
            lines.append(f"\n--- {r['path']} (评分: {r['score']:.2f}) ---")
            lines.append(r.get("content", ""))
        return "\n".join(lines)
    except Exception as e:
        logger.error("[memory_search] 搜索失败: %s", e)
        return f"搜索失败: {e}"


@tool(parse_docstring=True)
def memory_get(path: str) -> str:
    """精确读取记忆文件指定行范围的内容。
    memory_search 已返回完整内容时无需调用此工具。
    仅当需要读取超大记忆文件的特定行范围时使用。

    Args:
        path: 记忆文件路径，格式为 'path' 或 'path|from_line'
    """
    from ...rag.search import MemorySearchTool
    from ...config.loader import load_config as _load_config

    ws = current_workspace.get()
    agent_id = current_agent_id.get()
    config = _load_config()
    embedding_config = config.memory.model_dump().get("embedding") if config.memory else None

    tool = MemorySearchTool(
        workspace_dir=ws,
        agent_id=agent_id,
        top_k=6,
        embedding_config=embedding_config,
    )
    try:
        parts = path.split("|")
        file_path = parts[0].strip()
        from_line = int(parts[1].strip()) if len(parts) > 1 else 1
        return tool.get(file_path, from_line=from_line)
    except Exception as e:
        logger.error("[memory_get] 读取失败: %s", e)
        return f"读取失败: {e}"


# ── 共享辅助函数 ──


def _sync_index(ws: Path, agent_id: str, rel_path: str, content: str, source: str) -> None:
    """磁盘写入后同步索引到 FTS5 + Chroma。"""
    from ...memory.indexer import VectorIndexer
    from ...config.loader import load_config as _load_config
    from datetime import datetime

    config = _load_config()
    embedding_config = config.memory.model_dump().get("embedding") if config.memory else None
    indexer = VectorIndexer(
        workspace_dir=ws,
        agent_id=agent_id,
        embedding_config=embedding_config,
    )
    date_str = datetime.now().strftime("%Y-%m-%d")
    chunks = indexer._chunk(content)
    ids = [f"{rel_path}_{i}" for i in range(len(chunks))]
    indexer._delete_by_path(rel_path)
    indexer._write_chunks(chunks, ids, rel_path, source, date_str)
