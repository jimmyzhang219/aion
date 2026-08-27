"""后处理 — PostProcessor + DSML/COT 剥离函数

从 AgentLoop._strip_cot_tags()、strip_dsml() 及 run() 末尾的后处理代码提取。
"""

import logging
import re
from typing import Any, TypedDict

from .thinking_parser import extract_thinking_parts

logger = logging.getLogger(__name__)
# CoT 格式标签
_FINAL_PAT = re.compile(
    r"</?final>|&lt;/?final&gt;",
    re.IGNORECASE,
)


def strip_cot_tags(text: str) -> str:
    """清理 CoT 格式标签（<final></final> 等）。"""
    return _FINAL_PAT.sub("", text).strip()


# DSML 标签
_DSML_TAG_PAT = re.compile(r"</?\|\|DSML\|\|\w+[^>]*>")
_DSML_TOOL_CALLS_BLOCK = re.compile(
    r"<\|\|DSML\|\|tool_calls>[\s\S]*?</\|\|DSML\|\|tool_calls>",
)
_DSML_INVOKE_BLOCK = re.compile(
    r"<\|\|DSML\|\|invoke[^>]*>[\s\S]*?</\|\|DSML\|\|invoke>",
)
_DSML_PARAM_BLOCK = re.compile(
    r"<\|\|DSML\|\|parameter[^>]*>[\s\S]*?</\|\|DSML\|\|parameter>",
)


def strip_dsml(text: str) -> str:
    """清理 DSML（DeepSeek Native 工具调用 XML）标签。"""
    stripped = text.strip()
    if not stripped:
        return ""
    is_pure_dsml = bool(re.match(r"^<\|\|DSML\|\|", stripped))
    text = _DSML_TOOL_CALLS_BLOCK.sub("", text)
    text = _DSML_INVOKE_BLOCK.sub("", text)
    text = _DSML_PARAM_BLOCK.sub("", text)
    text = _DSML_TAG_PAT.sub("", text)
    text = text.strip()
    if is_pure_dsml:
        return ""
    return text


class PostProcessResult(TypedDict):
    """后处理结果"""

    response: str
    thinking_parts: list[str]


class PostProcessor:
    """后处理管道 — DSML/COT 剥离 + bootstrap misclaim audit + 空响应 fallback。"""

    def __init__(self, bootstrap_monitor: Any):
        self._bootstrap_monitor = bootstrap_monitor
        self._last_thinking_parts: list[str] = []

    async def process(
        self,
        raw_text: str,
        llm_last_msg: Any,
    ) -> PostProcessResult:
        """执行完整后处理管道。

        1. 从 llm_last_msg 提取 reasoning_content
        2. 从 raw_text 提取 <think> 标签内的 thinking parts
        3. strip CoT 标签
        4. strip DSML 标签
        5. bootstrap misclaim audit（委托 BootstrapMonitor）
        6. 空响应 fallback
        7. 更新 _last_thinking_parts

        Returns:
            PostProcessResult(response, thinking_parts)
        """
        text_content = raw_text.strip()
        thinking_parts: list[str] = []

        if hasattr(llm_last_msg, "additional_kwargs"):
            rc = llm_last_msg.additional_kwargs.get("reasoning_content", "")
            if rc:
                thinking_parts.append(rc)

        if not thinking_parts:
            extra_thinking, text_content = extract_thinking_parts(text_content)
            thinking_parts.extend(extra_thinking)

        text_content = strip_cot_tags(text_content)
        text_content = strip_dsml(text_content)
        text_content = await self._bootstrap_monitor.audit_misclaim(text_content)

        if not text_content and thinking_parts:
            total = sum(len(p) for p in thinking_parts)
            logger.warning(
                f"[PostProcessor] raw_text empty, promoting {len(thinking_parts)} thinking_parts ({total} chars) to response"
            )
            text_content = "\n\n".join(thinking_parts)
            thinking_parts = []

        if not text_content:
            text_content = "（任务因达到工具调用上限而被中断，部分操作可能已完成。）"

        self._last_thinking_parts = thinking_parts
        return PostProcessResult(response=text_content, thinking_parts=thinking_parts)

    @property
    def last_thinking_parts(self) -> list[str]:
        return self._last_thinking_parts
