"""DeepSeek V4 模型 LangChain 实现。

DeepSeek V4 使用 OpenAI 兼容 API，但有两个特有问题：
1. thinking mode 需要 ``extra_body = {"thinking": {"type": "enabled"}}``
2. 返回的 ``reasoning_content`` 在 LangChain 标准序列化中被丢弃，
   当涉及工具调用时后续请求必须回传此字段，否则 API 返回 400
   （详见 https://api-docs.deepseek.com/zh-cn/guides/thinking_mode）。

此模块提供 ``ChatDeepSeekV4`` 类，继承 ``ChatOpenAI`` 修复上述问题。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage
from langchain_openai.chat_models.base import (
    _convert_from_v1_to_chat_completions,
    _convert_message_to_dict,
)

from .reasoning import ReasoningOpenAI


class ChatDeepSeekV4(ReasoningOpenAI):
    """DeepSeek V4 通过 OpenAI 兼容 API 的支持。

    继承 ``ChatOpenAI``，复用 OpenAI 兼容 API 的标准处理
    （工具调用、流式、token 计数等）。

    特性：
    - 默认启用 thinking mode（``extra_body.thinking``）
    - 默认 ``reasoning_effort`` 为 ``high``
    - 覆盖 ``_get_request_payload``，当涉及工具调用时回传
      ``reasoning_content``（官方要求，否则返回 400）
    """

    def __init__(self, **kwargs: Any) -> None:
        if "extra_body" not in kwargs:
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        if "reasoning_effort" not in kwargs:
            kwargs["reasoning_effort"] = "high"
        super().__init__(**kwargs)

    def _get_request_payload(
        self,
        input_: Any,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        messages = self._convert_input(input_).to_messages()
        if stop is not None:
            kwargs["stop"] = stop
        payload = {**self._default_params, **kwargs}

        # 使用自定义序列化保留 additional_kwargs（如 reasoning_content）
        payload["messages"] = [_deepseek_v4_convert_message(m) for m in messages]

        # ── ChatDeepSeek 原后处理逻辑 ──
        for message in payload["messages"]:
            if message["role"] == "tool" and isinstance(message["content"], list):
                message["content"] = json.dumps(message["content"])
            elif message["role"] == "assistant" and isinstance(message["content"], list):
                text_parts = [
                    block.get("text", "")
                    for block in message["content"]
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                message["content"] = "".join(text_parts) if text_parts else ""

        return payload


def _deepseek_v4_convert_message(message: Any) -> dict:
    """将 BaseMessage 转为 dict，保留 additional_kwargs 中 DeepSeek 需要的字段。

    LangChain 的 ``_convert_message_to_dict`` 会丢弃 ``additional_kwargs``，
    但涉及工具调用时这些字段（如 ``reasoning_content``）需要回传给 API，
    否则返回 400。
    """
    if isinstance(message, AIMessage):
        d = _convert_message_to_dict(_convert_from_v1_to_chat_completions(message))
    else:
        d = _convert_message_to_dict(message)

    if isinstance(message, AIMessage) and message.additional_kwargs:
        _skip = {"tool_calls", "function_call", "name", "__openai_role__"}
        for k, v in message.additional_kwargs.items():
            if k not in d and k not in _skip:
                d[k] = v
    return d
