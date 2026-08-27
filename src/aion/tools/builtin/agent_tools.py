"""Agent 专用工具：subagent 派生等。"""

from __future__ import annotations

import logging

from langchain_core.tools import tool

from aion.tools._context import get_agent_loop

logger = logging.getLogger(__name__)


@tool
async def sessions_spawn(
    task: str,
    agent_id: str | None = None,
) -> str:
    """创建临时 subagent 以执行隔离的子任务。

    创建一个独立的临时 subagent 会话来执行指定的任务。
    该 subagent 拥有自己的会话文件和工具集。
    任务完成后返回结果。subagent 无法再创建其他 subagent。

    参数：
    task：子任务的详细描述
    agent_id：可选的目标代理ID（若未指定则使用父代理的代理）
    """
    parent = get_agent_loop()
    if parent is None:
        return "Error: sessions_spawn requires an active agent loop"
    return await parent.execute_subagent(  # type: ignore[attr-defined]
        task=task,
        agent_id=agent_id,
    )


# 标记：subagent 的 ToolRegistry 过滤此工具
# 使用 object.__setattr__ 绕过 Pydantic v2 的字段校验
object.__setattr__(sessions_spawn, "main_agent_only", True)


@tool
async def sessions_send(
    task: str,
    agent_id: str,
) -> str:
    """将任务发送给 member agent 执行。

    member agent 独立执行任务并返回结果。
    与 sessions_spawn 不同，sessions_send 目标是具有独立身份和内存的持久性 Agent Teams。

    参数：
    task：要分配的任务详细描述
    agent_id：目标成员代理名称
    """
    parent = get_agent_loop()
    if parent is None:
        return "Error: sessions_send requires an active agent loop"
    return await parent.execute_agent_send(  # type: ignore[attr-defined]
        agent_id=agent_id,
        task=task,
    )


object.__setattr__(sessions_send, "main_agent_only", True)
