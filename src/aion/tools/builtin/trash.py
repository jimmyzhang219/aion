"""Trash 工具 — 文件移到垃圾桶

将文件移动到系统垃圾桶，而不是直接删除。
这是安全删除机制：
- `trash` > `rm`（可恢复的比永久消失好）

bootstrap 校验由 loader 闭包注入，这里只做纯函数实现。
"""

import shutil
import time
from pathlib import Path

from langchain_core.tools import tool

from aion.core.context import current_workspace
from aion.log import get_logger

logger = get_logger(__name__)


@tool(parse_docstring=True)
def trash(path: str) -> str:
    """将文件或目录移动到系统垃圾桶（安全删除，可恢复）。
    当需要安全删除文件但保留恢复可能性时使用。
    如需永久删除（不可恢复），使用 delete。

    Args:
        path: 要删除的文件或目录路径（绝对路径）
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

        # 构建垃圾桶目标路径（平台感知）
        from ...core.constants import get_trash_path

        trash_dir = get_trash_path()
        trash_dir.mkdir(parents=True, exist_ok=True)

        # 同名冲突时追加 Unix 时间戳，避免覆盖垃圾桶内已有文件
        target_name = resolved_path.name
        target_path = trash_dir / target_name
        if target_path.exists():
            timestamp = int(time.time())
            name_parts = resolved_path.name.rsplit(".", 1)
            if len(name_parts) == 2:
                new_name = f"{name_parts[0]}_{timestamp}.{name_parts[1]}"
            else:
                new_name = f"{resolved_path.name}_{timestamp}"
            target_path = trash_dir / new_name

        shutil.move(str(resolved_path), str(target_path))
        return f"已移动到垃圾桶: {resolved_path.name}"

    except PermissionError:
        return f"权限不足，无法删除: {path}"
    except Exception as e:
        logger.error("[trash] 删除失败: %s", e)
        return f"删除失败: {e}"
