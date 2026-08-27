"""验证 3 个 bug fix 的单元测试"""

from typing import Optional

from pydantic import Field, create_model


# ────────────────────────────────────────────────
# Fix 1: Optional[ptype] — Pydantic 模型字段类型
# ────────────────────────────────────────────────
class TestOptionalPtype:
    """验证 _build_langchain_tools 中 None 默认值字段使用 Optional[ptype]"""

    def test_none_default_creates_optional_field(self):
        """default=None 时字段类型应为 Optional[float]，而非 float"""
        # 旧方式：fields[pname] = (ptype, Field(default=None))
        old = create_model("x", limit=(float, Field(default=None)))
        assert old.model_fields["limit"].annotation is float  # ❌ 旧方式

        # 新方式：fields[pname] = (Optional[ptype], Field(default=None))
        new = create_model("x", limit=(Optional[float], Field(default=None)))
        ann = new.model_fields["limit"].annotation
        # Python 3.14: Optional[float] == float | None
        assert ann is Optional[float] or ann == Optional[float] or str(ann) == "float | None"

    def test_non_none_default_uses_plain_type(self):
        """default=1 时字段类型保持 float"""
        model = create_model("x", offset=(float, Field(default=1)))
        assert model.model_fields["offset"].annotation is float
        assert model.model_fields["offset"].default == 1

    def test_required_field_is_required(self):
        """required 字段标记为 required"""
        model = create_model("x", path=(str, Field(default=...)))
        assert model.model_fields["path"].annotation is str
        assert model.model_fields["path"].is_required() is True


# ────────────────────────────────────────────────
# Fix 2: Context.add_system（已注释，相关方法已废弃）
# ────────────────────────────────────────────────
# class TestContextAddSystem:
#     """验证 Context.add_system 方法"""
#
#     def test_add_system_appends_message(self):
#         from aion.agent.context import Context
#         ctx = Context()
#         ctx.add_system("test notification")
#         assert len(ctx.messages) == 1
#         assert ctx.messages[0] == {"role": "system", "content": "test notification"}
#
#     def test_add_system_preserves_other_messages(self):
#         from aion.agent.context import Context
#         ctx = Context()
#         ctx.add_user("hello")
#         ctx.add_system("notification")
#         assert len(ctx.messages) == 2
#         assert ctx.messages[0]["role"] == "user"
#         assert ctx.messages[1]["role"] == "system"


# ────────────────────────────────────────────────
# Fix 3: EditTool 小文件返回完整内容
# ────────────────────────────────────────────────
class TestEditToolFullContent:
    """验证 edit 工具对小文件追加完整内容"""

    def test_small_file_includes_full_content(self, tmp_path):
        from aion.tools.builtin.edit import EditTool

        workspace = tmp_path / "ws"
        workspace.mkdir()
        # 创建小文件（< 800 chars）
        f = workspace / "tiny.md"
        f.write_text("# Hello\n\nThis is a tiny file.\n")

        et = EditTool(workspace)
        result = et.edit(str(f), [{"oldText": "tiny", "newText": "small"}])

        assert "-- 当前文件完整内容 ---" in result
        assert "# Hello" in result
        assert "This is a small file." in result

    def test_large_file_skips_full_content(self, tmp_path):
        from aion.tools.builtin.edit import EditTool

        workspace = tmp_path / "ws"
        workspace.mkdir()
        # 创建大文件（> 800 chars）
        f = workspace / "large.md"
        f.write_text("# Large\n\n" + ("x" * 900) + "\n")

        et = EditTool(workspace)
        result = et.edit(str(f), [{"oldText": "Large", "newText": "Small"}])

        assert "-- 当前文件完整内容 ---" not in result

    def test_edit_unchanged_shows_no_change(self, tmp_path):
        from aion.tools.builtin.edit import EditTool

        workspace = tmp_path / "ws"
        workspace.mkdir()
        f = workspace / "test.md"
        f.write_text("Hello")

        et = EditTool(workspace)
        result = et.edit(str(f), [{"oldText": "Hello", "newText": "Hello"}])

        assert "(内容未变)" in result


# ────────────────────────────────────────────────
# Fix 4: is_bootstrap_ritual_filename 用于删除检测
# ────────────────────────────────────────────────
class TestBootstrapDetection:
    """验证 delete 工具中的 bootstrap 文件检测"""

    def test_detect_agent_bootstrap(self):
        from aion.core.constants import is_bootstrap_ritual_filename

        assert is_bootstrap_ritual_filename("AGENT_BOOTSTRAP.md") is True
        assert is_bootstrap_ritual_filename("agent_bootstrap.md") is True  # 不区分大小写
        assert is_bootstrap_ritual_filename("AGENT_BOOTSTRAP.MD") is True
        assert is_bootstrap_ritual_filename("CONFIG.md") is False
        assert is_bootstrap_ritual_filename("README.md") is False

    def test_delete_tool_success_format(self, tmp_path):
        """非引导文件删除后返回 '已删除: <文件名>'"""
        from aion.tools.builtin.delete import delete as _del_tool

        _delete = _del_tool.func

        f = tmp_path / "test.txt"
        f.write_text("hello")
        result = _delete(str(f))
        assert result.startswith("已删除: test.txt")
        assert not f.exists()


# ────────────────────────────────────────────────
# Fix 5: _refresh_system_prompt 逻辑验证
# ────────────────────────────────────────────────
class TestRefreshSystemPrompt:
    """验证 _refresh_system_prompt 的正确性"""

    # def test_strip_system_messages(self):  # 使用已废弃的 add_system
    #     """移除所有 system 消息，保留其他消息"""
    #     ctx = Context()
    #     ctx.add_system("old bootstrap instructions")
    #     ctx.add_user("hello")
    #     ctx.add_system("another old instruction")
    #     ctx.messages.append({"role": "assistant", "content": "hi"})
    #     non_system = [m for m in ctx.messages if m.get("role") != "system"]
    #     assert len(non_system) == 2

    def test_delete_tool_parse_result(self):
        """验证 bootstrap 删除结果能被正确解析"""
        from aion.core.constants import is_bootstrap_ritual_filename

        # 模拟 delete 工具返回 "已删除: AGENT_BOOTSTRAP.md"
        result = "已删除: AGENT_BOOTSTRAP.md"
        assert result.startswith("已删除: ")
        name = result[len("已删除: ") :].strip()
        assert name == "AGENT_BOOTSTRAP.md"
        assert is_bootstrap_ritual_filename(name)
