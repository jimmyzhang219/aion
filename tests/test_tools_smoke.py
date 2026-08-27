"""工具冒烟测试 — 校验每个工具都能正常 import 和调用（无 RuntimeError / no ImportError）。"""

import pytest

from aion.core.context import current_workspace, current_agent_id


def test_no_runtime_error_stubs():
    """所有内置工具的 @tool 函数不应 raise RuntimeError。

    在 ContextVar 重构之前，部分工具靠 loader 闭包覆盖，
    其 @tool 函数体内只有 raise RuntimeError。本测试确保
    这个模式已全部清理。
    """
    from aion.tools._toolkit import TOOL_REGISTRY

    errors = []
    for name, tool in TOOL_REGISTRY.items():
        if tool.name == "process_document":
            continue  # process_document 需要 ChromaDB 初始化，不能简单调用
        # inspect 函数体源码，检查是否有 raise RuntimeError
        import inspect

        try:
            source = inspect.getsource(tool.func)
            if "raise RuntimeError" in source:
                errors.append(f"{name}: contains raise RuntimeError")
        except (OSError, TypeError):
            pass  # 部分函数可能无法获取源码

    assert not errors, "\n".join(errors)


def test_all_tools_importable():
    """TOOL_REGISTRY 中的所有工具可正常 import 且不是 RuntimeError stub。"""
    from aion.tools._toolkit import TOOL_REGISTRY

    for name, tool in TOOL_REGISTRY.items():
        assert tool.name == name
        assert tool.func is not None or tool.coroutine is not None
        assert tool.description, f"{name} has no description"
        # 验证函数不是 lambda（lambda 在 pickle/serialize 时有问题）
        import types

        fn = tool.func if tool.func is not None else tool.coroutine
        assert isinstance(fn, types.FunctionType), f"{name} is a lambda, not a real function"


class TestToolSmoke:
    """工具冒烟测试 — 在 ContextVar 环境下调用，验证不崩溃。"""

    @pytest.fixture(autouse=True)
    def setup_ctx(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "hello.txt").write_text("Hello World\nLine 2")
        (ws / "sub").mkdir()
        (ws / "sub" / "nested.py").write_text("x = 1")
        token_ws = current_workspace.set(ws)
        token_ag = current_agent_id.set("main")
        yield ws
        current_workspace.reset(token_ws)
        current_agent_id.reset(token_ag)

    def test_read_smoke(self):
        from aion.tools._toolkit import TOOL_REGISTRY

        r = TOOL_REGISTRY["read"].func("hello.txt")
        assert "Hello World" in r

    def test_read_absolute_path(self):
        from aion.tools._toolkit import TOOL_REGISTRY

        ws = current_workspace.get()
        r = TOOL_REGISTRY["read"].func(str(ws / "hello.txt"))
        assert "Hello World" in r

    def test_ls_default(self):
        from aion.tools._toolkit import TOOL_REGISTRY

        r = TOOL_REGISTRY["ls"].func(".")
        assert "hello.txt" in r

    def test_ls_relative(self):
        from aion.tools._toolkit import TOOL_REGISTRY

        r = TOOL_REGISTRY["ls"].func("sub")
        assert "nested.py" in r

    def test_write_smoke(self, setup_ctx):
        from aion.tools._toolkit import TOOL_REGISTRY

        ws = setup_ctx
        r = TOOL_REGISTRY["write"].func("new.txt", "content")
        assert "写入成功" in r
        assert (ws / "new.txt").read_text() == "content"

    def test_write_absolute(self, setup_ctx):
        from aion.tools._toolkit import TOOL_REGISTRY

        ws = setup_ctx
        target = ws / "abs.txt"
        r = TOOL_REGISTRY["write"].func(str(target), "abs")
        assert "写入成功" in r

    def test_edit_smoke(self, setup_ctx):
        from aion.tools._toolkit import TOOL_REGISTRY

        ws = setup_ctx
        r = TOOL_REGISTRY["edit"].func("hello.txt", [{"oldText": "Hello", "newText": "Hi"}])
        assert "已写入" in r or "Hi" in (ws / "hello.txt").read_text()

    def test_grep_smoke(self):
        from aion.tools._toolkit import TOOL_REGISTRY

        r = TOOL_REGISTRY["grep"].func("Hello")
        assert "hello.txt:1:" in r

    def test_find_smoke(self):
        from aion.tools._toolkit import TOOL_REGISTRY

        r = TOOL_REGISTRY["find"].func("**/*.py")
        assert "sub/nested.py" in r

    def test_trash_smoke(self, setup_ctx):
        from aion.tools._toolkit import TOOL_REGISTRY

        ws = setup_ctx
        (ws / "todel.txt").write_text("x")
        r = TOOL_REGISTRY["trash"].func("todel.txt")
        assert "移动" in r

    def test_delete_smoke(self, setup_ctx):
        from aion.tools._toolkit import TOOL_REGISTRY

        ws = setup_ctx
        (ws / "todel2.txt").write_text("x")
        r = TOOL_REGISTRY["delete"].func("todel2.txt")
        assert "删除" in r

    def test_apply_patch_smoke(self, setup_ctx):
        from aion.tools._toolkit import TOOL_REGISTRY

        ws = setup_ctx
        (ws / "patch_test.txt").write_text("old content\nsecond line")
        patch = (
            "--- a/patch_test.txt\n+++ b/patch_test.txt\n@@ -1,2 +1,2 @@\n-old content\n+new content\n second line\n"
        )
        r = TOOL_REGISTRY["apply_patch"].func(patch)
        assert "已写入" in r

    def test_exec_smoke(self, setup_ctx):
        from aion.tools._toolkit import TOOL_REGISTRY

        r = TOOL_REGISTRY["exec"].func("echo smoke_test", timeout=5)
        assert "smoke_test" in r

    def test_process_list_smoke(self):
        from aion.tools._toolkit import TOOL_REGISTRY

        r = TOOL_REGISTRY["process"].func("list")
        assert r is not None

    def test_web_fetch_smoke(self):
        from aion.tools._toolkit import TOOL_REGISTRY

        r = TOOL_REGISTRY["web_fetch"].func("https://example.com")
        assert "Example" in r or "example" in r.lower()

    def test_gateway_config_get_smoke(self):
        from aion.tools._toolkit import TOOL_REGISTRY

        r = TOOL_REGISTRY["gateway"].func("config.get")
        assert "ok" in r or "ok" in str(r).lower()

    def test_memory_write_smoke(self, setup_ctx):
        from aion.tools._toolkit import TOOL_REGISTRY

        r = TOOL_REGISTRY["memory_write"].func("test permanent memory")
        assert "已更新永久记忆" in r or "写入失败" in r

    def test_daily_memory_write_smoke(self, setup_ctx):
        from aion.tools._toolkit import TOOL_REGISTRY

        r = TOOL_REGISTRY["daily_memory_write"].func("test daily note")
        assert "已记录" in r or "写入失败" in r

    def test_memory_search_smoke(self):
        from aion.tools._toolkit import TOOL_REGISTRY

        r = TOOL_REGISTRY["memory_search"].func("test")
        # 搜索可能无结果，但不应该崩溃
        assert isinstance(r, str)

    def test_process_document_smoke(self, setup_ctx):
        from aion.tools._toolkit import TOOL_REGISTRY

        ws = setup_ctx
        (ws / "rag_doc.md").write_text("# Test document for RAG indexing")
        r = TOOL_REGISTRY["process_document"].func("rag_doc.md")
        assert "文档已索引" in r or "错误" in r
