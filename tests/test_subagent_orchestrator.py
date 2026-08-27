"""SubagentOrchestrator 单元测试"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from aion.agent.subagent_orchestrator import SubagentOrchestrator


class TestSubagentOrchestrator:
    def test_init_not_subagent(self):
        orch = SubagentOrchestrator(session_id="main-session", is_subagent=False)
        assert orch.session_id == "main-session"
        assert not orch.is_subagent
        assert not orch.has_pending()

    def test_init_subagent(self):
        orch = SubagentOrchestrator(session_id="sub-1", is_subagent=True)
        assert orch.is_subagent
        assert not orch.has_pending()

    def test_push_and_has_pending(self):
        orch = SubagentOrchestrator(session_id="main", is_subagent=False)
        orch.push_result("sub-1", "result content")
        assert orch.has_pending()

    def test_drain_pending(self):
        orch = SubagentOrchestrator(session_id="main", is_subagent=False)
        orch.push_result("sub-1", "result 1")
        orch.push_result("sub-2", "result 2")
        results = orch.drain_pending()
        assert len(results) == 2
        assert ("sub-1", "result 1") in results
        assert ("sub-2", "result 2") in results
        assert not orch.has_pending()

    def test_drain_pending_empty(self):
        orch = SubagentOrchestrator(session_id="main", is_subagent=False)
        assert orch.drain_pending() == []
        assert not orch.has_pending()

    @pytest.mark.asyncio
    async def test_wait_for_active_with_event(self):
        import asyncio

        orch = SubagentOrchestrator(session_id="main", is_subagent=False)
        mock_registry = MagicMock()
        mock_registry.list_active_by_parent.return_value = [MagicMock()]

        async def trigger():
            await asyncio.sleep(0.05)
            orch.push_result("sub-1", "done")
            return True

        async def wait():
            results = await orch.wait_for_active(mock_registry, timeout=2.0)
            return results

        _, results = await asyncio.gather(trigger(), wait())
        assert len(results) == 1
        assert results[0] == ("sub-1", "done")

    def test_result_event_set_on_push(self):

        orch = SubagentOrchestrator(session_id="main", is_subagent=False)
        assert not orch._result_event.is_set()
        orch.push_result("sub-1", "hi")
        assert orch._result_event.is_set()
