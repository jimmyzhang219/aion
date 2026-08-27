"""M3 Agent 循环（ReAct）单元测试

测试 Context 消息管理、AgentLoop 多轮对话、工具注册，
以及 Bootstrap 误导完成声明的二次审阅防护逻辑。
"""

import pytest
from pathlib import Path
import sys

# 将项目 src 加入导入路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aion.agent.context import Context
from aion.agent.loop import AgentLoop, _is_valid_response

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult, ChatGeneration


class TestIsValidResponse:
    """_is_valid_response 函数单元测试"""

    def test_valid_text_response(self):
        """有 content 的响应应被视为有效"""
        msg = AIMessage(content="hello")
        assert _is_valid_response(msg) is True

    def test_valid_tool_calls_response(self):
        """有 tool_calls 的响应应被视为有效"""
        msg = AIMessage(content="", tool_calls=[{"name": "test", "args": {}, "id": "1", "type": "tool_call"}])
        assert _is_valid_response(msg) is True

    def test_empty_response_invalid(self):
        """空 content 且无 tool_calls 应被视为无效"""
        msg = AIMessage(content="")
        assert _is_valid_response(msg) is False

    def test_whitespace_only_response_invalid(self):
        """仅有空白字符的 content 且无 tool_calls 应被视为无效"""
        msg = AIMessage(content="  \n  ")
        assert _is_valid_response(msg) is False

    def test_finish_reason_tool_calls_but_no_actual_calls_invalid(self):
        """finish_reason=tool_calls 但无实际 tool_calls 应被视为无效"""
        msg = AIMessage(content="some text")
        msg.response_metadata["finish_reason"] = "tool_calls"
        assert _is_valid_response(msg) is False


class MockChatModel(BaseChatModel):
    """用于测试的 Mock BaseChatModel。

    可传入多段 str：第 N 次调用使用第 N 段。
    """

    responses: list = ["Hello!"]
    call_count: int = 0
    last_messages: list | None = None
    model: str = "mock-model"

    def __init__(self, *responses, **kwargs):
        if not responses:
            responses = ["Hello!"]
        super().__init__(responses=list(responses), **kwargs)
        object.__setattr__(self, "call_count", 0)
        object.__setattr__(self, "last_messages", None)

    @property
    def _llm_type(self) -> str:
        return "mock"

    def _get_response(self) -> str:
        idx = min(self.call_count - 1, len(self.responses) - 1)
        return self.responses[idx]

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object = None,
        **kwargs: object,
    ) -> ChatResult:
        self.call_count += 1
        self.last_messages = messages
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self._get_response()))])

    def bind_tools(
        self,
        tools: list,
        *,
        tool_choice: str | None = None,
        **kwargs: object,
    ) -> BaseChatModel:
        """Mock bind_tools — 存储工具引用并返回自身。"""
        object.__setattr__(self, "bound_tools", tools)
        object.__setattr__(self, "tool_choice", tool_choice)
        return self


class TestContext:
    """Context 消息缓冲区测试"""

    def test_empty_context(self):
        """空上下文 get_messages 应返回空列表

        Returns:
            None
        """
        ctx = Context()
        messages = ctx.get_messages()
        assert messages == []

    def test_add_user(self):
        """add_user 应追加 role=user 的消息

        Returns:
            None
        """
        ctx = Context()
        ctx.add_user("Hello")
        messages = ctx.get_messages()
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello"

    def test_add_assistant(self):
        """add_assistant 应追加 role=assistant 的消息

        Returns:
            None
        """
        ctx = Context()
        ctx.add_assistant("Hi there!")
        messages = ctx.get_messages()
        assert len(messages) == 1
        assert messages[0]["role"] == "assistant"
        assert messages[0]["content"] == "Hi there!"

    def test_add_multiple_messages(self):
        """多条消息应按添加顺序保留角色序列

        Returns:
            None
        """
        ctx = Context()
        ctx.add_user("Hello")
        ctx.add_assistant("Hi!")
        ctx.add_user("How are you?")
        messages = ctx.get_messages()
        assert len(messages) == 3
        assert [m["role"] for m in messages] == ["user", "assistant", "user"]

    def test_get_messages_returns_copy(self):
        """get_messages 应返回副本，避免外部修改内部状态

        Returns:
            None
        """
        ctx = Context()
        ctx.add_user("Hello")
        messages1 = ctx.get_messages()
        messages2 = ctx.get_messages()
        assert messages1 is not messages2
        assert messages1 == messages2


