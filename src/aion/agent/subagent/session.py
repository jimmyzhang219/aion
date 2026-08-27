"""Subagent 会话文件管理。

文件命名：{timestamp}_{session_id}-subagent.jsonl
生命周期结束：重命名为 {…}.jsonl.delete
"""

import json
from datetime import datetime
from pathlib import Path


class SubagentSession:
    """Subagent 专属临时会话文件，一次性使用，无 compaction。"""

    def __init__(self, session_id: str, agent_id: str, workspace_dir: Path):
        session_dir = workspace_dir / "agents" / agent_id / "sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.file_path = session_dir / f"{timestamp}_{session_id}-subagent.jsonl"
        self.file_path.touch()

    def append_messages(self, messages: list[dict]) -> None:
        """追加写入多条消息到 JSONL（'a' 模式，实时持久化）。"""
        ts = datetime.now().isoformat()
        chunks: list[str] = []
        for msg in messages:
            entry = dict(msg)
            entry.setdefault("timestamp", ts)
            chunks.append(json.dumps({"type": "message", "message": entry}, ensure_ascii=False))
        with open(self.file_path, "a") as f:
            f.write("\n".join(chunks) + "\n")

    def mark_deleted(self) -> None:
        """标记生命周期结束：{name}.jsonl → {name}.jsonl.delete"""
        if not self.file_path.exists():
            return
        new_path = self.file_path.with_name(self.file_path.name + ".delete")
        self.file_path.rename(new_path)
