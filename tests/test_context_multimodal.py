"""Context 多模态支持单元测试"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class TestContextAddUser:
    """Context.add_user 多模态测试"""

    def test_add_user_str(self):
        """add_user 纯文本保持现有行为"""
        from aion.agent.context import Context

        ctx = Context()
        ctx.add_user("hello")
        assert ctx.messages == [{"role": "user", "content": "hello"}]

    def test_add_user_list(self):
        """add_user 接受 list[dict]"""
        from aion.agent.context import Context

        ctx = Context()
        content = [
            {"type": "text", "text": "看图"},
            {"type": "image", "data": "base64img", "mimeType": "image/jpeg"},
        ]
        ctx.add_user(content)
        assert len(ctx.messages) == 1
        assert ctx.messages[0]["role"] == "user"
        assert ctx.messages[0]["content"] == content

    def test_add_user_str_strips_thinking(self):
        """add_user str 仍处理 thinking 标签"""
        from aion.agent.context import Context

        ctx = Context()
        ctx.add_user("hello <thinking>test</thinking> world")
        # strip_thinking_tags 转换 <thinking> 为 [思考] 块
        assert "[思考]" in ctx.messages[0]["content"]
        assert "hello" in ctx.messages[0]["content"]
        assert "world" in ctx.messages[0]["content"]


class TestContextGetMessages:
    """Context.get_messages 多模态测试"""

    def test_get_messages_str(self):
        """get_messages 对 str content 仍处理 thinking 标签"""
        from aion.agent.context import Context

        ctx = Context()
        ctx.add_user("hello <thinking>test</thinking>")
        msgs = ctx.get_messages()
        # strip_thinking_tags 转换 <thinking> 为 [思考] 块
        assert "[思考]" in msgs[0]["content"]
        assert "hello" in msgs[0]["content"]

    def test_get_messages_list_preserved(self):
        """get_messages 对 list content 保持原样"""
        from aion.agent.context import Context

        ctx = Context()
        content = [
            {"type": "text", "text": "看图 <thinking>hidden</thinking>"},
            {"type": "image", "data": "img", "mimeType": "image/jpeg"},
        ]
        ctx.add_user(content)
        msgs = ctx.get_messages()
        assert msgs[0]["content"] == content  # 保持原样
