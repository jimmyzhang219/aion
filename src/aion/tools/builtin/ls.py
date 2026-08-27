"""ls 工具模块

列出指定目录下的文件与子目录名称，目录项以 / 后缀标识。
"""

from pathlib import Path

from langchain_core.tools import tool

from aion.core.context import current_workspace
from aion.log import get_logger

logger = get_logger(__name__)


@tool(parse_docstring=True)
def ls(path: str = ".") -> str:
    """列出目录内容，每行一个条目（目录带 / 后缀）。
    当需要浏览工作空间内的目录结构时使用。
    如需查找文件，使用 find 或 grep。

    Args:
        path: 目录路径，默认为当前目录 "."
    """
    try:
        p = Path(path).expanduser()
        if not p.is_absolute():
            ws = current_workspace.get()
            p = (ws / p).resolve()
        else:
            p = p.resolve()
        if not p.exists():
            return f"路径不存在: {path}"
        if not p.is_dir():
            return f"不是目录: {path}"
        items = list(p.iterdir())
        if not items:
            return f"{path} 是空目录"
        # 目录项追加 / 后缀，便于 LLM 区分文件与文件夹
        return "\n".join(f"{i.name}{'/' if i.is_dir() else ''}" for i in items)
    except Exception as e:
        logger.error("[ls] ls 失败: %s", e)
        return f"ls 失败: {e}"
