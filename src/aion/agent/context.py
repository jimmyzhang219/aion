"""上下文管理

维护 Agent 对话消息列表（Context），并提供启动上下文构建、thinking 标签解析等辅助能力。
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)
from typing import Optional

from ..rag.search import MemorySearchTool
from .startup_memory import build_daily_memory_startup_prelude
from .thinking_parser import THINK_PAT, strip_thinking_tags


class Context:
    """对话上下文

    内存中的 messages 列表，供 LLM 请求与 Transcript 持久化使用。
    """

    def __init__(self):
        """初始化空消息列表。"""
        self.messages: list[dict] = []  # [{"role": "user"|"assistant"|"system"|"tool", "content": "..."}]

    def add_user(self, content: str | list[dict]) -> None:
        """追加 user 消息。

        - ``str``: 纯文本，会先 strip thinking 标签
        - ``list[dict]``: 多模态 content blocks，保持原样

        Args:
            content: 用户消息正文（纯文本或多模态 content blocks）

        Returns:
            None
        """
        if isinstance(content, str):
            content = strip_thinking_tags(content)
        self.messages.append({"role": "user", "content": content})

    def add_assistant(self, content: str, reasoning_content: str = "", tool_calls: Optional[list] = None) -> None:
        """追加 assistant 消息（会先 strip thinking 标签）。

        Args:
            content: 助手回复正文
            reasoning_content: 模型返回的 reasoning_content（如有，存入消息供后续回传）
            tool_calls: 工具调用列表（如有，与消息一并存储供跨轮可见）

        Returns:
            None
        """
        content = strip_thinking_tags(content)
        msg: dict = {"role": "assistant", "content": content}
        if reasoning_content:
            msg["reasoning_content"] = reasoning_content
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self.messages.append(msg)

    def add_tool(self, content: str, tool_call_id: str, name: str = "") -> None:
        """追加 tool 消息（工具调用结果）。

        Args:
            content: 工具返回结果
            tool_call_id: 对应的 tool_call ID
            name: 工具名称

        Returns:
            None
        """
        msg = {"role": "tool", "content": content, "tool_call_id": tool_call_id}
        if name:
            msg["name"] = name
        self.messages.append(msg)

    def get_messages(self) -> list[dict]:
        """返回消息副本，content 中会再次剔除 thinking 标签。

        Returns:
            供 LLM 调用的消息 dict 列表
        """
        out: list[dict] = []
        for m in self.messages:
            d = dict(m)
            if "content" in d:
                if isinstance(d["content"], str):
                    d["content"] = THINK_PAT.sub("", d["content"]).strip()
                # list content blocks 不做 strip_thinking
            out.append(d)
        return out

    def reset(self) -> None:
        """重置上下文，保留 system message（如果存在）。

        Returns:
            None
        """
        if self.messages and self.messages[0]["role"] == "system":
            self.messages = [self.messages[0]]
        else:
            self.messages = []


def build_startup_context(
    workspace_dir: Path,
    agent_id: Optional[str] = None,
    memory_config: Optional[dict] = None,
) -> str:
    """构建启动上下文（接在 System Prompt 之后），**不再重复注入 Bootstrap**。

    Bootstrap 已由 ``build_system_prompt`` 注入；本函数对应 startup-context 预载：

    1. 运行时预载的近 N 天 ``memory/YYYY-MM-DD.md``（``[Untrusted daily memory: ...]`` + quoted block）
    2. Startup Memory Recall（跨 session 语义召回）

    ``agent_id`` 保留签名供扩展；当前与日记忆路径解析相关。

    Args:
        workspace_dir: 工作空间根目录
        agent_id: 可选 Agent ID
        memory_config: 记忆相关配置（daily_memory_days、max_total_chars 等）

    Returns:
        拼接后的启动上下文字符串，可能为空
    """
    if memory_config is None:
        memory_config = {}

    max_total_chars_startup = memory_config.get("max_total_chars", 2800)

    sections: list[str] = []

    # 1. Daily memory prelude（[Startup context loaded by runtime] + Untrusted blocks）
    try:
        daily_prelude = build_daily_memory_startup_prelude(
            workspace_dir,
            dict(memory_config),
            agent_id=agent_id,
        )
        if daily_prelude:
            sections.append(daily_prelude)
    except Exception:
        pass

    # 2. Startup Memory Recall（跨 session 召回关键个人信息）
    recall_queries = [
        "用户 名字 用户名 称呼",  # 搜索用户名/称呼
        "偏好 喜欢 讨厌",  # 搜索用户偏好
        "待办 计划 任务",  # 搜索待办事项
    ]

    recall_parts = []
    seen_paths = set()
    try:
        search_tool = MemorySearchTool(
            workspace_dir=workspace_dir,
            agent_id=agent_id,
            max_results=5,
            min_score=0.01,  # 极低阈值，确保找到任何匹配
            daily_memory_days=365,  # 搜索所有日期的记忆
        )

        for query in recall_queries:
            results = search_tool.search(query, sources=None)
            for r in results:
                if r["path"] in seen_paths:
                    continue
                seen_paths.add(r["path"])
                source_tag = "[memory]" if r.get("source") == "memory" else "[untrusted]"
                content = search_tool.get(r["path"])
                recall_parts.append(f"### Recall: {query[:20]}\n\n{source_tag}\n{content}")
    except Exception:
        pass  # 忽略召回失败，不阻塞启动

    if recall_parts:
        recall_section = "\n\n".join(recall_parts)
        sections.append(f"# Startup Context — Memory Recall\n\n{recall_section}")

    result = "\n\n".join(sections)
    if len(result) > max_total_chars_startup:
        logger.warning(
            "Startup context (%d chars) exceeds max_total_chars (%d). Loaded as-is without truncation.",
            len(result),
            max_total_chars_startup,
        )

    return result
