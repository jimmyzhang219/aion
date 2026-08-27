"""core/context.py 测试 — ContextVar set/get/reset 行为验证。"""

from pathlib import Path

import pytest

from aion.core.context import current_workspace, current_agent_id


class TestCurrentWorkspace:
    def test_default_raises_lookup_error(self):
        """未设置时 get() 应抛出 LookupError。"""
        # 创建一个新的 ContextVar 来避免全局污染
        import contextvars

        cv = contextvars.ContextVar("test")
        with pytest.raises(LookupError):
            cv.get()

    def test_set_and_get(self):
        token = current_workspace.set(Path("/tmp/test-ws"))
        try:
            assert current_workspace.get() == Path("/tmp/test-ws")
        finally:
            current_workspace.reset(token)

    def test_reset_restores_previous(self):
        token1 = current_workspace.set(Path("/ws1"))
        token2 = current_workspace.set(Path("/ws2"))
        assert current_workspace.get() == Path("/ws2")
        current_workspace.reset(token2)
        assert current_workspace.get() == Path("/ws1")
        current_workspace.reset(token1)

    def test_async_task_isolation(self):
        """asyncio Task 之间 ContextVar 独立。"""
        import asyncio

        results = []

        async def task_ws1():
            token = current_workspace.set(Path("/task1"))
            try:
                await asyncio.sleep(0.01)
                results.append(("task1", current_workspace.get()))
            finally:
                current_workspace.reset(token)

        async def task_ws2():
            token = current_workspace.set(Path("/task2"))
            try:
                await asyncio.sleep(0.02)
                results.append(("task2", current_workspace.get()))
            finally:
                current_workspace.reset(token)

        async def run():
            await asyncio.gather(task_ws1(), task_ws2())

        asyncio.run(run())
        assert ("task1", Path("/task1")) in results
        assert ("task2", Path("/task2")) in results


class TestCurrentAgentId:
    def test_default_value(self):
        """current_agent_id 有默认值 'main'。"""
        assert current_agent_id.get() == "main", "默认 agent_id 应为 main"

    def test_set_and_get(self):
        token = current_agent_id.set("agent-x")
        try:
            assert current_agent_id.get() == "agent-x"
        finally:
            current_agent_id.reset(token)
