"""Agent event types, config, result, and hook type aliases for AgentRunner.

AgentRunner class (ReAct loop) is added in the same module in a later task."""

import asyncio
from dataclasses import dataclass, field
from typing import Annotated, Any, Awaitable, Callable, Literal, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, add_messages
from langgraph.prebuilt import ToolNode

from ..log import get_trace_logger

logger = get_trace_logger(__name__)


# ── AgentEvent 事件类型 ──


@dataclass
class AgentEvent:
    """事件基类"""

    type: str = ""


@dataclass
class TurnStart(AgentEvent):
    type: str = "turn_start"


@dataclass
class TurnEnd(AgentEvent):
    type: str = "turn_end"
    message: dict | None = None
    tool_results: list[dict] | None = None


@dataclass
class ToolEnd(AgentEvent):
    type: str = "tool_end"
    tool_call_id: str = ""
    tool_name: str = ""
    result: dict | None = None
    is_error: bool = False


@dataclass
class RetryEvent(AgentEvent):
    type: str = "retry"
    attempt: int = 0
    error: str = ""


StopReason = Literal["complete", "max_rounds", "error", "aborted", "retry_exhausted"]


# ── AgentLoopConfig ──


@dataclass
class AgentLoopConfig:
    """全局默认配置，通过 AgentRunner.__init__ 传入。"""

    max_tool_rounds: int = 20
    tool_execution: Literal["parallel", "sequential"] = "parallel"
    max_retries: int = 5
    retry_delay: float = 1.0
    abort_on_retry_exhausted: bool = True


# ── Hook 类型别名 ──

RetryCheckFn = Callable[[Exception, int], Awaitable[bool]]
"""(error, attempt) -> True=重试, False=不重试"""

TransformCtxFn = Callable[[list[dict]], Awaitable[list[dict]]]
"""(messages) -> 压缩/裁剪后的 messages"""

ShouldStopFn = Callable[[int, list[dict]], Awaitable[bool]]
"""(round, messages) -> True=提前停止"""


# ── AgentResult ──


@dataclass
class AgentResult:
    """通用方法返回 —— 包含本次 run() 的所有产出信息"""

    messages: list[dict] = field(default_factory=list)
    response: str = ""
    total_rounds: int = 0
    tool_calls_executed: int = 0
    stop_reason: StopReason = "complete"
    error: str | None = None
    usage: dict | None = None


# ── AgentRunner ──


class _RetryExhaustedError(Exception):
    """Signals from _call_agent to run() that retries were exhausted."""

    pass


class _RunnerState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


