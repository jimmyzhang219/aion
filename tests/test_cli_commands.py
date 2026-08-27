"""CLI 命令与模块级修复的单元测试

覆盖：
- status.py 中 runtime 作用域 bug（UnboundLocalError）
- factory.py 中 MCP 配置读取路径修复
- manager.py 单例残留检查
"""

from pathlib import Path
from unittest.mock import patch
import sys, ast

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from aion.cli.status import status


class TestStatusRuntimeScope:
    """status.py 中 runtime 变量作用域测试

    核心 bug：pids 为空时 runtime 未初始化，后续 if runtime: 引发 UnboundLocalError。
    """

    @patch("aion.cli.status.create_service_manager")
    @patch("aion.cli.status._query_gateway_status")
    def test_no_pids_no_crash(self, mock_query, mock_sm, capsys):
        """pids 为空时 status 不应抛异常"""
        mock_sm.return_value.find_pids.return_value = set()
        mock_query.return_value = None
        try:
            status.callback(port=19528)
        except UnboundLocalError:
            pytest.fail("status() raised UnboundLocalError when pids is empty")
        except (SystemExit, Exception):
            pass

    @patch("aion.cli.status.create_service_manager")
    @patch("aion.cli.status._query_gateway_status")
    def test_with_pids_runtime_none(self, mock_query, mock_sm, capsys):
        """pids 存在但 HTTP 请求失败时 runtime 为 None，不应抛异常"""
        mock_sm.return_value.find_pids.return_value = {12345}
        mock_query.return_value = None
        try:
            status.callback(port=19528)
        except UnboundLocalError:
            pytest.fail("status() raised UnboundLocalError when runtime is None")
        except (SystemExit, Exception):
            pass


class TestFactoryMCPConfigSource:
    """factory.py 的源代码级验证——MCP 配置读取路径修复"""

    SRC = Path(__file__).parent.parent / "src" / "aion" / "agent" / "factory.py"

    def test_no_get_mcp_servers_call(self):
        """factory.py 中不应再调用 config.get_mcp_servers()"""
        source = self.SRC.read_text()
        tree = ast.parse(source)

        finder = _GetMcpServersFinder()
        finder.visit(tree)
        assert len(finder.found) == 0, f"factory.py 仍调用 config.get_mcp_servers()，行号：{finder.found}"

    def test_uses_ws_config_mcp_servers(self):
        """factory.py 应引用 ws_config.mcp_servers"""
        source = self.SRC.read_text()
        # 两个函数（get_or_create_agent_loop / create_agent_loop）都应使用 ws_config
        assert source.count("ws_config.mcp_servers") >= 2, "factory.py 应有两处 ws_config.mcp_servers 引用"


class TestManagerSingletonSource:
    """manager.py 的源代码级验证——无单例残留"""

    SRC = Path(__file__).parent.parent / "src" / "aion" / "mcp" / "manager.py"

    def test_no_instance_class_var(self):
        """manager.py 中不应有 _instance 类变量"""
        source = self.SRC.read_text()
        assert "get_instance" not in source, "manager.py 仍包含 get_instance()"


class _GetMcpServersFinder(ast.NodeVisitor):
    """AST visitor：查找 config.get_mcp_servers() 调用"""

    def __init__(self):
        self.found: list[int] = []

    def visit_Call(self, node):
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Call)
            and isinstance(node.func.value.func, ast.Attribute)
            and node.func.value.func.attr == "get_mcp_servers"
        ):
            self.found.append(node.lineno)
        self.generic_visit(node)
