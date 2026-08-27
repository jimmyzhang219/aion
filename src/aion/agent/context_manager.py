"""上下文管理 — ContextManager

封装 Context、Compaction、Pruning、持久化、System Prompt 构建。
从 AgentLoop 中的 context/compaction/pruning/persist/system_prompt 相关职责提取。
"""

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Any, TypedDict

from .context import Context
from .startup_memory import build_current_time_line
from .thinking_parser import THINK_PAT
from aion.channels.constants import ContentBlockType


def _extract_indexable_text(content: str | list[dict]) -> str:
    """从多模态 content blocks 中提取纯文本部分用于向量索引。

    当内容为 list[dict] 时（含图片/视频等非文本块），
    只保留 type=text 的块，丢弃 base64 等不可索引数据。
    """
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == ContentBlockType.TEXT:
            parts.append(block.get("text", ""))
    return " ".join(parts).strip() or ""


from ..llm.tokenizer import count_message_tokens
from ..session.context_trimmer import prune_context, cleanup_orphaned_tool_calls
from ..log import get_trace_logger

logger = get_trace_logger(__name__)


class UsageAccumulator(TypedDict):
    """Token 用量累加器。"""

    input_tokens: int
    output_tokens: int
    total_tokens: int


class ContextManager:
    """上下文管理 — Context + Compaction + Pruning + 持久化 + System Prompt。"""

    def __init__(
        self,
        llm: Any,
        session_id: str,
        workspace_dir: Path,
        agent_id: str,
        context_window_tokens: int,
        memory_config: Optional[dict] = None,
        is_subagent: bool = False,
        subagent_system_prompt: str = "",
    ):
        self.context = Context()
        self._accumulated_usage: UsageAccumulator = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self._workspace_dir = workspace_dir
        self._agent_id = agent_id
        self._session_id = session_id
        self._llm = llm
        self._memory_config = memory_config or {}
        self._is_subagent = is_subagent
        self._subagent_system_prompt = subagent_system_prompt
        self._context_window_tokens = context_window_tokens

        sessions_dir = workspace_dir / "agents" / agent_id / "sessions"
        from ..memory.short import SessionStore

        self._short_memory = SessionStore(session_id, sessions_dir)

        # 初始化向量索引器
        from ..memory.indexer import VectorIndexer

        embedding_config = self._memory_config.get("embedding")
        self._vector_indexer = VectorIndexer(
            workspace_dir=workspace_dir,
            agent_id=agent_id,
            embedding_config=embedding_config,
        )

        # 跟踪异步索引任务，persist_turn 时等待完成
        self._pending_index_tasks: set[asyncio.Task] = set()

        def _track_task(task: asyncio.Task) -> None:
            self._pending_index_tasks.add(task)
            task.add_done_callback(self._pending_index_tasks.discard)

        # DailyFileStore — on_write 回调触发异步全量覆盖索引
        from ..memory.mid import DailyFileStore
        from ..memory.indexer import WriteTask

        def _on_daily_write(file_path: Path, content: str) -> None:
            rel_path = str(file_path.relative_to(workspace_dir))
            date_str = file_path.stem
            task = asyncio.create_task(
                self._vector_indexer.handle_task(
                    WriteTask(
                        type="overwrite_daily",
                        text=content,
                        metadata={"path": rel_path, "source": "daily", "date": date_str},
                    )
                )
            )
            _track_task(task)

        self._mid_memory = DailyFileStore(workspace_dir, agent_id=agent_id, on_write=_on_daily_write)

        # LongTermStore — on_write 回调触发异步全量覆盖索引
        from ..memory.long import LongTermStore

        def _on_memory_write(file_path: Path, content: str) -> None:
            rel_path = str(file_path.relative_to(workspace_dir))
            date_str = datetime.now().strftime("%Y-%m-%d")
            task = asyncio.create_task(
                self._vector_indexer.handle_task(
                    WriteTask(
                        type="overwrite_memory",
                        text=content,
                        metadata={"path": rel_path, "source": "memory", "date": date_str},
                    )
                )
            )
            _track_task(task)

        self._long_memory = LongTermStore(workspace_dir, agent_id=agent_id, on_write=_on_memory_write)

        from ..session.compaction import Compaction

        self.compaction = Compaction(
            llm=llm,
            session_id=session_id,
            sessions_dir=sessions_dir,
            session_file_path=self._short_memory.file_path,
            trigger_ratio=0.8,
            context_window_tokens=context_window_tokens,
        )

        self._load_history()

    # === 消息操作代理 ===

    @property
    def messages(self) -> list[dict]:
        return self.context.messages

    @messages.setter
    def messages(self, value: list[dict]) -> None:
        self.context.messages = value

    def add_user(self, content: str) -> None:
        self.context.add_user(content)

    def add_assistant(self, content: str, reasoning_content: str = "", tool_calls: Optional[list] = None) -> None:
        self.context.add_assistant(content, reasoning_content, tool_calls=tool_calls)

    def get_messages(self) -> list[dict]:
        return self.context.get_messages()

    # === 运行时 ===

    async def compact_if_needed(self, messages: list[dict]) -> tuple[list[dict], Optional[dict]]:
        estimated_tokens = count_message_tokens(messages)
        if self.compaction.should_compact(estimated_tokens, self._context_window_tokens):
            messages, metadata = await self.compaction.compact(messages)
            self.context.messages = messages
            compaction_usage = metadata.get("usage")
            if compaction_usage:
                self._accumulated_usage["input_tokens"] += compaction_usage.get("input_tokens", 0) or 0
                self._accumulated_usage["output_tokens"] += compaction_usage.get("output_tokens", 0) or 0
                self._accumulated_usage["total_tokens"] += compaction_usage.get("total_tokens", 0) or 0
            logger.debug(f"[Compaction] checkpoint_uuid={metadata['checkpoint_uuid']}")
            return messages, metadata
        return messages, None

    def prune(self, messages: list[dict]) -> list[dict]:
        messages = prune_context(messages, self._context_window_tokens)
        messages = cleanup_orphaned_tool_calls(messages)
        return messages

    async def hard_cap_safety_net(self, messages: list[dict]) -> list[dict]:
        hard_est = count_message_tokens(messages)
        if hard_est >= self._context_window_tokens:
            messages, metadata = await self.compaction.compact(messages)
            self.context.messages = messages
        return messages

    def append_usage(self, usage: dict) -> None:
        self._accumulated_usage["input_tokens"] += int(usage.get("input_tokens", 0) or 0)
        self._accumulated_usage["output_tokens"] += int(usage.get("output_tokens", 0) or 0)
        self._accumulated_usage["total_tokens"] += int(usage.get("total_tokens", 0) or 0)

    # === 持久化 ===

    async def persist_turn(self, turn_messages: list[dict]) -> None:
        """持久化一轮对话消息到 session JSONL。"""
        self._short_memory.append_messages(turn_messages)

        # 等待所有异步索引任务完成
        if self._pending_index_tasks:
            await asyncio.gather(*self._pending_index_tasks, return_exceptions=True)

        # session 级同步索引
        first_user_raw = ""
        last_assistant = ""
        for m in turn_messages:
            raw_uc = m.get("content", "")
            is_nonempty_user = (isinstance(raw_uc, str) and raw_uc.strip()) or isinstance(raw_uc, list)
            if m.get("role") == "user" and is_nonempty_user and not first_user_raw:
                first_user_raw = m["content"]
            if m.get("role") == "assistant" and isinstance(m.get("content", ""), str) and m["content"].strip():
                last_assistant = m["content"]

        from ..memory.indexer import WriteTask

        session_rel_path = str(self._short_memory.file_path.relative_to(self._workspace_dir))
        date_str = datetime.now().strftime("%Y-%m-%d")
        first_user_text = _extract_indexable_text(first_user_raw)
        combined = f"user: {first_user_text}\nassistant: {last_assistant}"
        task = WriteTask(
            type="append_session",
            text=combined,
            metadata={"path": session_rel_path, "source": "sessions", "date": date_str},
        )
        await self._vector_indexer.handle_task(task)

    # === System Prompt ===

    def build_system_prompt(
        self,
        memory_config: Optional[dict] = None,
        is_subagent: bool = False,
        subagent_system_prompt: str = "",
    ) -> None:
        from .prompt import build_system_prompt

        sections = build_system_prompt(
            workspace_dir=self._workspace_dir,
            agent_id=self._agent_id,
            memory_config=memory_config or self._memory_config,
            is_subagent=is_subagent or self._is_subagent,
            subagent_system_prompt=subagent_system_prompt or self._subagent_system_prompt,
        )
        built_sections = [{"role": "system", "content": s} for s in sections]
        history = list(self.context.messages)
        self.context.messages = built_sections + history

    def refresh_system_prompt(self) -> None:
        non_system = [m for m in self.context.messages if m.get("role") != "system"]
        self.context.messages = non_system
        self.build_system_prompt()
        logger.info("System prompt refreshed (bootstrap state changed)")

    def set_time_anchor(self, now_ms: int) -> None:
        """在 context.messages 最前面插入/更新 Current time system 消息。

        Args:
            now_ms: 毫秒时间戳（通常来自 QueueItem.received_at_ms）
        """
        time_line = build_current_time_line(
            self._workspace_dir,
            self._agent_id,
            now_ms=now_ms,
        )
        # 移除所有旧的 Current time 锚点（role=system 且 content 以 Current time: 开头）
        self.context.messages = [
            m
            for m in self.context.messages
            if not (
                m.get("role") == "system"
                and isinstance(m.get("content"), str)
                and m["content"].startswith("Current time:")
            )
        ]
        # 插入到最前面（即所有 system prompt 之前）
        self.context.messages.insert(0, {"role": "system", "content": time_line})

    # === 生命周期 ===

    def reset(self, new_session_id: Optional[str] = None) -> None:
        self.context.messages = []
        self.build_system_prompt()
        if new_session_id:
            self._short_memory.start_new(new_session_id)
            self.compaction.session_file_path = self._short_memory.file_path
            self.compaction.session_id = new_session_id
        else:
            self._short_memory.clear()

    def _load_history(self) -> None:
        last_compaction, messages = self._short_memory.get_compaction_boundary()
        if last_compaction is not None:
            summary = last_compaction.get("message", {}).get("content", "")
            if summary:
                self.context.messages.append(
                    {
                        "role": "system",
                        "content": f"[对话历史摘要]\n\n{summary}",
                    }
                )
        for msg in messages:
            raw_content = msg["content"]
            content: str
            if isinstance(raw_content, str):
                content = re.sub(THINK_PAT, "", raw_content).strip()
            else:
                # 多模态内容（list[dict]）— 保留原样，不适用 regex
                content = raw_content  # type: ignore[assignment]
            if msg["role"] == "user":
                self.context.add_user(content)
            elif msg["role"] == "assistant":
                rc = msg.get("reasoning_content", "")
                tc = msg.get("tool_calls")
                self.context.add_assistant(content, reasoning_content=rc, tool_calls=tc)
            elif msg["role"] == "tool":
                self.context.add_tool(content, msg.get("tool_call_id", ""), msg.get("name", ""))

    # === 互斥标记（/new vs 空闲超时） ===

    _idle_mutex: bool = False

    @property
    def idle_mutex(self) -> bool:
        return self._idle_mutex

    @idle_mutex.setter
    def idle_mutex(self, value: bool) -> None:
        self._idle_mutex = value

    async def generate_and_write_daily_summary(self, session_id: str) -> None:
        """读取指定 session 的消息，生成 LLM 摘要并写入每日记忆。

        Args:
            session_id: 要读取的 session 标识
        """
        if self.idle_mutex:
            logger.debug("[DailySummary] idle_mutex 已锁，跳过")
            return
        self.idle_mutex = True
        try:
            from ..memory.short import SessionStore

            sessions_dir = self._workspace_dir / "agents" / self._agent_id / "sessions"
            temp_store = SessionStore(session_id, sessions_dir)
            messages = temp_store.get_messages()
            if not messages:
                logger.debug("[DailySummary] session %s 无消息，跳过", session_id)
                return

            history_lines = []
            for m in messages:
                role = m.get("role", "unknown")
                content = (m.get("content") or "")[:500]
                if content:
                    history_lines.append(f"[{role}]: {content}")

            if not history_lines:
                return

            from langchain_core.messages import HumanMessage

            prompt = (
                "你是一个对话摘要助手。请将以下对话压缩为一段简短的摘要，"
                "保留所有关键信息、决定、偏好和待办事项。"
                "摘要应该人类可读，能让人快速回顾对话内容。\n\n"
                "=== 对话历史 ===\n" + "\n".join(history_lines) + "\n\n=== 摘要 ==="
            )
            msg = await self._llm.ainvoke([HumanMessage(content=prompt)])
            summary = (msg.content or "").strip()
            # 记录 Generation（非阻塞，失败不影响主流程）
            if summary:
                try:
                    from ..observability import Tracer

                    if Tracer.available:
                        usage_meta = getattr(msg, "usage_metadata", None) or {}
                        Tracer.generation(
                            trace_id=self._session_id,
                            name="daily_summary",
                            model=getattr(self._llm, "model", "unknown"),
                            input=prompt,
                            output=summary,
                            usage={
                                "input": usage_meta.get("input_tokens", 0),
                                "output": usage_meta.get("output_tokens", 0),
                                "unit": "TOKENS",
                            },
                        )
                except Exception:
                    pass
            if summary:
                self._mid_memory.append(summary)
                logger.info("[DailySummary] 已写入每日记忆摘要（session: %s）", session_id)
        except Exception as e:
            logger.warning("[DailySummary] 生成/写入摘要失败: %s", e)
        finally:
            self.idle_mutex = False

    # === 查询 ===

    @property
    def short_memory(self):
        return self._short_memory

    @property
    def daily_file_store(self):
        return self._mid_memory

    @property
    def long_memory(self):
        return self._long_memory

    @property
    def accumulated_usage(self) -> UsageAccumulator:
        return self._accumulated_usage

    @property
    def session_id(self) -> str:
        return self.compaction.session_id

    @session_id.setter
    def session_id(self, value: str) -> None:
        self.compaction.session_id = value
