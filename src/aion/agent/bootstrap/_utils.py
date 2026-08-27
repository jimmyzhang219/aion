"""Internal utility functions — extracted from _constants.py to separate concerns."""

from __future__ import annotations

import re


def _extract_md_field_value(text: str, field_name: str) -> str:
    """从 Markdown 列表项 ``- **字段名：** 值`` 中提取字段值。"""
    pat = rf"^- \*\*{re.escape(field_name)}：\*\*\s*(.+?)\s*$"
    m = re.search(pat, text, flags=re.MULTILINE)
    if not m:
        return ""
    return m.group(1).strip()


def _is_effective_value(v: str) -> bool:
    """判断引导字段值是否「有效」（非空且非占位符）。"""
    if not v:
        return False
    bad_tokens = [
        "待补充",
        "未设置",
        "可选",
        "（",
        "_（",
        "（待",
        "(待",
        "_",
    ]
    return not any(tok in v for tok in bad_tokens)
