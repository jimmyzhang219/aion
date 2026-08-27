"""MCP 单元测试 + 集成测试

测试 MCPServerManager 的基础行为，以及与测试用 MCP Server 的集成。
"""

import asyncio
import sys
import time
from pathlib import Path
from typing import AsyncGenerator

import httpx
import pytest
import pytest_asyncio

from aion.mcp.manager import MCPServerManager
from aion.mcp import initialize_mcp_servers, close_workspace_mcp
from aion.config.schema import Config

# ── 单元测试 ──


class TestMCPServerManager:
    """MCPServerManager 基础行为测试"""

    def test_initial_state(self):
        """新建实例未初始化，无工具"""
        manager = MCPServerManager()
        assert manager.get_langchain_tools() == []
        assert manager.is_initialized() is False

    @pytest.mark.asyncio
    async def test_initialize_empty_config(self):
        """空配置不报错"""
        manager = MCPServerManager()
        await manager.initialize([])
        assert manager.get_langchain_tools() == []

    @pytest.mark.asyncio
    async def test_close_all_clears_state(self):
        """close_all 后工具列表清空"""
        manager = MCPServerManager()
        await manager.initialize([])
        await manager.close_all()
        assert manager.get_langchain_tools() == []


# ── 集成测试 ──

TEST_SERVER_PORT = 18910
_TEST_SERVER_URL = f"http://localhost:{TEST_SERVER_PORT}/mcp"


