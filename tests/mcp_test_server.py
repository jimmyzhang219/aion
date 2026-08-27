"""MCP 测试用 Server（Streamable HTTP 传输）

启动测试用 MCP 服务器供集成测试使用。
用法：
    python tests/mcp_test_server.py
    或通过 subprocess 自动启动（集成测试中）。
"""

import sys
from pathlib import Path

# 确保 src 在导入路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mcp.server.fastmcp import FastMCP


def create_server(port: int = 8910) -> FastMCP:
    """创建并返回测试用 MCP 服务器实例。

    Args:
        port: HTTP 端口号（仅在 streamable_http 传输时生效）
    """
    mcp = FastMCP("test-server", port=port)

    @mcp.tool()
    def echo(text: str) -> str:
        """返回输入文本"""
        return f"echo: {text}"

    @mcp.tool()
    def add(a: int, b: int) -> int:
        """两数相加"""
        return a + b

    return mcp


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8910, help="HTTP 端口")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="streamable-http",
    )
    args = parser.parse_args()
    mcp = create_server(port=args.port)
    mcp.run(transport=args.transport)
