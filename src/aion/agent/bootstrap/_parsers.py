"""Markdown 解析工具 — 章节提取与占位符判断"""

from __future__ import annotations

import re


def _extract_section_content(text: str, section_name: str) -> str:
    """从 Markdown 的 ``## section_name`` 章节中提取正文。

    Args:
        text: 完整 Markdown 文本
        section_name: 章节名称（如"项目 / 领域"）

    Returns:
        章节正文内容；章节不存在时返回空字符串
    """
    pat = rf"^##\s+{re.escape(section_name)}\s*$(.+?)(?=^##\s|\Z)"
    m = re.search(pat, text, flags=re.MULTILINE | re.DOTALL)
    if not m:
        return ""
    return m.group(1).strip()


def _is_placeholder_only(content: str) -> bool:
    """判断章节正文是否仅包含占位符模板（`_(...)_`），未填写实际内容。

    Args:
        content: 章节正文

    Returns:
        True 表示全是占位符，无实际内容
    """
    if not content:
        return True
    cleaned = content.strip().lstrip("- ").strip()
    # 仅占位符模板行（_(...)_）
    return bool(re.fullmatch(r"_\(.*?\)_", cleaned))
