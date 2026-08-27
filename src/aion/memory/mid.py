"""中期记忆（每日摘要）

按日历日期将 LLM 生成的摘要写入 Markdown 文件。
写入后触发异步全量覆盖索引（通过 FIFO 队列）。
"""

from __future__ import annotations
import logging
from pathlib import Path
from datetime import datetime
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class DailyFileStore:
    """中期记忆 — {memory_dir}/YYYY-MM-DD.md。

    只写摘要（非原文），由 LLM 决定何时调用工具写入。
    同步写磁盘后，通过回调触发异步全量覆盖索引。
    """

    def __init__(
        self,
        workspace_dir: Path,
        agent_id: str | None = None,
        on_write: Optional[Callable] = None,
    ):
        self.workspace_dir = Path(workspace_dir)
        self.agent_id = agent_id or "main"
        self.memory_dir = self.workspace_dir / "agents" / self.agent_id / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._on_write = on_write

    def _get_file(self) -> Path:
        date_str = datetime.now().strftime("%Y-%m-%d")
        return self.memory_dir / f"{date_str}.md"

    def append(self, content: str) -> None:
        """追加写 + 触发索引回调。"""
        file_path = self._get_file()
        with open(file_path, "a") as f:
            f.write(f"\n<!-- {datetime.now().isoformat()} -->\n")
            f.write(content)
            if not content.endswith("\n"):
                f.write("\n")

        if self._on_write:
            try:
                self._on_write(file_path, content)
            except Exception as e:
                logger.warning("[DailyFileStore] append on_write 回调失败: %s", e)

    def overwrite(self, content: str) -> None:
        """覆盖写 + 触发索引回调。"""
        file_path = self._get_file()
        with open(file_path, "w") as f:
            f.write(content)
            if not content.endswith("\n"):
                f.write("\n")

        if self._on_write:
            try:
                self._on_write(file_path, content)
            except Exception as e:
                logger.warning("[DailyFileStore] overwrite on_write 回调失败: %s", e)

    def read_today(self) -> str:
        file_path = self._get_file()
        if not file_path.exists():
            return ""
        return file_path.read_text(encoding="utf-8")
