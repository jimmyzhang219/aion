"""AgentLoop Plan-and-Execute 模式测试"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aion.agent.loop import AgentLoop, _parse_plan_steps
from aion.agent.agent_runner import AgentRunner, AgentResult
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult, ChatGeneration


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


@pytest.mark.asyncio
async def test_plan_graph_initializes(tmp_path):
    """Plan graph 在 AgentLoop 初始化时应成功构建"""
    llm = MockChatModel("Hello!")
    agent = AgentLoop(llm, workspace_dir=tmp_path, max_tool_rounds=50)

    assert agent._plan_graph is not None
    assert agent._graph_interrupted is False


@pytest.mark.asyncio
async def test_plan_mode_routes_to_plan(tmp_path):
    """execution_mode='plan' 应路由到 _run_plan"""
    llm = MockChatModel("## 执行计划\n1. 第一步\n2. 第二步\n3. 第三步")
    agent = AgentLoop(llm, workspace_dir=tmp_path, max_tool_rounds=50)

    # Plan mode: should generate a plan and interrupt (or try to)
    result = await agent.run("test", execution_mode="plan")
    assert result is not None
    # Should contain plan text (from interrupt) or execution plan prefix
    assert "执行计划" in result or "计划" in result


@pytest.mark.asyncio
async def test_react_mode_is_default(tmp_path):
    """execution_mode 默认应为 'react'，路由到 _run_react"""
    llm = MockChatModel("Hello! How can I help?")
    agent = AgentLoop(llm, workspace_dir=tmp_path, max_tool_rounds=50)

    result = await agent.run("Hi")
    assert result == "Hello! How can I help?"


@pytest.mark.asyncio
async def test_empty_message_returns_early(tmp_path):
    """空消息应直接返回空串"""
    llm = MockChatModel("should not be called")
    agent = AgentLoop(llm, workspace_dir=tmp_path, max_tool_rounds=50)

    result = await agent.run("")
    assert result == ""


@pytest.mark.asyncio
async def test_reset_context_clears_interrupted(tmp_path):
    """reset_context 应重置 _graph_interrupted"""
    llm = MockChatModel("Hello!")
    agent = AgentLoop(llm, workspace_dir=tmp_path, max_tool_rounds=50)

    agent._graph_interrupted = True
    agent.reset_context()
    assert agent._graph_interrupted is False


@pytest.mark.asyncio
async def test_parse_plan_steps_normal():
    """_parse_plan_steps 应正确分割带编号的步骤"""
    plan = "## 执行计划\n1. 第一步\n2. 第二步\n3. 第三步"
    steps = _parse_plan_steps(plan)
    assert len(steps) == 3
    assert steps[0] == "第一步"
    assert steps[1] == "第二步"
    assert steps[2] == "第三步"


def test_parse_plan_steps_parentheses():
    """_parse_plan_steps 应支持括号编号如 1)"""
    plan = "1) 第一步\n2) 第二步"
    steps = _parse_plan_steps(plan)
    assert len(steps) == 2
    assert steps[0] == "第一步"
    assert steps[1] == "第二步"


def test_parse_plan_steps_fallback():
    """_parse_plan_steps 在无编号行时应返回整段文本"""
    plan = "这是一个简单的计划描述，没有编号。"
    steps = _parse_plan_steps(plan)
    assert len(steps) == 1
    assert steps[0] == plan


@pytest.mark.asyncio
async def test_execute_plan_step_forwards_callbacks(tmp_path, monkeypatch):
    """_execute_plan_step 应将 langfuse_cb 封装为列表传给 AgentRunner.run 的 callbacks 参数"""
    llm = MockChatModel("ok")
    agent = AgentLoop(llm, workspace_dir=tmp_path, max_tool_rounds=50)

    captured_callbacks = None

    async def _mock_run(self, messages, *, callbacks=None, **kwargs):
        nonlocal captured_callbacks
        captured_callbacks = callbacks
        return AgentResult(stop_reason="complete", response="done")

    monkeypatch.setattr(AgentRunner, "run", _mock_run)

    cb = object()
    await agent._execute_plan_step("test step", langfuse_cb=cb)

    assert captured_callbacks == [cb]


@pytest.mark.asyncio
async def test_execute_plan_step_no_callback(tmp_path, monkeypatch):
    """_execute_plan_step 在 langfuse_cb=None 时应传 callbacks=None"""
    llm = MockChatModel("ok")
    agent = AgentLoop(llm, workspace_dir=tmp_path, max_tool_rounds=50)

    captured_callbacks = "sentinel"

    async def _mock_run(self, messages, *, callbacks=None, **kwargs):
        nonlocal captured_callbacks
        captured_callbacks = callbacks
        return AgentResult(stop_reason="complete", response="done")

    monkeypatch.setattr(AgentRunner, "run", _mock_run)

    await agent._execute_plan_step("test step")

    assert captured_callbacks is None
