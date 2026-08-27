"""永久记忆（MEMORY.md）

全量结构化总结，保存至 agents/{agent_id}/memory/MEMORY.md。
对话时全量加载进 system prompt。
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class LongTermStore:
    """长期记忆 — agents/{agent_id}/memory/MEMORY.md。

    由 LLM 通过 memory_write 工具全量覆盖写入。
    同步写磁盘后，通过回调触发异步全量覆盖索引。
    全量加载进 system prompt（prompt 中引导 <12000 字符，程序不做硬截断）。
    """

    def __init__(
        self,
        workspace_dir: Path,
        agent_id: str | None = None,
        on_write: Optional[Callable] = None,
    ):
        self.workspace_dir = Path(workspace_dir)
        self.agent_id = agent_id or "main"
        memory_dir = self.workspace_dir / "agents" / self.agent_id / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        self.file_path = memory_dir / "MEMORY.md"
        if not self.file_path.exists():
            self.file_path.write_text("# 永久记忆\n\n")
        self._on_write = on_write

    def overwrite(self, content: str) -> None:
        with open(self.file_path, "w") as f:
            f.write(content)
            if not content.endswith("\n"):
                f.write("\n")
        if self._on_write:
            try:
                self._on_write(self.file_path, content)
            except Exception as e:
                logger.warning("[LongTermStore] on_write 回调失败: %s", e)

    def read_all(self) -> str:
        return self.file_path.read_text(encoding="utf-8")
