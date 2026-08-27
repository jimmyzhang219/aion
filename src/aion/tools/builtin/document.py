"""document 处理工具 — 从 ContextVar 读取 workspace + agent_id 后索引文档。"""

from pathlib import Path

from langchain_core.tools import tool

from aion.core.context import current_workspace, current_agent_id


@tool(parse_docstring=True)
def process_document(file_path: str) -> str:
    """处理文档文件，分块并索引到向量库以便后续检索。支持 txt、md 等文本格式。
    用于将外部文档内容纳入记忆搜索范围。
    处理后可通过 memory_search 检索文档内容。

    Args:
        file_path: 文档文件路径
    """
    from ...memory.indexer import VectorIndexer
    from ...config.loader import load_config

    ws = current_workspace.get()
    agent_id = current_agent_id.get()
    config = load_config()
    embedding_config = config.memory.model_dump().get("embedding") if config.memory else None

    # 解析相对路径为绝对路径（index_document 内部只做了 expanduser 不做 workspace resolve）
    resolved = (
        str((ws / file_path).resolve())
        if not Path(file_path).is_absolute()
        else str(Path(file_path).expanduser().resolve())
    )
    indexer = VectorIndexer(
        workspace_dir=ws,
        agent_id=agent_id,
        embedding_config=embedding_config,
    )
    return indexer.index_document(resolved)
