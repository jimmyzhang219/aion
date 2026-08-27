"""子 Agent（Subagent）注册表、提示词与工具 Schema 单元测试

覆盖 SubagentRegistry 的注册/并发/深度限制、
build_subagent_system_prompt 文案，以及 sessions_spawn / subagents 工具 schema。
"""

import sys
from pathlib import Path

# 将项目 src 加入导入路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


def test_registry_register():
    """register 应创建 running 状态记录

    Returns:
        None
    """
    from aion.agent.subagent.registry import SubagentRegistry

    reg = SubagentRegistry(max_concurrent=5, max_depth=3)
    rec = reg.register(
        session_id="sub-1",
        parent_session_id="main",
        agent_id="worker",
        task="分析目录",
        depth=1,
    )
    assert rec.session_id == "sub-1"
    assert rec.parent_session_id == "main"
    assert rec.status == "running"


def test_registry_list():
    """list_by_parent 应返回同一父会话下的所有子任务

    Returns:
        None
    """
    from aion.agent.subagent.registry import SubagentRegistry

    reg = SubagentRegistry(max_concurrent=5, max_depth=3)
    reg.register("sub-1", "main", "worker", "task1", depth=1)
    reg.register("sub-2", "main", "worker", "task2", depth=1)
    entries = reg.list_by_parent("main")
    assert len(entries) == 2


def test_registry_list_active():
    """kill 后 list_active 不应再包含该会话

    Returns:
        None
    """
    from aion.agent.subagent.registry import SubagentRegistry

    reg = SubagentRegistry(max_concurrent=5, max_depth=3)
    reg.register("sub-1", "main", "worker", "task1", depth=1)
    active = reg.list_active()
    assert len(active) == 1
    reg.kill("sub-1")
    active = reg.list_active()
    assert len(active) == 0


def test_registry_kill():
    """kill 应将状态置为 killed 并返回 True

    Returns:
        None
    """
    from aion.agent.subagent.registry import SubagentRegistry

    reg = SubagentRegistry(max_concurrent=5, max_depth=3)
    reg.register("sub-1", "main", "worker", "task1", depth=1)
    result = reg.kill("sub-1")
    assert result is True
    entry = reg.get("sub-1")
    assert entry.status == "killed"


def test_registry_kill_nonexistent():
    """kill 不存在的 session_id 应返回 False

    Returns:
        None
    """
    from aion.agent.subagent.registry import SubagentRegistry

    reg = SubagentRegistry(max_concurrent=5, max_depth=3)
    result = reg.kill("nonexistent")
    assert result is False


def test_registry_duplicate_session_id():
    """重复 session_id 注册应抛出 ValueError

    Returns:
        None
    """
    from aion.agent.subagent.registry import SubagentRegistry

    reg = SubagentRegistry(max_concurrent=5, max_depth=3)
    reg.register("sub-1", "main", "worker", "task1", depth=1)
    with pytest.raises(ValueError, match="已存在"):
        reg.register("sub-1", "main", "worker", "task2", depth=1)


def test_registry_max_concurrent():
    """超过 max_concurrent 时再次 register 应失败

    Returns:
        None
    """
    from aion.agent.subagent.registry import SubagentRegistry

    reg = SubagentRegistry(max_concurrent=2, max_depth=3)
    reg.register("sub-1", "main", "worker", "t1", depth=1)
    reg.register("sub-2", "main", "worker", "t2", depth=1)
    with pytest.raises(ValueError, match="已达最大并发"):
        reg.register("sub-3", "main", "worker", "t3", depth=1)


def test_registry_max_depth_exceeded():
    """depth 达到 max_depth 时 check_can_spawn 应拒绝

    Returns:
        None
    """
    from aion.agent.subagent.registry import SubagentRegistry

    reg = SubagentRegistry(max_concurrent=5, max_depth=3)
    with pytest.raises(ValueError, match="达到最大 spawn 深度"):
        reg.check_can_spawn(depth=3)


def test_registry_complete():
    """complete 应将状态置为 completed 并保存 result

    Returns:
        None
    """
    from aion.agent.subagent.registry import SubagentRegistry

    reg = SubagentRegistry(max_concurrent=5, max_depth=3)
    reg.register("sub-1", "main", "worker", "task1", depth=1)
    reg.complete("sub-1", "result: ok")
    entry = reg.get("sub-1")
    assert entry.status == "completed"
    assert entry.result == "result: ok"


def test_build_subagent_system_prompt():
    """系统提示应包含任务描述与子 agent 说明关键字

    Returns:
        None
    """
    from aion.agent.subagent.prompt import build_subagent_system_prompt

    prompt = build_subagent_system_prompt(
        task="分析/src目录",
        child_session_id="sub-1",
        parent_session_id="main-session",
    )
    assert "分析/src目录" in prompt
    assert "子 agent" in prompt
    assert "结果自动返回" in prompt


def test_build_subagent_prompt_leaf():
    """子 agent 提示中不应出现 sessions_spawn 等派生关键字

    Returns:
        None
    """
    from aion.agent.subagent.prompt import build_subagent_system_prompt

    prompt = build_subagent_system_prompt(
        task="读取单个文件",
        child_session_id="leaf-1",
        parent_session_id="orch-session",
    )
    assert "读取单个文件" in prompt
    assert "子 agent" in prompt
    assert "sessions_spawn" not in prompt
    assert "Subagent 派生" not in prompt
    assert "进一步派生子 agent" not in prompt


def test_sessions_spawn_tool_schema():
    """sessions_spawn 的 schema 应要求 task 字段（从 agent-tools.py 检查）"""
    from aion.tools.builtin.agent_tools import sessions_spawn  # type: ignore[import]

    args = sessions_spawn.get_input_schema().schema()
    props = args.get("properties", {})
    assert "task" in props


def test_subagents_tool_schema():
    """SUBAGENTS_SCHEMA 应包含 action 属性

    Returns:
        None
    """
    from aion.agent.subagent.tools import SUBAGENTS_SCHEMA

    props = SUBAGENTS_SCHEMA["input_schema"]["properties"]
    assert "action" in props


def test_global_registry_singleton():
    """get_global_registry 应返回同一单例实例

    Returns:
        None
    """
    from aion.agent.subagent.registry import get_global_registry

    # reset_global_registry 已注释（生产无调用方）；直接重新创建全局实例
    import aion.agent.subagent.registry as _r

    _r._global_registry = None
    reg1 = get_global_registry()
    reg2 = get_global_registry()
    assert reg1 is reg2
