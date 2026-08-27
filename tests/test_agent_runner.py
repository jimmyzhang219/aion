"""测试 AgentRunner — 通用 ReAct 循环"""

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult, ChatGeneration
from langchain_core.tools import tool

from aion.agent.agent_runner import (
    AgentEvent,
    TurnStart,
    TurnEnd,
    ToolEnd,
    RetryEvent,
    AgentLoopConfig,
    AgentResult,
    AgentRunner,
)


class MockChatModel(BaseChatModel):
    """模拟 LLM，可传入多段 str，第 N 次调用使用第 N 段。"""

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
        tools: list,  # type: ignore[override]
        *,
        tool_choice: str | None = None,
        **kwargs: object,
    ) -> BaseChatModel:
        object.__setattr__(self, "bound_tools", tools)
        object.__setattr__(self, "tool_choice", tool_choice)
        return self


class MockChatModelWithTools(MockChatModel):
    """增强版 Mock，可指定哪次调用返回 tool_calls。"""

    tool_call_responses: list[list[dict]] = []
    text_responses: list[str] = []

    def __init__(self, *texts, tool_calls: list[list[dict]] | None = None, **kwargs):
        super().__init__(*texts, **kwargs)
        object.__setattr__(self, "tool_call_responses", tool_calls or [])
        object.__setattr__(self, "text_responses", list(texts))

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object = None,
        **kwargs: object,
    ) -> ChatResult:
        self.call_count += 1
        self.last_messages = messages
        idx = min(self.call_count - 1, len(self.text_responses) - 1)
        tc_idx = min(self.call_count - 1, len(self.tool_call_responses) - 1)
        has_tc = self.call_count - 1 < len(self.tool_call_responses)
        if has_tc and self.tool_call_responses[tc_idx]:
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content="", tool_calls=self.tool_call_responses[tc_idx]))]
            )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.text_responses[idx]))])


class _FailingChatModel(MockChatModel):
    """模拟 LLM，前 N 次调用抛异常。"""

    fail_count: int = 0

    def __init__(self, fail_count: int = 3, **kwargs):
        super().__init__(**kwargs)
        object.__setattr__(self, "fail_count", fail_count)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object = None,
        **kwargs: object,
    ) -> ChatResult:
        self.call_count += 1
        self.last_messages = messages
        if self.call_count <= self.fail_count:
            raise ConnectionError(f"simulated failure #{self.call_count}")
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])


@tool
async def echo(text: str) -> str:
    """Echo back text"""
    return f"echo: {text}"


class TestAgentEvent:
    def test_turn_start_type(self):
        event = TurnStart()
        assert event.type == "turn_start"
        assert isinstance(event, AgentEvent)

    def test_turn_end_defaults(self):
        event = TurnEnd()
        assert event.type == "turn_end"
        assert event.message is None
        assert event.tool_results is None
        assert isinstance(event, AgentEvent)

    def test_turn_end_with_values(self):
        event = TurnEnd(message={"role": "assistant", "content": "hello"}, tool_results=[{"role": "tool"}])
        assert event.message == {"role": "assistant", "content": "hello"}
        assert event.tool_results == [{"role": "tool"}]

    def test_tool_end_with_error(self):
        event = ToolEnd(tool_call_id="tc1", tool_name="search", is_error=True)
        assert event.type == "tool_end"
        assert event.is_error is True
        assert isinstance(event, AgentEvent)

    def test_retry_event(self):
        event = RetryEvent(attempt=2, error="timeout")
        assert event.type == "retry"
        assert event.attempt == 2
        assert event.error == "timeout"
        assert isinstance(event, AgentEvent)


class TestAgentLoopConfig:
    def test_defaults(self):
        config = AgentLoopConfig()
        assert config.max_tool_rounds == 20
        assert config.tool_execution == "parallel"
        assert config.max_retries == 5
        assert config.retry_delay == 1.0
        assert config.abort_on_retry_exhausted is True

    def test_custom_values(self):
        config = AgentLoopConfig(max_tool_rounds=10, max_retries=0)
        assert config.max_tool_rounds == 10
        assert config.max_retries == 0


