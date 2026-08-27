"""
MCP 服务器管理器模块

管理持久 ClientSession 和 tools/list_changed 通知处理。
使用 AsyncExitStack 管理 transport + session 的生命周期。
"""

import asyncio
import logging
from contextlib import AsyncExitStack
from typing import Callable, Optional

import httpx
from langchain_core.tools import BaseTool
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from mcp.types import ToolListChangedNotification
from langchain_mcp_adapters.tools import load_mcp_tools

logger = logging.getLogger(__name__)

TOOL_NAME_PREFIX = True


class MCPServerManager:
    """MCP 服务器连接管理器 — 持久 session。

    为每个 MCP 服务器保持一个持久 ClientSession，
    支持工具列表缓存和 tools/list_changed 通知处理。
    """

    def __init__(self):
        self._connections: dict[str, dict] = {}
        self._sessions: dict[str, ClientSession] = {}
        self._session_tools: dict[str, list[BaseTool]] = {}
        self._tools_changed_callbacks: dict[str, Callable] = {}
        self._stack: Optional[AsyncExitStack] = None
        self._initialized: bool = False

    async def initialize(self, servers_config: list[dict]) -> None:
        if self._initialized:
            logger.warning("MCPServerManager already initialized, skipping")
            return

        self._stack = AsyncExitStack()

        for cfg in servers_config:
            name = cfg.get("name", "unknown")
            url = cfg.get("url")
            command = cfg.get("command")

            if url:
                # HTTP 模式（streamable-http）
                self._connections[name] = cfg
                # 连接前先做 TCP 可达性检测，避免 SDK 内部异常传播问题
                if not await self._check_tcp_reachable(url):
                    logger.warning("MCP '%s' unreachable (%s), skipping", name, url)
                    continue
                await self._connect_streamable_http(name)
            elif command:
                # stdio 模式
                self._connections[name] = cfg
                await self._connect_stdio(name)
            else:
                logger.warning("MCP '%s': no url or command, skipping", name)

        self._initialized = True

    def _create_notification_handler(self, server_name: str) -> Callable:
        """创建消息处理器，监听 tools/list_changed 通知。"""

        async def handler(message) -> None:
            if isinstance(message, ToolListChangedNotification):
                logger.info("MCP '%s': tools list changed, refreshing...", server_name)
                await self._refresh_tools(server_name)
                cb = self._tools_changed_callbacks.get(server_name)
                if cb:
                    await cb(server_name)

        return handler

    async def _refresh_tools(self, server_name: str) -> None:
        """重新拉取指定 server 的工具列表并更新缓存。"""
        session = self._sessions.get(server_name)
        if not session:
            logger.warning("MCP '%s': cannot refresh tools, session not found", server_name)
            return
        tools = await load_mcp_tools(
            session,
            server_name=server_name,
            tool_name_prefix=TOOL_NAME_PREFIX,
        )
        self._session_tools[server_name] = tools
        logger.info("MCP '%s' tools refreshed: %d tools", server_name, len(tools))

    async def _check_tcp_reachable(self, url: str) -> bool:
        """检查 HTTP MCP 服务器的 TCP 端口是否可达。

        轻量级 TCP 连接检测，使用 5 秒超时。
        避免在 server 未启动时进入 SDK 复杂的异常处理路径。
        """
        if not url.startswith(("http://", "https://")):
            return True  # URL 格式不支持检查，让后续连接自行处理
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5)
            return True
        except (OSError, asyncio.TimeoutError):
            return False

    async def _connect_streamable_http(self, name: str) -> None:
        assert self._stack is not None  # called from initialize()
        cfg = self._connections[name]
        url = cfg.get("url", "")

        client = create_mcp_http_client(timeout=httpx.Timeout(10))
        await self._stack.enter_async_context(client)

        read, write, _get_sid = await self._stack.enter_async_context(streamable_http_client(url, http_client=client))

        session = await self._stack.enter_async_context(
            ClientSession(read, write, message_handler=self._create_notification_handler(name))
        )
        # 设超时：transport 层吞掉异常后 send_request 会永远等响应
        await asyncio.wait_for(session.initialize(), timeout=10)
        self._sessions[name] = session

        tools = await load_mcp_tools(
            session,
            server_name=name,
            tool_name_prefix=TOOL_NAME_PREFIX,
        )
        self._session_tools[name] = tools
        logger.info("MCP '%s' streamable_http connected, %d tools", name, len(tools))

    async def _connect_stdio(self, name: str) -> None:
        assert self._stack is not None  # called from initialize()
        cfg = self._connections[name]
        command = cfg.get("command", "")
        args = cfg.get("args", [])

        server_params = StdioServerParameters(command=command, args=args)
        read, write = await self._stack.enter_async_context(stdio_client(server_params))

        session = await self._stack.enter_async_context(
            ClientSession(read, write, message_handler=self._create_notification_handler(name))
        )
        # 设超时：transport 层吞掉异常后 send_request 会永远等响应
        await asyncio.wait_for(session.initialize(), timeout=10)
        self._sessions[name] = session

        tools = await load_mcp_tools(
            session,
            server_name=name,
            tool_name_prefix=TOOL_NAME_PREFIX,
        )
        self._session_tools[name] = tools
        logger.info("MCP '%s' stdio connected, %d tools", name, len(tools))

    def get_langchain_tools(self) -> list[BaseTool]:
        result: list[BaseTool] = []
        for tools in self._session_tools.values():
            result.extend(tools)
        return result

    def set_on_tools_changed(self, server_name: str, callback: Callable) -> None:
        """注册工具变更回调。当 server 广播 tools/list_changed 时触发。"""
        self._tools_changed_callbacks[server_name] = callback

    def server_names(self) -> list[str]:
        return list(self._connections.keys())

    async def refresh_all_tools(self) -> None:
        """刷新所有已连接服务器的工具列表缓存。

        每次 AgentLoop 初始化时调用此方法，确保获取最新的工具列表，
        避免因异步 tools/list_changed 通知延迟而使用过时缓存。
        """
        for name in list(self._sessions.keys()):
            try:
                await self._refresh_tools(name)
            except Exception:
                logger.warning("MCP '%s': failed to refresh tools", name)

    async def close_all(self) -> None:
        if self._stack:
            await self._stack.aclose()
            self._stack = None
        self._sessions.clear()
        self._session_tools.clear()
        self._initialized = False

    def is_initialized(self) -> bool:
        return self._initialized
