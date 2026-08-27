"""工具注册 — TOOL_REGISTRY + bootstrap/subagent/MCP 工具合并。

所有内置工具由 _toolkit.py 的 @tool 自动发现机制收集到 TOOL_REGISTRY。
ToolRegistry 封装了 bootstrap 覆盖（trash/delete 的安全检查）以及
subagent/MCP 工具的注册。
"""

import inspect
import logging
from pathlib import Path

from typing import Callable

from langchain_core.tools import BaseTool, StructuredTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """工具注册 — 管理 bootstrap 包装、subagent 工具、MCP 工具。

    内置工具来自 TOOL_REGISTRY（@tool 自动发现），
    额外工具（bootstrap 覆盖 / subagent / MCP）存在 _extra_tools 中。
    """

    def __init__(self, agent_loop=None, is_subagent: bool = False):
        self._agent_loop = agent_loop
        self._is_subagent = is_subagent
        self._extra_tools: dict[str, Callable] = {}
        self._mcp_structured_tools: list[BaseTool] = []

    def register_all(self) -> dict[str, Callable]:
        """注册子工具 + bootstrap 覆盖，返回 {name: callable}。"""
        self._wrap_bootstrap_tools()
        self._register_subagent_tools()
        return self.get_all_tools()

    def get_all_tools(self) -> dict[str, Callable]:
        """返回 TOOL_REGISTRY + 额外工具合并字典（额外工具优先）。"""
        from ..tools._toolkit import TOOL_REGISTRY

        result: dict[str, Callable] = {}
        for name, raw in TOOL_REGISTRY.items():
            if raw.func is not None:
                result[name] = raw.func
        result.update(self._extra_tools)
        return result

    def build_langchain_tools(self) -> list[BaseTool]:
        """构建 LangChain BaseTool 列表。"""
        from ..tools._toolkit import TOOL_REGISTRY

        tools: list[BaseTool] = []
        # 内置工具 — @tool 本身就是完整实现
        for name, raw in TOOL_REGISTRY.items():
            if self._is_subagent and getattr(raw, "main_agent_only", False):
                continue
            func = self._extra_tools.get(name) or raw.func or raw.coroutine
            if func is None:
                continue
            tools.append(self._to_structured(name, func, raw))
        # 额外工具（subagent / MCP）— 动态构建
        for name, func in self._extra_tools.items():
            if name not in TOOL_REGISTRY:
                tools.append(self._to_dynamic_structured(name, func))
        # MCP 工具（已是 StructuredTool）
        tools.extend(self._mcp_structured_tools)
        return tools

    def _to_structured(self, name: str, func: Callable, raw: StructuredTool) -> StructuredTool:
        """基于 TOOL_REGISTRY 元数据包装工具。"""
        is_async = inspect.iscoroutinefunction(func)
        return StructuredTool.from_function(
            func=func if not is_async else None,
            name=name,
            description=raw.description,
            args_schema=raw.args_schema,
            coroutine=func if is_async else None,
        )

    def _to_dynamic_structured(self, name: str, func: Callable) -> StructuredTool:
        """为无 @tool schema 的额外工具（subagent/MCP）动态构建。"""
        is_async = inspect.iscoroutinefunction(func)
        doc = (getattr(func, "__doc__", "") or "").strip()
        desc = doc.split("\n")[0].strip() if doc else name
        return StructuredTool.from_function(
            func=func if not is_async else None,
            name=name,
            description=desc,
            coroutine=func if is_async else None,
        )

    def register_mcp_structured_tools(self, tools: list[BaseTool]) -> None:
        """注册预构建的 MCP StructuredTool（不经过 callable → StructuredTool 二次包装）。"""
        self._mcp_structured_tools = tools

    def register_mcp_tools(self, tools_dict: dict) -> None:
        """注册 MCP 工具到额外工具字典。"""
        self._extra_tools.update(tools_dict)

    def _wrap_bootstrap_tools(self) -> None:
        """用 bootstrap 校验闭包替换 trash/delete。"""
        from ..tools.builtin.trash import trash as _trash_tool
        from ..tools.builtin.delete import delete as _delete_tool
        from ..core.constants import is_bootstrap_ritual_filename
        from ..agent.bootstrap import validate_bootstrap_delete_allowed

        _trash_raw = _trash_tool.func  # type: ignore[attr-defined]
        _delete_raw = _delete_tool.func  # type: ignore[attr-defined]

        def _trash_bootstrap(path: str) -> str:
            resolved = Path(path).expanduser().resolve()
            if is_bootstrap_ritual_filename(resolved.name):
                ok, reason = validate_bootstrap_delete_allowed(resolved)
                if not ok:
                    return f"拒绝删除: {reason}"
            return _trash_raw(path)

        def _delete_bootstrap(path: str) -> str:
            resolved = Path(path).expanduser().resolve()
            if is_bootstrap_ritual_filename(resolved.name):
                ok, reason = validate_bootstrap_delete_allowed(resolved)
                if not ok:
                    return f"拒绝删除: {reason}"
            return _delete_raw(path)

        self._extra_tools["trash"] = _trash_bootstrap
        self._extra_tools["delete"] = _delete_bootstrap

    def _register_subagent_tools(self) -> None:
        """注册 Subagent 工具。"""
        try:
            from ..agent.subagent.tools import (
                create_agents_list_tool,
                create_subagents_tool,
            )
            from ..agent.subagent.registry import get_global_registry

            sub_registry = get_global_registry()
            agent_loop = self._agent_loop
            # sessions_spawn 已通过 auto-discovery 注册，不再需要手动添加
            self._extra_tools["agents_list"] = create_agents_list_tool(agent_loop)
            self._extra_tools["subagents"] = create_subagents_tool(sub_registry, agent_loop=agent_loop)
        except Exception as e:
            logger.warning("[Subagent] tool register failed: %s", e)
