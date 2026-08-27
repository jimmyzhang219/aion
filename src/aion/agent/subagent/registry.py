"""SubagentRegistry — 内存级子 agent 注册表

跟踪 sessions_spawn 派生的子 agent 状态，限制并发数与嵌套深度。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class SubagentRecord:
    """单个子 agent 的运行记录。

    Attributes:
        session_id: 子 agent 独立 session ID（transcript 文件名）
        parent_session_id: 派发方 session ID
        agent_id: 子 agent 使用的 Agent 配置 ID
        task: 派发任务描述（截断展示用）
        depth: 嵌套深度（父 depth + 1）
        status: running | completed | killed
        result: 完成后的最终文本；运行中为 None
        created_at: ISO 格式创建时间
    """

    session_id: str  # 子 agent 的 session ID
    parent_session_id: str  # 父 agent session ID
    agent_id: str  # 目标 agent ID
    task: str  # 派生任务描述
    depth: int  # 嵌套深度（0=main 的直接子 agent）
    status: str = "running"  # running | completed | killed
    result: Optional[str] = None  # 完成后的结果文本
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


class SubagentRegistry:
    """内存级子 agent 注册表。跟踪 spawn 状态、限制并发和深度。"""

    def __init__(self, max_concurrent: int = 5, max_depth: int = 3):
        """初始化注册表。

        Args:
            max_concurrent: 同一父 session 下最大并发子 agent 数
            max_depth: 最大嵌套 spawn 深度
        """
        self._records: dict[str, SubagentRecord] = {}
        self.max_concurrent = max_concurrent
        self.max_depth = max_depth

    def check_can_spawn(self, depth: int) -> None:
        """检查当前深度是否允许继续 spawn。

        Args:
            depth: 即将创建的子 agent 深度

        Raises:
            ValueError: 深度已达 max_depth

        Returns:
            None
        """
        if depth >= self.max_depth:
            raise ValueError(f"达到最大 spawn 深度 ({self.max_depth})")

    def register(
        self,
        session_id: str,
        parent_session_id: str,
        agent_id: str,
        task: str,
        depth: int,
    ) -> SubagentRecord:
        """注册新的子 agent 记录（含并发与深度校验）。

        Args:
            session_id: 子 session ID
            parent_session_id: 父 session ID
            agent_id: 目标 agent ID
            task: 任务描述
            depth: 嵌套深度

        Returns:
            新创建的 SubagentRecord

        Raises:
            ValueError: session_id 重复、深度或并发超限
        """
        self.check_can_spawn(depth)
        if session_id in self._records:
            raise ValueError(f"session_id '{session_id}' 已存在")
        active = [
            r for r in self._records.values() if r.parent_session_id == parent_session_id and r.status == "running"
        ]
        if len(active) >= self.max_concurrent:
            raise ValueError(f"已达最大并发子 agent 数 ({self.max_concurrent})")
        record = SubagentRecord(
            session_id=session_id,
            parent_session_id=parent_session_id,
            agent_id=agent_id,
            task=task,
            depth=depth,
        )
        self._records[session_id] = record
        return record

    def get(self, session_id: str) -> Optional[SubagentRecord]:
        """按 session_id 查询记录。

        Args:
            session_id: 子 agent session ID

        Returns:
            SubagentRecord 或 None
        """
        return self._records.get(session_id)

    def list_by_parent(self, parent_session_id: str) -> list[SubagentRecord]:
        """列出指定父 session 下的所有子 agent（含已完成）。

        Args:
            parent_session_id: 父 session ID

        Returns:
            SubagentRecord 列表
        """
        return [r for r in self._records.values() if r.parent_session_id == parent_session_id]

    def list_active(self) -> list[SubagentRecord]:
        """列出全局所有 status=running 的子 agent。

        Returns:
            运行中的 SubagentRecord 列表
        """
        return [r for r in self._records.values() if r.status == "running"]

    def list_active_by_parent(self, parent_session_id: str) -> list[SubagentRecord]:
        """列出指定父 session 下仍在运行的子 agent。

        Args:
            parent_session_id: 父 session ID

        Returns:
            运行中的 SubagentRecord 列表
        """
        return [r for r in self._records.values() if r.parent_session_id == parent_session_id and r.status == "running"]

    def kill(self, session_id: str) -> bool:
        """将子 agent 标记为 killed（不强制终止底层 asyncio 任务）。

        Args:
            session_id: 目标子 session ID

        Returns:
            True 表示记录存在并已更新；False 表示不存在
        """
        record = self._records.get(session_id)
        if not record:
            return False
        record.status = "killed"
        return True

    def complete(self, session_id: str, result: str) -> bool:
        """标记子 agent 已完成并保存结果。

        Args:
            session_id: 子 session ID
            result: 子 agent 最终输出

        Returns:
            True 表示记录存在并已更新；False 表示不存在
        """
        record = self._records.get(session_id)
        if not record:
            return False
        record.status = "completed"
        record.result = result
        return True


_global_registry: Optional[SubagentRegistry] = None  # 进程级单例


def get_global_registry() -> SubagentRegistry:
    """获取进程级 SubagentRegistry 单例（懒创建）。

    Returns:
        全局 SubagentRegistry 实例
    """
    global _global_registry
    if _global_registry is None:
        _global_registry = SubagentRegistry()
    return _global_registry


# def reset_global_registry() -> None:  # 未使用（仅测试中调用）
#     """重置全局注册表（主要用于测试）。"""
#     global _global_registry
#     _global_registry = None
