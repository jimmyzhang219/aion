"""子 Agent 结果协调 — SubagentOrchestrator

从 AgentLoop 中的 _pending_results、_result_event、push_subagent_result() 提取。
"""

import asyncio
from typing import Optional, Any


class SubagentOrchestrator:
    """子 agent 结果协调 — push/drain/wait。"""

    def __init__(self, session_id: str, is_subagent: bool = False):
        self.session_id = session_id
        self.is_subagent = is_subagent
        self._pending_results: list[tuple[str, str]] = []
        self._result_event = asyncio.Event()

    def push_result(self, session_id: str, result: str) -> None:
        """子 agent 完成时推送结果到队列。"""
        self._pending_results.append((session_id, result))
        self._result_event.set()

    def has_pending(self) -> bool:
        return bool(self._pending_results)

    def drain_pending(self) -> list[tuple[str, str]]:
        results = list(self._pending_results)
        self._pending_results.clear()
        return results

    async def wait_for_active(
        self,
        registry: Any,
        timeout: Optional[float] = None,
    ) -> list[tuple[str, str]]:
        """等待活跃子 agent 完成并返回结果。"""
        self._result_event.clear()
        await asyncio.wait_for(self._result_event.wait(), timeout=timeout)
        self._result_event.clear()
        return self.drain_pending()
