"""Delete 工具 — 永久删除文件

直接删除文件，不移动到垃圾桶。
- `delete` > `trash`（明确不可恢复）
- 用于明确要永久删除的场景

bootstrap 校验由 loader 闭包注入，这里只做纯函数实现。
"""

from pathlib import Path

from langchain_core.tools import tool

from aion.core.context import current_workspace
from aion.log import get_logger

logger = get_logger(__name__)


@tool(parse_docstring=True)
def delete(path: str) -> str:
    """永久删除文件（不可恢复，不经过垃圾桶）。
    当需要彻底删除文件且不需要恢复时使用。delete 比 trash 更彻底。
    如需安全删除（可恢复），使用 trash。

    Args:
        path: 要删除的文件路径（绝对路径）
    """
    try:
        p = Path(path).expanduser()
        if not p.is_absolute():
            ws = current_workspace.get()
            resolved_path = (ws / p).resolve()
        else:
            resolved_path = p.resolve()

        if not resolved_path.exists():
            return f"文件不存在: {path}"

        # 直接 unlink，不经过 ~/.Trash
        resolved_path.unlink()
        return f"已删除: {resolved_path.name}"

    except PermissionError:
        logger.warning("[delete] 权限不足: %s", path)
        return f"权限不足，无法删除: {path}"
    except FileNotFoundError:
        logger.warning("[delete] 文件不存在: %s", path)
        return f"文件不存在: {path}"
    except Exception as e:
        logger.error("[delete] 删除失败: %s", e)
        return f"删除失败: {e}"
