"""Read 工具模块

读取文件内容，支持行偏移（offset）与行数限制（limit），
并根据上下文窗口大小自适应分页，避免单次返回过大。

@tool 版本只暴露 LLM 需要的参数（path/offset/limit）；
context_window_tokens 由 loader 注入。
"""

from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

from aion.core.context import current_workspace
from aion.log import get_logger

logger = get_logger(__name__)


def read_impl(
    path: str,
    offset: int = 1,
    limit: Optional[int] = None,
    context_window_tokens: int = 200000,
) -> str:
    """读取文件内容的底层实现

    Args:
        path: 文件路径（绝对路径）
        offset: 起始行号（1-based，默认 1）
        limit: 最大返回行数（None 时按上下文窗口自适应分页）
        context_window_tokens: 模型上下文窗口 token 数，用于计算自适应上限

    Returns:
        文件内容文本；若被截断则附加 offset 提示以便继续读取
    """
    resolved = Path(path).expanduser()
    if not resolved.exists():
        return f"文件不存在: {path}"
    try:
        text = resolved.read_text(encoding="utf-8")
    except Exception as e:
        logger.error("[read] 读取失败: %s", e)
        return f"读取失败: {e}"

    lines = text.splitlines(keepends=True)
    # 防御：Pydantic 可能通过 "number" schema 将 int→float，slice 索引要求 int
    offset = int(offset)
    if limit is not None:
        limit = int(limit)

    # offset 为 1-based 行号，越界时提前返回提示
    if offset < 1:
        offset = 1
    if offset > len(lines):
        return f"offset {offset} 超出文件行数 {len(lines)}"

    # 未指定 limit 时：按上下文窗口 5% 估算字符预算，最多 50K，最多读 4 页
    if limit is None:
        adaptive_chars = min(int(context_window_tokens * 4 * 0.05), 50000)
        max_pages = 4
        full_text = "".join(lines[offset - 1 :])
        if len(full_text) <= adaptive_chars:
            capped_text = full_text
            capped = False
            next_offset = None
        else:
            # 自适应多页：每页约 adaptive_chars 字符，最多 max_pages 页
            page_chars = adaptive_chars
            collected = ""
            current_offset = offset
            page_count = 0
            while page_count < max_pages:
                start = current_offset - 1
                est_lines = 0
                char_count = 0
                # 从 start 行起累加字符，直到超过 page_chars 预算（至少保留一行）
                for i in range(start, len(lines)):
                    line_len = len(lines[i])
                    if char_count + line_len > page_chars and est_lines > 0:
                        break
                    char_count += line_len
                    est_lines += 1
                page_lines = lines[start : start + est_lines]
                page_text = "".join(page_lines)
                collected += page_text
                page_count += 1
                current_offset += est_lines
                if current_offset > len(lines):
                    break
                # 剩余内容为空则提前结束，避免无意义空页
                remaining = "".join(lines[current_offset - 1 :])
                if not remaining.strip():
                    break

            capped_text = collected
            capped = page_count >= max_pages and current_offset <= len(lines)
            next_offset = current_offset if capped else None
    else:
        # 显式 limit：按行号区间截取
        end = offset + int(limit) - 1
        capped_text = "".join(lines[offset - 1 : end])
        capped = end < len(lines)
        next_offset = end + 1 if capped else None

    result = capped_text
    if capped and next_offset:
        byte_size = len(capped_text.encode("utf-8"))
        if byte_size < 1024:
            size_str = f"{byte_size}B"
        elif byte_size < 1024 * 1024:
            size_str = f"{byte_size / 1024:.1f}KB"
        else:
            size_str = f"{byte_size / (1024 * 1024):.1f}MB"
        result += f"\n[Read output capped at {size_str}. Use offset={next_offset} to continue.]"

    return result


@tool(parse_docstring=True)
def read(path: str, offset: int = 1, limit: Optional[int] = None) -> str:
    """读取文本文件内容。支持 txt、md、py、json、yaml、csv 等文本格式。
    不支持二进制文件（图片、PDF 等）。文本输出会被截断（以行数或大小先达到的为准）。
    大文件使用 offset/limit 分段读取。需要完整文件时，用 offset 继续直到读完。

    Args:
        path: 文件路径（绝对路径或相对于工作空间）
        offset: 起始行号（1-based，默认1）
        limit: 最大行数（默认自动）
    """
    ws = current_workspace.get()
    resolved = str((ws / path).resolve()) if not Path(path).is_absolute() else str(Path(path).expanduser().resolve())
    return read_impl(resolved, offset=offset, limit=limit)
