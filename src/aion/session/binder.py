"""Session Binding 机制

将 session_key（如 feishu:chat_id:thread_id:...）映射到 session_id（UUID）。
session_id 用于命名 transcript 文件，实现跨请求的持久化会话绑定。
绑定关系持久化到 workspace 根目录的 session_bindings.json。
"""

import json
import shutil
import uuid
from pathlib import Path
from typing import Optional


class SessionBinder:
    """会话绑定管理器（持久化到磁盘）。

    维护 session_key → session_id 的双向查找表，支持创建、刷新与解除绑定。
    """

    CURRENT_VERSION = 1  # 绑定文件 JSON schema 版本号（版本检查体为空，保留常量作文档参考）

    def __init__(self, workspace_dir: Path):
        """初始化绑定管理器并从磁盘加载已有绑定。

        Args:
            workspace_dir: workspace 根目录，绑定文件位于其下 session_bindings.json
        """
        self.workspace_dir = Path(workspace_dir)
        self.bindings_file = self.workspace_dir / "session_bindings.json"
        self._bindings: dict[str, str] = {}  # session_key -> session_id
        self._load()

    def _load(self) -> None:
        """从磁盘加载绑定关系，损坏时备份并清空内存表。

        Returns:
            None
        """
        if not self.bindings_file.exists():
            return

        try:
            with open(self.bindings_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            self._bindings = data.get("bindings", {})
        except (json.JSONDecodeError, IOError):
            # 备份损坏的文件，避免反复解析失败
            backup_file = self.bindings_file.with_suffix(".bak")
            if self.bindings_file.exists():
                shutil.copy2(self.bindings_file, backup_file)
            self._bindings = {}

    def _save(self) -> None:
        """原子写入绑定表到磁盘（先写 .tmp 再 rename）。

        Returns:
            None
        """
        data = {
            "version": self.CURRENT_VERSION,
            "bindings": self._bindings,
        }

        # 确保目录存在
        self.bindings_file.parent.mkdir(parents=True, exist_ok=True)

        # 写入临时文件
        tmp_file = self.bindings_file.with_suffix(".tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        # 原子替换，避免半写状态
        tmp_file.rename(self.bindings_file)

    def get_session_id(self, session_key: str) -> Optional[str]:
        """获取 session_key 当前绑定的 session_id。

        Args:
            session_key: 外部会话标识（如飞书 chat/thread 组合键）

        Returns:
            已绑定的 session_id；未绑定时返回 None
        """
        return self._bindings.get(session_key)

    def get_or_create_session_id(self, session_key: str) -> str:
        """获取已有 session_id，不存在则创建新 UUID 并持久化。

        Args:
            session_key: 外部会话标识

        Returns:
            绑定到该 key 的 session_id（新建或已有）
        """
        session_id = self._bindings.get(session_key)
        if session_id:
            return session_id

        # 创建新绑定
        session_id = str(uuid.uuid4())
        self._bindings[session_key] = session_id
        self._save()
        return session_id

    def refresh_binding(self, session_key: str) -> str:
        """为 session_key 生成新的 session_id（用于 /new 重置会话）。

        Args:
            session_key: 外部会话标识

        Returns:
            新生成的 session_id
        """
        new_session_id = str(uuid.uuid4())
        self._bindings[session_key] = new_session_id
        self._save()
        return new_session_id
