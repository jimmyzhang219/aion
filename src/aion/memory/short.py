"""短期记忆模块 — 会话级 JSONL 持久化。

格式（每行一个 JSON）：
  {"type":"message","message":{"role":"user|assistant","content":"...","timestamp":"..."}}
  {"type":"compaction","message":{"role":"system","content":"摘要","timestamp":"..."}}

路径：{sessions_dir}/{YYYY-MM-DD_HH-MM-SS}_{session_id}.jsonl
"""

import json
import re
from pathlib import Path
from datetime import datetime
from typing import Optional


class SessionStore:
    """短期记忆 — 会话级 JSONL 文件。

    每个 session_id 对应一个独立 JSONL 文件。
    重启后若存在同一 session_id 的活跃文件，会自动复用最新的而非新建。
    """

    def __init__(self, session_id: str, session_dir: Path):
        self.session_id = session_id
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)

        existing = self._find_existing()
        if existing is not None:
            self.file_path = existing
        else:
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            self.file_path = self.session_dir / f"{timestamp}_{session_id}.jsonl"
            # Touch the file to ensure it exists
            self.file_path.touch()

    _FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})_(.+)\.jsonl$")

    def _find_existing(self) -> Optional[Path]:
        best: Optional[Path] = None
        best_ts: str = ""
        for f in self.session_dir.iterdir():
            if not f.is_file() or f.suffix != ".jsonl":
                continue
            m = self._FILENAME_RE.match(f.name)
            if not m:
                continue
            if m.group(2) == self.session_id:
                if not best or m.group(1) > best_ts:
                    best = f
                    best_ts = m.group(1)
        return best

    def append(self, user_content: str, assistant_content: str, reasoning_content: str = "") -> None:
        """追加一轮 user + assistant 对话到 JSONL 文件。"""
        ts = datetime.now().isoformat()
        user_msg = {"role": "user", "content": user_content, "timestamp": ts}
        assistant_msg = {"role": "assistant", "content": assistant_content, "timestamp": ts}
        if reasoning_content:
            assistant_msg["reasoning_content"] = reasoning_content
        lines = (
            json.dumps({"type": "message", "message": user_msg}, ensure_ascii=False)
            + "\n"
            + json.dumps({"type": "message", "message": assistant_msg}, ensure_ascii=False)
            + "\n"
        )
        with open(self.file_path, "a") as f:
            f.write(lines)

    def append_messages(self, messages: list[dict]) -> None:
        """追加多条消息到 JSONL 文件，每条一行。

        Args:
            messages: 消息 dict 列表（需包含 role, content 等字段）
        """
        ts = datetime.now().isoformat()
        lines = ""
        for msg in messages:
            entry_msg = dict(msg)
            entry_msg["timestamp"] = ts
            lines += json.dumps({"type": "message", "message": entry_msg}, ensure_ascii=False) + "\n"
        with open(self.file_path, "a") as f:
            f.write(lines)

    def append_entry(self, entry: dict) -> None:
        """追加一条原始 JSONL 条目。"""
        with open(self.file_path, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_all_entries(self) -> list[dict]:
        """读取 JSONL 全部条目。"""
        if not self.file_path.exists():
            return []
        entries = []
        with open(self.file_path) as f:
            for line in f:
                if line.strip():
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return entries

    def get_messages(self) -> list[dict]:
        """读取所有对话消息（跳过 compaction 条目）。"""
        entries = self.get_all_entries()
        messages = []
        for e in entries:
            if e.get("type") == "compaction":
                continue
            msg = e.get("message", {})
            if "content" in msg:
                messages.append(msg)
        return messages

    def get_compaction_boundary(self) -> tuple[Optional[dict], list[dict]]:
        """返回 (last_compaction_entry, subsequent_messages)。"""
        all_entries = self.get_all_entries()
        last_compaction: Optional[dict] = None
        last_compaction_index = -1
        for i, entry in enumerate(all_entries):
            if entry.get("type") == "compaction":
                last_compaction = entry
                last_compaction_index = i

        subsequent = []
        start = last_compaction_index + 1 if last_compaction_index >= 0 else 0
        for entry in all_entries[start:]:
            if entry.get("type") == "compaction":
                continue
            msg = entry.get("message", {})
            if "content" in msg:
                subsequent.append(msg)

        return last_compaction, subsequent

    def clear(self) -> None:
        """标记会话结束，保留 JSONL 文件（不删除）。"""
        pass

    def start_new(self, new_session_id: str) -> None:
        """开始新会话：创建一个新的 timestamped 文件（旧文件保留不删）。"""
        self.session_id = new_session_id
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.file_path = self.session_dir / f"{timestamp}_{new_session_id}.jsonl"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.file_path.touch()
