"""LangChain 消息桥接 — dict ↔ BaseMessage 转换"""

import json

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from aion.channels.constants import ContentBlockType


def _content_to_lc_format(content: str | list[dict]) -> str | list[dict]:
    """将内部 content 转为 LangChain/OpenAI 通用格式。

    转换规则（所有 OpenAI 兼容 API 通用）：

    - ``str`` → 原样返回
    - ``{"type": "text"}`` →  ``{"type": "text", "text": "..."}``
    - ``{"type": "image"}`` → ``{"type": "image_url", "image_url": {"url": "data:..."}}``
    - ``video``、``audio`` 等无通用 OpenAI 标准的类型 → 保持内部格式，由 provider 子类处理
    """
    if isinstance(content, str):
        return content

    blocks: list[dict] = []
    for block in content:
        t = block.get("type")
        if t == ContentBlockType.TEXT:
            blocks.append({"type": "text", "text": block.get("text", "")})
        elif t == ContentBlockType.IMAGE:
            data = block.get("data", "")
            mime = block.get("mimeType", "image/jpeg")
            blocks.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
        else:
            # video / audio / 其他 — 无通用 OpenAI 标准格式，保持内部格式
            # dispatch_message 已确认模型支持该模态，由 provider 子类进一步转换
            blocks.append(block)
    return blocks


def dict_messages_to_lc(messages: list[dict]) -> list[BaseMessage]:
    """将 Agent context 的 dict 消息转为 LangChain BaseMessage。

    Args:
        messages: Agent 侧消息字典列表。

    Returns:
        LangChain ``BaseMessage`` 列表。
    """
    out: list[BaseMessage] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            out.append(SystemMessage(content=str(content)))
        elif role == "user":
            lc_content = _content_to_lc_format(content)
            out.append(HumanMessage(content=lc_content))  # type: ignore[arg-type]
        elif role == "assistant":
            tcs = m.get("tool_calls")
            text = str(content or "")
            extra_kwargs = {}
            rc = m.get("reasoning_content")
            if rc:
                extra_kwargs["reasoning_content"] = str(rc)
            if tcs:
                lc_tool_calls: list[dict] = []
                for tc in tcs:
                    if not isinstance(tc, dict):
                        continue
                    tid = str(tc.get("id", "") or "")
                    name = str(tc.get("name", "") or "")
                    args_raw = tc.get("arguments", "{}")
                    if isinstance(args_raw, str):
                        try:
                            args_d: dict = json.loads(args_raw) if args_raw else {}
                        except json.JSONDecodeError:
                            args_d = {}
                    elif isinstance(args_raw, dict):
                        args_d = args_raw
                    else:
                        args_d = {}
                    lc_tool_calls.append({"name": name, "id": tid, "args": args_d})
                out.append(AIMessage(content=text, tool_calls=lc_tool_calls, additional_kwargs=extra_kwargs))
            else:
                out.append(AIMessage(content=text, additional_kwargs=extra_kwargs))
        elif role == "tool":
            out.append(
                ToolMessage(
                    content=str(content),
                    tool_call_id=str(m.get("tool_call_id", "") or ""),
                )
            )
        else:
            out.append(HumanMessage(content=str(content)))
    return out