@pytest_asyncio.fixture(scope="module")
async def mcp_test_server() -> AsyncGenerator[str, None]:
    """启动测试用 MCP server（Streamable HTTP 模式），返回 URL。"""
    server_script = str(Path(__file__).parent / "mcp_test_server.py")
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        server_script,
        "--port",
        str(TEST_SERVER_PORT),
        "--transport",
        "streamable-http",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # 等待 server 就绪
    async def wait_for_server(timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        async with httpx.AsyncClient() as client:
            while time.monotonic() < deadline:
                try:
                    await client.get(f"http://localhost:{TEST_SERVER_PORT}/")
                    # 任何响应（包括 404）都表示 server 已启动
                    return True
                except (httpx.ConnectError, httpx.RemoteProtocolError):
                    await asyncio.sleep(0.3)
        return False

    ready = await wait_for_server()
    if not ready:
        proc.kill()
        pytest.fail("MCP test server did not start in time")

    yield _TEST_SERVER_URL

    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()


@pytest.mark.asyncio
async def test_connect_and_list_tools(mcp_test_server: str):
    """通过 MCPServerManager 连接测试 server 并列举工具"""
    manager = MCPServerManager()
    config = [
        {
            "name": "test-server",
            "url": mcp_test_server,
        }
    ]
    await manager.initialize(config)
    tools = manager.get_langchain_tools()
    tool_names = [t.name for t in tools]
    assert "test-server_echo" in tool_names, f"Expected echo, got {tool_names}"
    assert "test-server_add" in tool_names, f"Expected add, got {tool_names}"


@pytest.mark.asyncio
async def test_call_tool(mcp_test_server: str):
    """通过 StructuredTool 调用 echo 工具"""
    manager = MCPServerManager()
    config = [
        {
            "name": "test-server",
            "url": mcp_test_server,
        }
    ]
    await manager.initialize(config)
    tools = manager.get_langchain_tools()

    echo_tool = next(t for t in tools if t.name == "test-server_echo")
    result = await echo_tool.ainvoke({"text": "hello"})
    assert "echo: hello" in str(result)


# ── Workspace 缓存测试 ──


class TestInitializeMCPServers:
    """initialize_mcp_servers 的 workspace 级缓存测试"""

    def test_no_servers_returns_empty_tools(self):
        """servers 列表为空时返回空 tools"""
        import asyncio

        result = asyncio.run(initialize_mcp_servers([], workspace_key=Path("/tmp/ws-a")))
        assert "tools" in result
        assert result["tools"] == []

    def test_different_workspace_keys_different_manager(self):
        """不同 workspace_key 应产生不同的 MCPServerManager 实例"""
        from aion.mcp import _mcp_instances

        _mcp_instances.clear()
        import asyncio

        asyncio.run(initialize_mcp_servers([], workspace_key=Path("/tmp/ws-a")))
        asyncio.run(initialize_mcp_servers([], workspace_key=Path("/tmp/ws-b")))

        assert Path("/tmp/ws-a") in _mcp_instances
        assert Path("/tmp/ws-b") in _mcp_instances
        assert _mcp_instances[Path("/tmp/ws-a")] is not _mcp_instances[Path("/tmp/ws-b")]

    def test_same_workspace_key_reuses_cache(self):
        """相同 workspace_key 的第二次调用应复用缓存的 Manager"""
        from aion.mcp import _mcp_instances

        _mcp_instances.clear()
        import asyncio

        asyncio.run(initialize_mcp_servers([], workspace_key=Path("/tmp/ws-cache")))
        first = _mcp_instances.get(Path("/tmp/ws-cache"))

        asyncio.run(initialize_mcp_servers([], workspace_key=Path("/tmp/ws-cache")))
        second = _mcp_instances.get(Path("/tmp/ws-cache"))

        assert first is second

    def test_default_key_fallback(self):
        """workspace_key 为 None 时使用 Path('default') 降级"""
        from aion.mcp import _mcp_instances

        _mcp_instances.clear()
        import asyncio

        asyncio.run(initialize_mcp_servers([]))
        assert Path("default") in _mcp_instances


class TestCloseWorkspaceMCP:
    """close_workspace_mcp 清理行为测试"""

    def test_close_unknown_workspace_no_error(self):
        """关闭不存在的 workspace 不应抛异常"""
        close_workspace_mcp(Path("/tmp/does-not-exist"))

    def test_close_removes_from_cache(self):
        """关闭后对应的 workspace 应从缓存移除"""
        from aion.mcp import _mcp_instances

        _mcp_instances.clear()
        import asyncio

        asyncio.run(initialize_mcp_servers([], workspace_key=Path("/tmp/ws-close")))
        assert Path("/tmp/ws-close") in _mcp_instances

        close_workspace_mcp(Path("/tmp/ws-close"))
        assert Path("/tmp/ws-close") not in _mcp_instances


# ── 持久 Session + Sampling 测试 ──


@pytest.mark.asyncio
async def test_persistent_session(mcp_test_server: str):
    """验证持久 session 能够跨工具调用复用"""
    manager = MCPServerManager()
    config = [{"name": "test-server", "url": mcp_test_server}]
    await manager.initialize(config)

    assert "test-server" in manager._sessions
    assert manager._sessions["test-server"] is not None

    tools = manager.get_langchain_tools()
    echo_tool = next(t for t in tools if t.name == "test-server_echo")
    result = await echo_tool.ainvoke({"text": "persistent"})
    assert "echo: persistent" in str(result)

    await manager.close_all()


def test_server_names():
    """验证 server_names 返回已注册名称"""
    manager = MCPServerManager()
    assert manager.server_names() == []


# ── 完整链路测试：schema 解析 → MCPServerManager → 工具调用 ──


@pytest.mark.asyncio
async def test_mcp_config_to_tool_call(mcp_test_server: str):
    """验证新格式 mcpServers 配置经 Pydantic schema 解析后，
    能正确连接 MCP Server 并调用工具获取结果。"""
    # 1. 构造新格式配置 dict（模拟 aion.json 中的 mcpServers）
    raw = {
        "models": {"test": {"model": "test", "apiKey": "sk-test"}},
        "workspaces": {
            "scopes": [
                {
                    "test-ws": {
                        "agents": {"leader": "main", "main": {"provider": "test", "fallback": []}},
                        "mcpServers": {
                            "math-srv": {
                                "url": mcp_test_server,
                                "transport": "streamable-http",
                            },
                        },
                    }
                }
            ],
            "current": "test-ws",
        },
        "log_level": "info",
    }

    # 2. Pydantic schema 解析（真实代码路径）
    config = Config.model_validate(raw)

    # 3. 获取工作空间配置中的 MCPServerConfig
    ws_config = config.get_workspace("test-ws")
    assert ws_config is not None
    assert "math-srv" in ws_config.mcp_servers
    srv = ws_config.mcp_servers["math-srv"]
    assert srv.url == mcp_test_server
    assert srv.transport == "streamable-http"

    # 4. 按 factory.py 的方式转换为 list[dict]
    servers_list = [
        {"name": name, "command": cfg.command, "args": cfg.args, "url": cfg.url, "transport": cfg.transport}
        for name, cfg in ws_config.mcp_servers.items()
    ]

    # 5. 初始化 MCPServerManager（真实代码路径）
    manager = MCPServerManager()
    await manager.initialize(servers_list)

    # 6. 获取工具列表
    tools = manager.get_langchain_tools()
    tool_names = [t.name for t in tools]
    assert "math-srv_echo" in tool_names, f"echo tool not found, got {tool_names}"
    assert "math-srv_add" in tool_names, f"add tool not found, got {tool_names}"

    # 7. 调用工具并验证结果
    echo_tool = next(t for t in tools if t.name == "math-srv_echo")
    result = await echo_tool.ainvoke({"text": "mcp format test"})
    assert "echo: mcp format test" in str(result), f"Unexpected echo result: {result}"

    add_tool = next(t for t in tools if t.name == "math-srv_add")
    result = await add_tool.ainvoke({"a": 123, "b": 456})
    assert "579" in str(result), f"Unexpected add result: {result}"

    # 8. 清理
    await manager.close_all()
