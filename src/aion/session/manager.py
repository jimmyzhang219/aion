"""Session 列举器

列举 workspace 下的 Session Transcript 列表。
支持 Agent 独享 sessions 目录（agents/<agent_id>/sessions/）。
"""

import json
from pathlib import Path


class SessionLister:
    """Session 列举 — Agent 独享 sessions 目录 {workspace}/agents/{agent_id}/sessions/"""

    def __init__(
        self,
        workspace_dir: Path,
        agent_id: str = "main",
    ):
        """初始化 Session 管理器并创建 sessions 目录。

        Args:
            workspace_dir: 工作空间根目录。
            agent_id: Agent ID，用于定位 Agent 独享 sessions 子目录，默认 "main"
        """

        self.sessions_dir = workspace_dir / "agents" / agent_id / "sessions"

        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def list_recent(self, limit: int = 10) -> list[dict]:
        """按文件修改时间倒序列出最近的 Session 摘要。

        Args:
            limit: 最多返回的 session 数量

        Returns:
            摘要 dict 列表，含 session_id、message_count、last_updated 等
        """
        if not self.sessions_dir.exists():
            return []

        sessions = []
        for file_path in sorted(self.sessions_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True):
            stem = file_path.stem
            # 剥离时间戳前缀 "YYYY-MM-DD_HH-MM-SS_"（固定 20 字符），保留 session_id
            session_id = stem[20:] if len(stem) > 20 and stem[10] == "_" else stem

            # 跳过 Compaction 产生的 checkpoint 快照文件
            if session_id.startswith(".checkpoint"):
                continue

            try:
                entries = []
                for line in file_path.read_text(encoding="utf-8").strip().split("\n"):
                    if line.strip():
                        try:
                            entries.append(json.loads(line))
                        except Exception:
                            pass

                messages = [e for e in entries if e.get("type") != "compaction" and "content" in e]
                compaction_count = sum(1 for e in entries if e.get("type") == "compaction")

                first_msg = ""
                if messages:
                    first_msg = messages[0].get("content", "")[:100]

                last_updated = None
                if entries:
                    last_updated = entries[-1].get("ts")

                sessions.append(
                    {
                        "session_id": session_id,
                        "file_path": str(file_path),
                        "message_count": len(messages),
                        "compaction_count": compaction_count,
                        "last_updated": last_updated,
                        "first_message": first_msg,
                    }
                )

                if len(sessions) >= limit:
                    break
            except Exception:
                continue

        return sessions
