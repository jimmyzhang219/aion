"""agent_tools.py 单元测试"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_sessions_spawn_is_tool():
    """sessions_spawn 应是 StructuredTool 实例"""
    from langchain_core.tools import StructuredTool
    from aion.tools.builtin.agent_tools import sessions_spawn  # type: ignore[import]

    assert isinstance(sessions_spawn, StructuredTool)


def test_sessions_spawn_has_main_agent_only():
    """sessions_spawn 应有 main_agent_only=True 标记"""
    from aion.tools.builtin.agent_tools import sessions_spawn  # type: ignore[import]

    assert getattr(sessions_spawn, "main_agent_only", False) is True


def test_sessions_spawn_schema():
    """sessions_spawn 的 schema 应含 task 和 agent_id"""
    from aion.tools.builtin.agent_tools import sessions_spawn  # type: ignore[import]

    assert "task" in sessions_spawn.args
    assert "agent_id" in sessions_spawn.args
