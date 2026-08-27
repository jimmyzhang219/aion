"""会话压缩（Compaction）

当上下文接近模型 token 上限时，将历史对话 LLM 摘要化并写回 session JSONL，
同时创建 checkpoint 快照供回溯。由 AgentLoop 在 run 前自动触发或 /compact 手动触发。
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

from ..llm.tokenizer import count_tokens


def _content_to_summary_text(content: str | list[dict] | Any) -> str:
    """将消息 content 转为摘要用的纯文本

    - ``str`` → 原样返回（并添加 role label）
    - ``list[dict]`` → 仅提取 text blocks 拼接
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
        return " ".join(parts) if parts else f"[{len(content)} blocks, no text]"
    return str(content)


class Compaction:
    """会话压缩

    完整压缩流程：
    1. memory flush（可选）：silent turn 把重要信息写入 memory
    2. capture snapshot：当前 session JSONL 复制为 .checkpoint.{uuid}.jsonl
    3. summarize：调用 LLM 将历史消息压缩为摘要
    4. 写回：摘要覆盖 session 中的历史消息
    5. 记录：在 session entry 中记录 compactionCheckpoints[]

    触发条件：
    - 上下文比例：消息 token 达到模型上限的 N%（默认 80%）
    - 手动触发：/compact 命令强制压缩
    - 溢出重试：上下文超出时自动触发
    - 超时重试：LLM 调用超时后触发
    """

    def __init__(
        self,
        llm: Any,  # BaseLLM 实例
        session_id: str,
        sessions_dir: Path | str,
        session_file_path: Path | str | None = None,
        trigger_ratio: float = 0.8,
        compact_model: Optional[str] = None,
        context_window_tokens: Optional[int] = None,
    ):
        """初始化 Compaction 实例。

        Args:
            llm: 用于生成摘要的 LLM 实例
            session_id: 当前 session ID
            sessions_dir: session JSONL 所在目录
            session_file_path: session JSONL 文件完整路径；未提供时按 *_{session_id}.jsonl 查找
            trigger_ratio: 触发压缩的上下文占用比例阈值（相对 context_window_tokens）
            compact_model: 可选专用压缩模型名（未使用时走 llm 默认）
            context_window_tokens: 上下文 token 上限，默认 200000
        """
        self.llm = llm
        self.session_id = session_id
        self.sessions_dir = Path(sessions_dir)
        self.trigger_ratio = trigger_ratio
        self.compact_model = compact_model
        self.context_window_tokens = context_window_tokens or 200000
        self.compaction_checkpoints: list[dict] = []  # 内存中的 checkpoint 记录

        if session_file_path is not None:
            self._session_file_path = Path(session_file_path)
        else:
            # 兼容旧格式或构造时未传入路径时的回退查找
            candidates = list(self.sessions_dir.glob(f"*_{self.session_id}.jsonl"))
            if candidates:
                self._session_file_path = candidates[0]
            else:
                self._session_file_path = self.sessions_dir / f"{self.session_id}.jsonl"

    @property
    def session_file_path(self):
        return self._session_file_path

    @session_file_path.setter
    def session_file_path(self, value):
        self._session_file_path = Path(value)

    def _get_session_file(self) -> Path:
        """返回当前会话的 transcript 文件路径。

        Returns:
            由构造函数 session_file_path 参数确定的 transcript 文件路径；
            若未提供，则通过 glob 匹配 *_{session_id}.jsonl 确定。
        """
        return self.session_file_path

    def _get_checkpoint_file(self) -> tuple[Path, str]:
        """生成 checkpoint 快照文件路径。

        Returns:
            (checkpoint_path, checkpoint_uuid) 元组
        """
        checkpoint_uuid = str(uuid.uuid4())[:8]
        checkpoint_path = self.sessions_dir / f".checkpoint.{checkpoint_uuid}.{self.session_id}.jsonl"
        return checkpoint_path, checkpoint_uuid

    def should_compact(self, context_tokens: int, max_tokens: Optional[int] = None) -> bool:
        """判断当前上下文 token 数是否达到压缩阈值。

        Args:
            context_tokens: 估算的当前上下文 token 数
            max_tokens: 可选覆盖实例级 max_tokens

        Returns:
            True 表示应触发 compact
        """
        limit = max_tokens if max_tokens is not None else self.context_window_tokens
        if limit <= 0:
            return False
        ratio = context_tokens / limit
        return ratio >= self.trigger_ratio

    async def compact(self, messages: list[dict]) -> tuple[list[dict], dict]:
        """执行完整压缩流程。

        Args:
            messages: 压缩前的完整消息列表（含 system）

        Returns:
            (compressed_messages, metadata) 元组；
            metadata 含 checkpoint_uuid、summary、original_count、usage 等
        """
        # Step 1：复制当前 JSONL 为 .checkpoint.{uuid} 快照，便于压缩后回溯
        session_file = self._get_session_file()
        checkpoint_path, checkpoint_uuid = self._get_checkpoint_file()

        if session_file.exists():
            checkpoint_path.write_text(session_file.read_text(encoding="utf-8"), encoding="utf-8")

        # Step 2：从最新消息开始取，在上下文窗口内尽量多取，不截断内容
        budget_tokens = int(self.context_window_tokens * 0.7)  # 预留 70% 给摘要输入
        recent_messages = self._select_recent_fitting(messages, budget_tokens)
        summary_prompt = self._build_summary_prompt(recent_messages)

        from langchain_core.messages import HumanMessage

        msg = await self.llm.ainvoke([HumanMessage(content=summary_prompt)])
        summary = (msg.content or "").strip()

        # === Step 3: 组装压缩后上下文 ===
        # 保留首条 system、一条 compaction 摘要 assistant、以及最近 4 条对话（不足则保留最后 1 条）
        recent = messages[-4:] if len(messages) > 4 else [messages[-1]] if messages else []

        compaction_marker = (
            f"[Compaction 摘要 | {datetime.now().isoformat()} | "
            f"checkpoint: {checkpoint_uuid} | "
            f"原始消息数: {len(messages)}]\n\n{summary}"
        )

        compressed = [
            *messages[:1],  # 保留 system prompt
            {"role": "assistant", "content": compaction_marker, "is_compaction": True},
            *recent,
        ]

        # === Step 4: 写回 session JSONL ===
        self._write_compacted_session(compressed, summary, checkpoint_uuid, checkpoint_path)

        # === Step 5: 记录 checkpoint ===
        checkpoint_record = {
            "checkpoint_uuid": checkpoint_uuid,
            "checkpoint_path": str(checkpoint_path),
            "timestamp": datetime.now().isoformat(),
            "original_count": len(messages),
            "compressed_count": len(compressed),
            "summary": summary,
        }
        self.compaction_checkpoints.append(checkpoint_record)

        # === Step 6: 返回 metadata ===
        metadata = {
            "checkpoint_uuid": checkpoint_uuid,
            "checkpoint_path": str(checkpoint_path),
            "summary": summary,
            "original_count": len(messages),
            "compressed_count": len(compressed),
            "timestamp": datetime.now().isoformat(),
            "usage": getattr(msg, "usage_metadata", None),
        }

        return compressed, metadata

    def _select_recent_fitting(self, messages: list[dict], budget_tokens: int) -> list[dict]:
        """从最新消息开始选，在 budget_tokens 内尽量多取，不截断内容。

        单条超限时不丢弃（利用 prompt 预留的 30% 缓冲空间）。
        """
        selected: list[dict] = []
        total = 0
        for m in reversed(messages):
            raw = m.get("content")
            if isinstance(raw, str):
                content = raw.strip()
            elif isinstance(raw, list):
                content = " ".join(b.get("text", "") for b in raw if isinstance(b, dict) and b.get("type") == "text")
            else:
                content = ""
            if not content:
                continue
            line = f"[{m.get('role', 'unknown')}]: {content}"
            msg_tokens = count_tokens(line) + 1  # +1 for \n
            if selected and total + msg_tokens > budget_tokens:
                break
            selected.append(m)
            total += msg_tokens
        selected.reverse()
        return selected if selected else messages[-1:]

    def _build_summary_prompt(self, messages: list[dict]) -> str:
        """将消息拼接为 LLM 摘要提示词（不截断内容）。

        Args:
            messages: 待摘要的消息子集

        Returns:
            完整的 user 侧摘要请求文本
        """
        history_text = "\n".join(
            f"[{m.get('role', 'unknown')}]: {_content_to_summary_text(m.get('content', ''))}"
            for m in messages
            if m.get("content")
        )
        return (
            "你是一个对话历史压缩助手。请将以下对话历史压缩为一段简短的摘要，"
            "保留所有关键信息、决定、偏好和待办事项。摘要应该人类可读，"
            "能让人快速回顾对话内容。\n\n"
            f"=== 对话历史 ===\n{history_text}\n\n=== 摘要 ==="
        )

    def _write_compacted_session(
        self,
        compressed: list[dict],
        summary: str,
        checkpoint_uuid: str,
        checkpoint_path: str | Path,
    ) -> None:
        """追加 compaction 条目到 session JSONL（不删除原始消息）。

        Args:
            compressed: 压缩后的消息列表
            summary: LLM 生成的摘要文本
            checkpoint_uuid: 快照 UUID
            checkpoint_path: 快照文件路径

        Returns:
            None
        """
        session_file = self._get_session_file()

        compaction_entry = {
            "type": "compaction",
            "message": {
                "role": "system",
                "content": summary,
                "timestamp": datetime.now().isoformat(),
            },
        }
        with open(session_file, "a") as f:
            f.write(json.dumps(compaction_entry, ensure_ascii=False) + "\n")
