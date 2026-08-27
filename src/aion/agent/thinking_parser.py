"""Thinking tag parsing utilities

Provides canonical implementations for extracting and stripping think/thinking
tags from LLM response text.
"""

import re


def extract_thinking_parts(text: str) -> tuple[list[str], str]:
    """从文本中提取 thinking 标签内的推理内容，并返回清除标签后的正文。

    Args:
        text: 可能含 thinking 标签的原始文本

    Returns:
        (thinking_parts, clean_text) 元组：推理片段列表与去掉标签后的文本
    """
    if not text:
        return [], ""
    # 用十六进制转义拼出 think/thinking 标签，避免在源码中直接出现敏感标签名
    T_O = r"<\x74\x68\x69\x6e\x6b>"
    T_C = r"</\x74\x68\x69\x6e\x6b>"
    T_OL = r"<think\x69\x6e\x67>"
    T_CL = r"</think\x69\x6e\x67>"
    thinking_parts = []
    result = text
    pat_ascii = rf"{T_O}\n?([\s\S]*?)\n?{T_C}"
    pat_html = rf"{T_OL}\n?([\s\S]*?)\n?{T_CL}"
    pat_entity = r"&lt;/?think(?:ing)?&gt;\n?([\s\S]*?)\n?&lt;/?think(?:ing)?&gt;"
    # 循环剥离各格式标签，每次只处理第一个匹配
    for pat in [pat_ascii, pat_html, pat_entity]:
        found = True
        while found:
            found = False
            for m in re.finditer(pat, result):
                found = True
                inner = m.group(1).strip()
                if inner:
                    thinking_parts.append(inner)
                result = result[: m.start()] + result[m.end() :]
                break
    # 清理残留的 HTML 实体标签碎片
    for t in [
        "&lt;think&gt;",
        "&lt;/think&gt;",
        "&lt;thinking&gt;",
        "&lt;/thinking&gt;",
        "&lt;/?think&gt;",
        "&lt;/?thinking&gt;",
    ]:
        result = result.replace(t, "")
    return thinking_parts, result.strip()


def strip_thinking_tags(text: str) -> str:
    """去掉 thinking 标签并将推理内容格式化为 [思考] 前缀块。

    Args:
        text: 原始文本

    Returns:
        若含 thinking 则「[思考]\\n...\\n\\n正文」；否则返回 strip 后的正文
    """
    parts, clean = extract_thinking_parts(text)
    if parts:
        formatted = "\n".join(f"[思考]\n{p}" for p in parts)
        return formatted + "\n\n" + clean
    return clean


# 统一 THINK_PAT — 匹配 ASCII/HTML 实体两种 thinking 标签格式
# 在源码中用十六进制转义避免直接出现敏感标签名
THINK_PAT = re.compile(
    r"<\x74\x68\x69\x6e\x6b>[\s\S]*?</\x74\x68\x69\x6e\x6b>"
    r"|<\x74\x68\x69\x6e\x6b\x69\x6e\x67>[\s\S]*?</\x74\x68\x69\x6e\x6b\x69\x6e\x67>"
    r"|&lt;/?think(?:ing)?&gt;[\s\S]*?&lt;/?think(?:ing)?&gt;\s*",
)