class TestAgentResult:
    def test_defaults(self):
        result = AgentResult()
        assert result.messages == []
        assert result.response == ""
        assert result.total_rounds == 0
        assert result.tool_calls_executed == 0
        assert result.stop_reason == "complete"
        assert result.error is None
        assert result.usage is None

    def test_non_default_values(self):
        result = AgentResult(
            messages=[{"role": "user", "content": "hi"}],
            response="hello",
            total_rounds=2,
            tool_calls_executed=1,
            stop_reason="max_rounds",
            error="too many rounds",
            usage={"prompt_tokens": 10, "completion_tokens": 20},
        )
        assert result.messages == [{"role": "user", "content": "hi"}]
        assert result.response == "hello"
        assert result.total_rounds == 2
        assert result.tool_calls_executed == 1
        assert result.stop_reason == "max_rounds"
        assert result.error == "too many rounds"
        assert result.usage == {"prompt_tokens": 10, "completion_tokens": 20}


class TestAgentRunner:
    """AgentRunner 核心功能测试"""

    @pytest.mark.asyncio
    async def test_text_response(self):
        """纯文本响应应直接返回，无工具调用"""
        llm = MockChatModel("Hello! How can I help?")
        tools = []
        runner = AgentRunner(llm, tools, config=AgentLoopConfig(max_tool_rounds=10))

        events: list[AgentEvent] = []
        result = await runner.run(
            messages=[{"role": "user", "content": "Hi!"}],
            emit=lambda e: events.append(e),
        )

        assert result.response == "Hello! How can I help?"
        assert result.stop_reason == "complete"
        assert result.total_rounds == 1
        assert any(e.type == "turn_start" for e in events)
        assert any(e.type == "turn_end" for e in events)

    @pytest.mark.asyncio
    async def test_empty_response(self):
        """LLM 返回空内容应走重试逻辑，最终标记为 error"""
        llm = MockChatModel("")
        runner = AgentRunner(llm, [], config=AgentLoopConfig(max_retries=2))

        result = await runner.run(
            messages=[{"role": "user", "content": "Hi!"}],
        )

        assert result.stop_reason in ("error", "retry_exhausted")

    @pytest.mark.asyncio
    async def test_tool_call_flow(self):
        """LLM 先返回 tool_calls，工具执行后再返回最终文本"""
        llm = MockChatModelWithTools(
            "The result is 42",
            tool_calls=[
                [
                    {
                        "name": "echo",
                        "args": {"text": "test"},
                        "id": "1",
                        "type": "tool_call",
                    }
                ],
            ],
        )
        runner = AgentRunner(llm, tools=[echo], config=AgentLoopConfig(max_tool_rounds=10))
        result = await runner.run(
            messages=[{"role": "user", "content": "Call echo"}],
        )
        assert result.response == "The result is 42"
        assert result.tool_calls_executed == 1
        assert result.total_rounds == 2

    @pytest.mark.asyncio
    async def test_event_emission(self):
        """验证所有事件类型正确发射"""
        events: list[AgentEvent] = []
        llm = MockChatModel("Final answer")
        runner = AgentRunner(llm, [], config=AgentLoopConfig(max_tool_rounds=10))
        await runner.run(
            messages=[{"role": "user", "content": "Hi"}],
            emit=lambda e: events.append(e),
        )
        event_types = [e.type for e in events]
        assert "turn_start" in event_types
        assert "turn_end" in event_types

    @pytest.mark.asyncio
    async def test_retry_check_hook(self):
        """retry_check 应被调用并决定是否重试"""
        llm = _FailingChatModel(fail_count=3)
        retry_calls: list[tuple] = []

        async def my_retry_check(error: Exception, attempt: int) -> bool:
            retry_calls.append((error, attempt))
            return attempt < 2

        runner = AgentRunner(llm, [], config=AgentLoopConfig(max_retries=5))
        result = await runner.run(
            messages=[{"role": "user", "content": "Hi"}],
            retry_check=my_retry_check,
        )
        assert result.stop_reason == "retry_exhausted"
        assert len(retry_calls) == 2

    @pytest.mark.asyncio
    async def test_should_stop_hook(self):
        """should_stop 返回 True 应提前终止"""
        llm = MockChatModel("step1", "step2", "step3")

        async def my_stop(round_num: int, msgs: list) -> bool:
            return round_num >= 1

        runner = AgentRunner(llm, [], config=AgentLoopConfig(max_tool_rounds=10))
        result = await runner.run(
            messages=[{"role": "user", "content": "Do work"}],
            should_stop=my_stop,
        )
        assert result.total_rounds == 1

    @pytest.mark.asyncio
    async def test_max_tool_rounds(self):
        """超过 max_tool_rounds 应停止"""
        llm = MockChatModel("step1", "step2", "step3", "step4")
        runner = AgentRunner(llm, [], config=AgentLoopConfig(max_tool_rounds=2))
        result = await runner.run(
            messages=[{"role": "user", "content": "Do work"}],
        )
        assert result.total_rounds <= 2
