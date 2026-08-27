"""Subagent 工具：subagents、agents_list

为 AgentLoop 注册子 agent 管理工具，供 LLM 管理并行执行中的子任务。
subagents 提供 list/kill/await 管理操作；agents_list 列出工作空间内可用 agent。
sessions_spawn 已迁移至 agent_tools.py 通过 auto-discovery 注册。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Optional

from .registry import SubagentRegistry

if TYPE_CHECKING:
    from ..loop import AgentLoop

logger = logging.getLogger(__name__)

SUBAGENTS_SCHEMA = {
    "name": "subagents",
    "description": "管理子 agent：列出、终止或等待子 agent 完成",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "kill", "await"],
                "description": "list=列出活跃, kill=终止, await=等待完成并返回结果",
            },
            "target": {
                "type": "string",
                "description": "kill/await 时目标 subagent 的 session_id（await 不指定则等待全部）",
            },
        },
        "required": ["action"],
    },
}

AGENTS_LIST_SCHEMA = {
    "name": "agents_list",
    "description": "列出当前工作空间可用的 agent（供 sessions_spawn 的 agent_id 参数参考）",
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


def create_agents_list_tool(agent_loop: AgentLoop):
    """创建 agents_list 工具函数。

    Args:
        agent_loop: 父 AgentLoop 实例

    Returns:
        callable() -> str
    """

    def agents_list() -> str:
        """列出工作空间内配置的所有 agent。"""
        try:
            from ...config.loader import load_config

            config = load_config()
            ws_name = agent_loop.workspace_dir.name
            ws_config = config.get_workspace(ws_name)
            if not ws_config:
                return "错误：未找到工作空间配置"

            agents = ws_config.agents
            leader = agents.get("leader", "main")

            lines = [f"工作空间: {ws_name}", f"Leader: {leader}", "", "可用 Agent:"]
            lines.append(f"  {'agent_id':<20} {'provider':<15} {'说明':<30}")
            lines.append(f"  {'-' * 20} {'-' * 15} {'-' * 30}")

            for key, value in agents.items():
                if key == "leader":
                    continue
                if isinstance(value, dict):
                    provider = value.get("provider", "?")
                    description = value.get("description", "")
                else:
                    provider = str(value)
                    description = ""
                marker = " ← leader" if key == leader else ""
                lines.append(f"  {key:<20} {provider:<15} {description + marker:<30}")

            return "\n".join(lines)
        except Exception as e:
            return f"错误：无法读取 agent 列表: {e}"

    return agents_list


def create_subagents_tool(
    registry: SubagentRegistry,
    agent_loop: Optional[AgentLoop] = None,
):
    """创建 subagents 管理工具函数。

    Args:
        registry: 子 agent 注册表
        agent_loop: 父 AgentLoop（await 操作需要，可选）

    Returns:
        callable(action, target=None) -> str
    """

    async def subagents(action: str, target: Optional[str] = None) -> str:
        """列出、终止或等待子 agent。

        Args:
            action: "list" | "kill" | "await"
            target: kill/await 时必填的 session_id（await 不指定则等全部）

        Returns:
            表格文本或操作结果说明
        """
        if action == "list":
            active = registry.list_active()
            if not active:
                return "(暂无活跃子 agent)"
            lines = [f"{'session_id':<25} {'agent':<15} {'task':<30} {'depth':<6}"]
            lines.append("-" * 80)
            for r in active:
                lines.append(f"{r.session_id:<25} {r.agent_id:<15} {r.task[:28]:<30} {r.depth:<6}")
            return "\n".join(lines)

        elif action == "kill":
            if not target:
                return "错误：kill 需要指定 target session_id"
            ok = registry.kill(target)
            return f"✓ 已终止子 agent: {target}" if ok else f"错误：session '{target}' 不存在"

        elif action == "await":
            if agent_loop is None:
                return "错误：await 操作需要绑定父 AgentLoop"

            return await await_subagent_results(
                parent_loop=agent_loop,
                registry=registry,
                target_session_id=target,
            )

        return f"错误：未知 action '{action}'"

    return subagents


async def await_subagent_results(
    parent_loop: AgentLoop,
    registry: Optional[SubagentRegistry] = None,
    target_session_id: Optional[str] = None,
) -> str:
    """等待子 agent 完成并返回注入到 context 的结果。

    阻塞直到指定子 agent（或所有活跃子 agent）完成，
    将结果收集并返回，供父 LLM 合成使用。

    Args:
        parent_loop: 父 AgentLoop 实例
        registry: SubagentRegistry 实例
        target_session_id: 等待特定子 agent（None 则等待全部）

    Returns:
        str: 子 agent 结果（多个结果用分隔符连接）
    """
    reg = registry
    if reg is None:
        from .registry import get_global_registry

        reg = get_global_registry()

    # 若指定了特定 session，等待它完成
    if target_session_id:
        record = reg.get(target_session_id)
        if not record:
            return f"错误：子 agent '{target_session_id}' 不存在"
        if record.status == "completed":
            return record.result or "(无结果)"
        if record.status == "killed":
            return f"子 agent '{target_session_id}' 已被终止"

        # 轮询等待完成
        while True:
            await asyncio.sleep(0.5)
            record = reg.get(target_session_id)
            if not record or record.status in ("completed", "killed"):
                break

        if record and record.status == "completed":
            return record.result or "(无结果)"
        return f"子 agent '{target_session_id}' 状态: {record.status if record else 'unknown'}"

    # 等待所有活跃子 agent 完成
    while True:
        active = reg.list_active_by_parent(parent_loop.session_id)
        if not active:
            break
        await asyncio.sleep(0.5)

    # 收集已完成的结果
    all_records = reg.list_by_parent(parent_loop.session_id)
    completed = [r for r in all_records if r.status == "completed" and r.result]
    if not completed:
        return "(无子 agent 结果)"

    parts = []
    for r in completed:
        parts.append(f"## Subagent: {r.session_id} ({r.agent_id})\n**Task**: {r.task[:200]}\n\n{r.result}")
    return "\n\n---\n\n".join(parts)