class TestAgentLoop:
    """AgentLoop 主循环与 Bootstrap 防护测试"""

    @pytest.mark.asyncio
    async def test_simple_chat(self, tmp_path):
        """单轮用户输入应触发一次 LLM 并返回其文本

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        llm = MockChatModel("Hello! How can I help?")
        agent = AgentLoop(llm, workspace_dir=tmp_path, max_tool_rounds=50)
        result = await agent.run("Hi!")
        assert result == "Hello! How can I help?"
        assert llm.call_count == 1

    @pytest.mark.asyncio
    async def test_chat_with_context(self, tmp_path):
        """多轮 run 应累积上下文并多次调用 LLM

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        llm = MockChatModel("Nice to meet you!")
        agent = AgentLoop(llm, workspace_dir=tmp_path, max_tool_rounds=50)
        await agent.run("I'm Alice")
        response = await agent.run("What's my name?")
        assert response == "Nice to meet you!"
        # 应已调用 LLM 两次
        assert llm.call_count == 2

    @pytest.mark.asyncio
    async def test_tool_call_basic(self, tmp_path):
        """注册的工具应可通过 tools 字典取回同一可调用对象

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        llm = MockChatModel("No tool needed")
        agent = AgentLoop(llm, workspace_dir=tmp_path, max_tool_rounds=50)

        def echo_tool(arg):
            """测试用 echo 工具：回显参数

            Args:
                arg: 待回显的字符串

            Returns:
                str: 带 Echo 前缀的回显结果
            """
            return f"Echo: {arg}"

        agent.tools["echo"] = echo_tool
        assert "echo" in agent.tools
        assert agent.tools["echo"] is echo_tool
        """注册的工具应可通过 tools 字典取回同一可调用对象
        
        Args:
            tmp_path: pytest 临时目录（Path）
        
        Returns:
            None
        """
        llm = MockChatModel("No tool needed")
        agent = AgentLoop(llm, workspace_dir=tmp_path, max_tool_rounds=50)

        def echo_tool(arg):
            """测试用 echo 工具：回显参数

            Args:
                arg: 待回显的字符串

            Returns:
                str: 带 Echo 前缀的回显结果
            """
            return f"Echo: {arg}"

        agent.tools["echo"] = echo_tool
        assert "echo" in agent.tools
        assert agent.tools["echo"] is echo_tool

    @pytest.mark.asyncio
    async def test_direct_llm_response(self, tmp_path):
        """无工具调用时 run 应直接返回 LLM 文本

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        llm = MockChatModel("Hello! How can I help?")
        agent = AgentLoop(llm, workspace_dir=tmp_path, max_tool_rounds=50)
        result = await agent.run("Hi!")
        assert result == "Hello! How can I help?"
        assert llm.call_count == 1

    @pytest.mark.asyncio
    async def test_session_persistence(self, tmp_path):
        """连续两轮对话后上下文应保留历史消息

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        llm = MockChatModel("Response 1")
        agent = AgentLoop(llm, workspace_dir=tmp_path, max_tool_rounds=50)
        await agent.run("Message 1")
        await agent.run("Message 2")

        # 上下文应包含多轮 user/assistant 消息
        ctx_messages = agent.context.get_messages()
        assert len(ctx_messages) >= 3  # user, assistant, user, assistant...

    @pytest.mark.asyncio
    async def test_guard_bootstrap_misclaim_when_root_bootstrap_remains(self, tmp_path):
        """根目录 WORKSPACE_BOOTSTRAP 仍在且审阅 YES 时应追加系统提示

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        (tmp_path / "WORKSPACE_BOOTSTRAP.md").write_text("x", encoding="utf-8")
        # 主回复 + 审阅：误导 → YES
        llm = MockChatModel("好，初始化完成。", "YES")
        agent = AgentLoop(llm, workspace_dir=tmp_path, agent_id="main", max_tool_rounds=50)
        result = await agent.run("Hi")
        assert "初始化完成" in result
        assert "轻提示" in result
        assert "工作区级引导文件尚未清理" in result
        assert llm.call_count == 2

    @pytest.mark.asyncio
    async def test_guard_bootstrap_misclaim_catches_colloquial_收工(self, tmp_path):
        """口语「完成！初始化收工」靠二次 LLM 审阅，不靠关键词表。

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        (tmp_path / "WORKSPACE_BOOTSTRAP.md").write_text("x", encoding="utf-8")
        llm = MockChatModel("完成！初始化收工。", "YES")
        agent = AgentLoop(llm, workspace_dir=tmp_path, agent_id="main", max_tool_rounds=50)
        result = await agent.run("Hi")
        assert "初始化收工" in result
        assert "轻提示" in result
        assert "工作区级引导文件尚未清理" in result
        assert llm.call_count == 2

    @pytest.mark.asyncio
    async def test_bootstrap_misclaim_audit_no_does_not_append(self, tmp_path):
        """审阅判 NO 时不追加系统提示（即使仍有引导文件）。

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        (tmp_path / "WORKSPACE_BOOTSTRAP.md").write_text("x", encoding="utf-8")
        llm = MockChatModel("今天先聊聊天气吧。", "NO")
        agent = AgentLoop(llm, workspace_dir=tmp_path, agent_id="main", max_tool_rounds=50)
        result = await agent.run("Hi")
        assert result == "今天先聊聊天气吧。"
        assert "系统提示" not in result
        assert llm.call_count == 2

    @pytest.mark.asyncio
    async def test_guard_bootstrap_misclaim_off_when_no_bootstrap(self, tmp_path):
        """无引导文件时不触发审阅，仅一次 LLM 调用

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        llm = MockChatModel("好，初始化完成。")
        agent = AgentLoop(llm, workspace_dir=tmp_path, max_tool_rounds=50)
        result = await agent.run("Hi")
        assert result == "好，初始化完成。"
        assert "系统提示" not in result
        assert llm.call_count == 1
