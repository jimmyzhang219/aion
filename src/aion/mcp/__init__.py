"""MCP (Model Context Protocol) Client 模块

基于 mcp SDK + langchain_mcp_adapters，支持 stdio 和 Streamable HTTP 传输。
MCP 是 workspace 级别隔离的：每个 workspace 有独立的
MCPServerManager 实例，由 _mcp_instances 按 workspace_key 缓存管理。

主要组件：
- MCPServerManager: MCP 服务器管理器（实例级，非单例）
"""

import logging
from pathlib import Path
from typing import Optional

from .manager import MCPServerManager

logger = logging.getLogger(__name__)


# 模块级缓存（workspace_key → {"manager": MCPServerManager, "servers_config": list[dict]}）
# 每个 workspace 有独立的 MCP 连接管理，避免全局单例导致跨 workspace 污染。
# servers_config 用于 diff 检测：配置变更时自动重建连接，无需重启 Gateway。
_mcp_instances: dict[Path, dict] = {}


async def initialize_mcp_servers(
    servers: list[dict],
    workspace_key: Optional[Path] = None,
) -> dict:
    """初始化（或获取缓存的）指定 workspace 的 MCP 服务器。

    支持配置变更检测：当 servers 列表与上次缓存不一致时，
    自动关闭旧连接并重新初始化，无需重启 Gateway 进程。

    Args:
        servers: MCP 服务器配置列表
        workspace_key: workspace 目录绝对路径

    Returns:
        {"tools": list[BaseTool], "manager": MCPServerManager}
    """
    key = workspace_key or Path("default")

    if key in _mcp_instances:
        cached = _mcp_instances[key]
        if cached.get("servers_config") != servers:
            logger.info("MCP config changed for workspace %s, reconnecting...", key)
            await cached["manager"].close_all()
            del _mcp_instances[key]
        else:
            # 返回缓存前刷新工具列表，确保获取最新状态
            # （tools/list_changed 可能因异步延迟尚未更新）
            await cached["manager"].refresh_all_tools()

    if key not in _mcp_instances:
        manager = MCPServerManager()
        if servers:
            await manager.initialize(servers)
        _mcp_instances[key] = {
            "manager": manager,
            "servers_config": list(servers) if servers else [],
        }

    return {
        "tools": _mcp_instances[key]["manager"].get_langchain_tools(),
        "manager": _mcp_instances[key]["manager"],
    }


def close_workspace_mcp(workspace_key: Path) -> None:
    """关闭指定 workspace 的 MCP 连接并清除缓存。

    用于 workspace 切换/删除时主动清理。
    调用方应确保该 workspace 没有正在处理的消息。

    Args:
        workspace_key: workspace 目录绝对路径
    """
    import asyncio

    entry = _mcp_instances.pop(workspace_key, None)
    if entry:
        manager = entry["manager"]
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(manager.close_all())
            else:
                asyncio.run(manager.close_all())
        except RuntimeError:
            asyncio.run(manager.close_all())


# 包对外公开的类型与类
__all__ = [
    "MCPServerManager",
    "initialize_mcp_servers",
    "close_workspace_mcp",
]