class AgentRunner:
    """通用 ReAct 循环，业务无关。内部使用 LangGraph StateGraph。"""

    def __init__(
        self,
        llm: BaseChatModel,
        tools: list,
        *,
        config: AgentLoopConfig | None = None,
    ):
        self._llm = llm
        self._tools = tools
        self._config = config or AgentLoopConfig()

    async def run(
        self,
        messages: list[dict],
        *,
        emit: Callable[[AgentEvent], Awaitable[None]] | None = None,
        retry_check: RetryCheckFn | None = None,
        transform_context: TransformCtxFn | None = None,
        should_stop: ShouldStopFn | None = None,
        callbacks: list | None = None,
    ) -> AgentResult:
        """执行 ReAct 循环。"""

        async def _default_retry_check(e: Exception, a: int) -> bool:
            return True

        async def _default_should_stop(r: int, m: list) -> bool:
            return False

        hook_retry_check = retry_check or _default_retry_check
        hook_should_stop = should_stop or _default_should_stop

        async def _emit(event: AgentEvent) -> None:
            r = emit(event) if emit else None
            if r is not None:
                await r

        # 构建 LangGraph（每次都重建以确保最新工具）
        langchain_tools = self._tools
        llm_with_tools: BaseChatModel = (
            self._llm.bind_tools(langchain_tools)  # type: ignore[assignment]
            if langchain_tools
            else self._llm
        )
        graph = self._build_graph(llm_with_tools, langchain_tools, hook_retry_check, _emit)

        # 转换 dict messages → LangChain BaseMessage
        from ..llm.lc_bridge import dict_messages_to_lc

        lc_messages = dict_messages_to_lc(messages)

        # 应用 transform_context
        if transform_context:
            messages = await transform_context(messages)

        new_messages: list[dict] = []
        total_rounds = 0
        tool_calls_executed = 0
        final_response = ""
        stop_reason: StopReason = "complete"
        error: str | None = None
        usage: dict | None = None

        await _emit(TurnStart())

        graph_config: dict[str, Any] = {"recursion_limit": max(25, self._config.max_tool_rounds * 2 + 2)}
        if callbacks:
            graph_config["callbacks"] = callbacks

        try:
            async for chunk in graph.astream(
                {"messages": lc_messages},
                stream_mode="updates",
                config=graph_config,
            ):
                for node_name, node_output in chunk.items():
                    node_msgs = node_output.get("messages", [])
                    if not node_msgs:
                        continue
                    msg = node_msgs[-1]

                    if node_name == "agent":
                        if isinstance(msg, AIMessage):
                            total_rounds += 1
                            um = getattr(msg, "usage_metadata", None)
                            if isinstance(um, dict):
                                usage = um

                            if msg.tool_calls:
                                tc_list = [
                                    {
                                        "id": tc.get("id", "")
                                        if isinstance(tc, dict)
                                        else (getattr(tc, "id", "") or ""),
                                        "name": tc.get("name", "")
                                        if isinstance(tc, dict)
                                        else getattr(tc, "name", "unknown"),
                                        "arguments": tc.get("args", {})
                                        if isinstance(tc, dict)
                                        else getattr(tc, "args", {}),
                                    }
                                    for tc in msg.tool_calls
                                ]
                                tool_names = [t["name"] for t in tc_list]
                                logger.info("[Agent] Round %d: LLM → %s", total_rounds, ", ".join(tool_names))  # type: ignore[arg-type]
                                new_messages.append(
                                    {
                                        "role": "assistant",
                                        "content": str(getattr(msg, "content", "") or ""),
                                        "tool_calls": tc_list,
                                        "reasoning_content": getattr(msg, "additional_kwargs", {}).get(
                                            "reasoning_content", ""
                                        )
                                        or "",
                                    }
                                )
                                await _emit(
                                    TurnEnd(
                                        message={
                                            "role": "assistant",
                                            "tool_calls": tc_list,
                                            "reasoning_content": getattr(msg, "additional_kwargs", {}).get(
                                                "reasoning_content", ""
                                            )
                                            or "",
                                        }
                                    )
                                )
                            else:
                                text = str(msg.content or "")
                                rc = getattr(msg, "additional_kwargs", {}).get("reasoning_content", "") or ""
                                logger.info("[Agent] Round %d: LLM → text (%d chars)", total_rounds, len(text))
                                final_response = text
                                new_messages.append(
                                    {
                                        "role": "assistant",
                                        "content": text,
                                        "reasoning_content": rc,
                                    }
                                )
                                await _emit(
                                    TurnEnd(
                                        message={
                                            "role": "assistant",
                                            "content": text,
                                            "reasoning_content": rc,
                                        }
                                    )
                                )

                    elif node_name == "tools":
                        for tm in node_msgs:
                            if isinstance(tm, ToolMessage):
                                tool_calls_executed += 1
                                tname = getattr(tm, "name", "unknown") or "unknown"
                                tcontent = str(getattr(tm, "content", "") or "")
                                tc_id = getattr(tm, "tool_call_id", "") or ""
                                logger.info("[Agent] Round %d: %s → %d chars", total_rounds, tname, len(tcontent))
                                new_messages.append(
                                    {
                                        "role": "tool",
                                        "content": tcontent,
                                        "tool_call_id": tc_id,
                                        "name": tname,
                                    }
                                )
                                await _emit(
                                    ToolEnd(
                                        tool_call_id=tc_id,
                                        tool_name=tname,
                                        result={"content": tcontent},
                                        is_error=False,
                                    )
                                )

                # 检查 should_stop
                if stop_reason == "complete":
                    stop = await hook_should_stop(total_rounds, new_messages)
                    if stop:
                        break

        except _RetryExhaustedError as e:
            stop_reason = "retry_exhausted"
            error = str(e)
        except asyncio.CancelledError:
            stop_reason = "aborted"
            error = "cancelled"
        except Exception as e:
            stop_reason = "error"
            error = str(e)

        logger.info(
            "[Agent] ReAct done: rounds=%d tools=%d stop=%s response_len=%d",
            total_rounds,
            tool_calls_executed,
            stop_reason,
            len(final_response),
        )

        return AgentResult(
            messages=new_messages,
            response=final_response,
            total_rounds=total_rounds,
            tool_calls_executed=tool_calls_executed,
            stop_reason=stop_reason,
            error=error,
            usage=usage,
        )

    def _build_graph(
        self,
        llm_with_tools: BaseChatModel,
        langchain_tools: list,
        retry_check: RetryCheckFn,
        emit: Callable[[AgentEvent], Awaitable[None]],
    ):
        """构建 LangGraph StateGraph。"""
        config_local = self._config

        async def _call_agent(state: _RunnerState, config: RunnableConfig | None = None) -> dict:
            for attempt in range(1, config_local.max_retries + 2):  # 1次初始 + max_retries 次重试
                try:
                    logger.info("[Agent] 等待 LLM 响应...")
                    response = await llm_with_tools.ainvoke(state["messages"], config=config)
                    if _is_response_valid(response):
                        return {"messages": [response]}
                except Exception as e:
                    await emit(RetryEvent(attempt=attempt, error=str(e)))
                    if not await retry_check(e, attempt):
                        raise _RetryExhaustedError(f"retry_check rejected retry #{attempt}: {e}") from e
                    if attempt <= config_local.max_retries:
                        continue
                    raise
                # 空响应也重试
                if attempt <= config_local.max_retries:
                    continue
                raise ValueError("LLM returned empty response after retries")
            raise AssertionError("unreachable")

        def _should_continue(state: _RunnerState) -> str:
            last_msg = state["messages"][-1] if state["messages"] else None
            if isinstance(last_msg, AIMessage) and getattr(last_msg, "tool_calls", None):
                return "tools"
            return "__end__"

        tool_node = ToolNode(langchain_tools) if langchain_tools else None
        builder = StateGraph(_RunnerState)
        builder.add_node("agent", _call_agent)
        if tool_node:
            builder.add_node("tools", tool_node)
            builder.add_conditional_edges("agent", _should_continue)
            builder.add_edge("tools", "agent")
        else:
            builder.add_edge("agent", "__end__")
        builder.set_entry_point("agent")
        return builder.compile()


def _is_response_valid(response: AIMessage) -> bool:
    """检查 LLM 响应是否有效。"""
    if not isinstance(response, AIMessage):
        return False
    if getattr(response, "tool_calls", None):
        return True
    content = response.content
    content_text = content if isinstance(content, str) else ""
    fr = response.response_metadata.get("finish_reason", "")
    if fr in ("stop", "") and not content_text.strip():
        return False
    if fr == "tool_calls" and not getattr(response, "tool_calls", None):
        return False
    return True
