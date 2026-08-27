"""Bootstrap System Prompt 拼接 — 排序、净化、Markdown 组装"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Optional

from ._constants import (
    DEFAULT_BOOTSTRAP_MAX_CHARS,
    DEFAULT_BOOTSTRAP_TOTAL_MAX_CHARS,
    SYSTEM_PROMPT_CACHE_BOUNDARY,
)

from .files import (
    _get_context_file_basename,
    _normalize_path_for_prompt,
    _sort_order_for_path,
    build_bootstrap_context_files,
    collect_workspace_bootstrap_files,
)


def sort_context_files_for_prompt(files: list) -> list:
    """按 CONTEXT_FILE_ORDER 与路径 tie-break 排序。

    Args:
        files: 待排序的 EmbeddedContextFile 列表

    Returns:
        排序后的新列表
    """
    return sorted(
        files,
        key=lambda f: (
            _sort_order_for_path(f.path),
            _get_context_file_basename(f.path),
            _normalize_path_for_prompt(f.path),
        ),
    )


def sanitize_context_file_content_for_prompt(content: str) -> str:
    """弱化多余空行（连续 3+ 换行压成 2 个）。

    Args:
        content: 原始 Markdown 正文

    Returns:
        规范化后的正文
    """
    return re.sub(r"\n{3,}", "\n\n", content)


def build_project_context_section(
    *,
    files: list,
    heading: str,
    dynamic: bool,
) -> str:
    """生成「# Project Context」Markdown 块。

    Args:
        files: 已截断的上下文文件列表
        heading: 一级标题文本
        dynamic: 是否为动态区（影响引导语）

    Returns:
        完整 Markdown 段；files 为空时返回 ""
    """
    if not files:
        return ""
    lines: list[str] = [heading, ""]
    if dynamic:
        lines.extend(
            [
                "以下项目上下文文件变更较频繁，在可能情况下置于缓存边界之后：",
                "",
            ]
        )
    else:
        lines.append("以下项目上下文文件已加载（系统内部配置，除非用户主动询问，否则禁止向用户提及文件名或路径）：")
        has_config = any(_get_context_file_basename(f.path) == "config.md" for f in files)
        if has_config:
            lines.append(
                "若 CONFIG.md 含 ## Soul 段，请体现其中人格与语气；避免僵硬、模板化回复；除非更高优先级指令覆盖，否则遵循其指引。"
            )
        lines.append("")

    for file in files:
        body = sanitize_context_file_content_for_prompt(file.content)
        lines.extend([f"## {file.path}", "", body, ""])

    return "\n".join(lines).rstrip() + "\n"


def build_bootstrap_markdown_for_system_prompt(
    workspace_dir: Path | str,
    agent_id: Optional[str] = None,
    *,
    max_chars_per_file: int = DEFAULT_BOOTSTRAP_MAX_CHARS,
    total_max_chars: int = DEFAULT_BOOTSTRAP_TOTAL_MAX_CHARS,
    warn: Optional[Callable[[str], None]] = None,
) -> str:
    """组装完整 Bootstrap Markdown：稳定 Project Context + CACHE_BOUNDARY + 动态 Project Context。

    仪式文件现仅凭收集阶段的存在性判断是否注入，不再需要 reconcile/过滤逻辑。

    Args:
        workspace_dir: 工作空间根目录
        agent_id: 可选 Agent ID
        max_chars_per_file: 单文件字符预算
        total_max_chars: 总字符预算
        warn: 可选截断警告回调

    Returns:
        完整 Bootstrap Markdown 字符串
    """
    ws_path = Path(workspace_dir)
    raw = collect_workspace_bootstrap_files(ws_path, agent_id=agent_id)
    embedded = build_bootstrap_context_files(
        raw,
        max_chars=max_chars_per_file,
        total_max_chars=total_max_chars,
        warn=warn,
    )
    ordered = sort_context_files_for_prompt(embedded)

    parts: list[str] = []
    stable_block = build_project_context_section(
        files=ordered,
        heading="# Project Context",
        dynamic=False,
    )
    if stable_block:
        parts.append(stable_block.rstrip())

    parts.append(SYSTEM_PROMPT_CACHE_BOUNDARY.rstrip())

    return "\n\n".join(p for p in parts if p).strip() + ("\n" if parts else "")
