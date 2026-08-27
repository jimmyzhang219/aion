"""AgentLoop — 组件编排器

编排 ToolRegistry / ContextManager / SubagentOrchestrator /
PostProcessor / BootstrapMonitor 各组件协同工作。

核心职责：MCP 延迟初始化、LangGraph ReAct 循环、子 agent 结果协调。
"""

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional, Literal

from .context_manager import UsageAccumulator
from .post_processor import PostProcessor
from .tool_registry import ToolRegistry
from ..log import get_trace_logger
from ..observability import Tracer  # LangFuse 统一观测入口

logger = get_trace_logger(__name__)  # Agent 主循环与工具轮次日志


from .bootstrap_monitor import BootstrapMonitor
from .content import resolve_content_blocks
from .subagent_orchestrator import SubagentOrchestrator
from .context_manager import ContextManager
from langgraph.graph import StateGraph
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool, StructuredTool
from langchain_openai.chat_models.base import BaseChatOpenAI
from typing import TypedDict, Annotated
from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages

from .agent_runner import AgentRunner, AgentLoopConfig


class _PlanState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    plan: Optional[str]
    plan_approved: bool
    current_step_index: int
    step_results: list[str]


class AgentLoop:
    """Agent ReAct 循环

    核心机制：
    1. Bootstrap：将 `# Project Context`（及动态区 HEARTBEAT）注入 system prompt（见 ``bootstrap`` 模块）
    2. LangGraph ReAct Loop：LLM 推理 → 工具调用 → ToolNode 执行 → 循环
    3. 记忆召回：prompt 中注入 memory_search/memory_get 工具，LLM 自动调用召回记忆
    """

    def _build_plan_and_execute_graph(self) -> Any:
        """构建 Plan-and-Execute LangGraph。

        Graph structure:
          planner ──(conditional)──→ executor ──→ END
              │
              └──(pending approval)──→ planner (loop)
        """
        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.types import interrupt

        async def planner_node(state: _PlanState) -> dict:
            if state.get("plan_approved"):
                return {"plan_approved": True}

            plan = state.get("plan")
            runner = AgentRunner(
                self.llm,
                [],
                config=AgentLoopConfig(max_tool_rounds=1, max_retries=2),
            )
            if not plan:
                system_msg = (
                    "你是一个计划制定助手，负责 Plan-and-Execute 模式里的 Plan 环节。请分析用户的需求，制定一个清晰的执行计划。\n"
                    "计划应为分步骤的列表，每步用一个句子描述目标。"
                    "输出格式：\n"
                    "## 执行计划\n"
                    "1. 步骤一描述\n"
                    "2. 步骤二描述\n"
                    "..."
                )
                # 传所有 user 消息给 LLM，保证 resume 时"好的"有上下文
                user_msgs = [str(m.content) for m in state["messages"] if getattr(m, "type", "") in ("human", "user")]
                latest_input = user_msgs[-1] if user_msgs else ""
                result = await runner.run(
                    messages=[
                        {
                            "role": "system",
                            "content": system_msg + "\n\n用户原始请求：" + (user_msgs[0] if user_msgs else ""),
                        },
                        {"role": "user", "content": latest_input},
                    ],
                )
                plan = result.response or ""

            # Human-in-the-Loop（HITL），Pause graph, show plan to user
            user_feedback = interrupt(plan)

            feedback_str = str(user_feedback or "").strip().lower()
            approval_keywords = ("好的", "可以", "执行", "批准", "同意", "ok", "yes", "approve", "开始", "go")
            if any(feedback_str.startswith(kw) for kw in approval_keywords):
                return {"plan": plan, "plan_approved": True, "current_step_index": 0, "step_results": []}

            # Revision request → re-plan
            revise_prompt = f"原始计划：\n{plan}\n\n用户反馈：\n{user_feedback}\n\n请根据用户反馈重新制定计划。"
            revision_result = await runner.run(
                messages=[
                    {"role": "system", "content": "你是一个计划制定助手。"},
                    {"role": "user", "content": revise_prompt},
                ],
            )
            new_plan = revision_result.response or ""
            return {"plan": new_plan, "plan_approved": False, "current_step_index": 0, "step_results": []}

        async def executor_node(state: _PlanState) -> dict:
            plan = state.get("plan") or ""
            steps = _parse_plan_steps(plan)
            results = list(state.get("step_results", []))
            start = state.get("current_step_index", 0)

            for i in range(start, len(steps)):
                step_text = steps[i]
                prev_summary = "\n".join(f"步骤{j + 1}: {r}" for j, r in enumerate(results)) if results else "无"

                step_prompt = (
                    f"你正在执行计划的第 {i + 1}/{len(steps)} 步。\n\n"
                    f"## 计划上下文\n{plan}\n\n"
                    f"## 当前步骤目标\n{step_text}\n\n"
                    f"## 已完成步骤结果\n{prev_summary}\n\n"
                    f"请完成此步骤。完成后总结你做了什么。"
                )

                result = await self._execute_plan_step(step_prompt, langfuse_cb=self._plan_lf_cb)
                results.append(result)

            summary_lines = [f"步骤 {i + 1}: {results[i]}" for i in range(len(steps))]
            plan_summary = "[执行完成] 已按计划完成：\n" + "\n".join(summary_lines)
            return {
                "step_results": results,
                "current_step_index": len(steps),
                "plan_approved": True,
                "messages": [AIMessage(content=plan_summary)],
            }

        def route_after_planner(state: _PlanState) -> str:
            if state.get("plan_approved"):
                return "executor"
            return "planner"

        # noinspection PyTypeChecker
        builder = StateGraph(_PlanState)
        # noinspection PyTypeChecker
        builder.add_node("planner", planner_node)
        # noinspection PyTypeChecker
        builder.add_node("executor", executor_node)
        builder.set_entry_point("planner")
        builder.add_conditional_edges("planner", route_after_planner)
        builder.add_edge("executor", "__end__")

        checkpointer = MemorySaver()
        return builder.compile(checkpointer=checkpointer)

    async def _execute_plan_step(self, step_prompt: str, langfuse_cb: Any = None) -> str:
        """执行单个计划步骤，内部用 AgentRunner 做 mini ReAct 循环。"""
        messages = [
            {
                "role": "system",
                "content": "你是一个计划执行助手，负责 Plan-and-Execute 模式里的 Execute 环节。根据当前步骤目标，使用可用工具完成任务。",
            },
            {"role": "user", "content": step_prompt},
        ]

        langchain_tools = self.tool_registry.build_langchain_tools()
        runner = AgentRunner(
            self.llm,
            langchain_tools,
            config=AgentLoopConfig(max_tool_rounds=15),
        )

        result = await runner.run(
            messages=messages,
            callbacks=[langfuse_cb] if langfuse_cb else None,
        )

        if result.stop_reason in ("error", "retry_exhausted"):
            logger.error(f"Plan step failed: {result.error}")
            return "步骤执行出错"

        return result.response or "已完成"

    def __init__(
        self,
        llm: BaseChatOpenAI,
        max_tool_rounds: int,
        session_id: str = "default",
        workspace_dir: Path | str = "",
        agent_id: str = "main",
        memory_config: Optional[dict] = None,
        mcp_servers: Optional[list] = None,
        context_window_config: Optional[dict] = None,
        is_subagent: bool = False,
        subagent_depth: int = 0,
        parent_session_id: Optional[str] = None,
        subagent_system_prompt: str = "",
    ):
        """初始化 AgentLoop 及 transcript、compaction、工具与 LangGraph agent。

        Args:
            llm: LLM 实例
            max_tool_rounds: 原生 ReAct 最大工具轮数（从 config 传入）
            session_id: 会话 ID
            workspace_dir: 工作空间根目录
            agent_id: 可选 Agent ID
            memory_config: 记忆相关配置
            mcp_servers: MCP 服务器配置列表
            context_window_config: 上下文窗口 token 上限
            is_subagent: 是否为子 agent 模式
            subagent_depth: 子 agent 嵌套深度
            parent_session_id: 父 session ID

        Returns:
            None
        """
        self.llm = llm
        self.session_id = session_id
        # Trace / Observability 上下文，由 run() 在每次执行时设入
        self._current_trace_id: str = ""
        self._current_lf_session_id: str = ""
        if not workspace_dir:
            raise ValueError("workspace_dir is required")
        self.workspace_dir = Path(workspace_dir)
        self.agent_id = agent_id  # Agent ID，用于加载 Agent 专属 Bootstrap 文件
        self.memory_config = memory_config or {}
        self._mcp_servers = mcp_servers or []
        self._last_thinking_parts: list[str] = []  # 最近一次 thinking 内容，供外部获取
        self.tools: dict[str, Callable] = {}
        self.context_window_tokens = 200000  # default
        if context_window_config:
            self.context_window_tokens = context_window_config.get("context_window", 200000)

        # 余额缓存：(查询时间戳, 余额字符串)，1 小时过期
        self._balance_cache: Optional[tuple[float, str]] = None

        self._max_tool_rounds = max_tool_rounds
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Plan-and-Execute 阶段 Langfuse callback，由 _run_plan 设入、finally 清除
        self._plan_lf_cb: Any = None

        # Subagent 模式
        self.is_subagent = is_subagent
        self.subagent_depth = subagent_depth
        self.parent_session_id = parent_session_id
        self._subagent_system_prompt = subagent_system_prompt  # 子 agent 专用 prompt
        self.subagent_orch = SubagentOrchestrator(session_id, is_subagent)

        # 上下文管理（Context + Compaction + Pruning + 持久化 + System Prompt）
        self.ctx_mgr = ContextManager(
            llm=self.llm,
            session_id=self.session_id,
            workspace_dir=self.workspace_dir,
            agent_id=self.agent_id,
            context_window_tokens=self.context_window_tokens,
            memory_config=self.memory_config,
            is_subagent=self.is_subagent,
            subagent_system_prompt=self._subagent_system_prompt,
        )

        # 注册内置工具（通过 ToolRegistry）
        self.tool_registry = ToolRegistry(
            agent_loop=self,
            is_subagent=self.is_subagent,
        )
        self.tools = self.tool_registry.register_all()

        self._mcp_initialized: bool = False

        # ── Plan Graph（Plan-and-Execute 模式）──
        self._plan_graph = None
        self._graph_interrupted = False
        try:
            self._plan_graph = self._build_plan_and_execute_graph()
            logger.info(f"Plan graph initialized: model={self.llm.model}")
        except Exception as e:
            logger.warning(f"Plan graph init failed: {e}", exc_info=True)

        # 初始化 MCP 服务器并注册工具
        # 构建 system prompt 并注入启动上下文
        self.bootstrap_monitor = BootstrapMonitor(
            self.workspace_dir,
            self.agent_id,
            self.llm,
        )
        self.post_processor = PostProcessor(self.bootstrap_monitor)
        self._build_system_prompt()

    def push_subagent_result(self, session_id: str, result: str) -> None:
        """子 agent 完成时将结果推入父 context（announce 机制）。

        Args:
            session_id: 子 agent session ID
            result: 子 agent 完成结果文本
        """
        self.subagent_orch.push_result(session_id, result)

    def _build_system_prompt(self) -> None:
        """分多段 role=system 消息注入 context（委托 ContextManager）。"""
        self.ctx_mgr.build_system_prompt(
            memory_config=self.memory_config,
            is_subagent=self.is_subagent,
            subagent_system_prompt=self._subagent_system_prompt,
        )

    def _refresh_system_prompt(self) -> None:
        """基于当前磁盘状态重新构建 system prompt（委托 ContextManager）。"""
        self.ctx_mgr.refresh_system_prompt()

    def _build_langchain_tools(self) -> list:
        """将 self.tools dict 中的工具包装为 LangChain StructuredTool 列表（已委托给 ToolRegistry）。

        Returns:
            LangChain StructuredTool 列表
        """
        return self.tool_registry.build_langchain_tools()

    @property
    def mcp_servers(self) -> list[dict]:
        return self._mcp_servers

    @property
    def max_tool_rounds(self) -> int:
        return self._max_tool_rounds

    @property
    def react_loop(self):
        return getattr(self, "_loop", None)

    @property
    def last_thinking_parts(self) -> list[str]:
        return getattr(self, "_last_thinking_parts", [])

    @property
    def accumulated_usage(self) -> UsageAccumulator:
        """本次 run() 期间累积的 Token 用量（委托 ContextManager）。"""
        return self.ctx_mgr.accumulated_usage

    @property
    def context(self):
        return self.ctx_mgr.context

    @property
    def compaction(self):
        return self.ctx_mgr.compaction

    @property
    def _short_memory(self):
        return self.ctx_mgr.short_memory

    @property
    def _mid_memory(self):
        return self.ctx_mgr.daily_file_store

    _BALANCE_CACHE_TTL: int = 3600  # 余额缓存 TTL（秒）

    async def get_balance(self) -> Optional[str]:
        """获取 API 余额（含 1 小时缓存）。

        Returns:
            余额字符串；不支持或失败时 None
        """
        now = time.time()
        if self._balance_cache is not None:
            cached_time, cached_value = self._balance_cache
            if now - cached_time < self._BALANCE_CACHE_TTL:
                return cached_value

        get_balance_fn = getattr(self.llm, "get_balance", None)
        if get_balance_fn is None:
            return None
        balance = await get_balance_fn()
        if balance is not None:
            self._balance_cache = (now, balance)
        return balance

    def reset_context(self, new_session_id: Optional[str] = None) -> None:
        """重置会话上下文（委托 ContextManager）。"""
        self.ctx_mgr.reset(new_session_id)
        self._graph_interrupted = False

    async def _init_mcp_async(self) -> None:
        """初始化 MCP 连接，注册工具，重建 LangGraph agent。

        可被工厂或 dispatch 层主动调用以实现提前初始化，
        也可由 _run_prelude 延迟调用。
        调用多次（缓存命中时）为幂等操作。
        单服务器连接失败不阻塞其他服务器。
        """
        if not self._mcp_servers or self._mcp_initialized:
            return

        from ..mcp import initialize_mcp_servers

        try:
            mcp_data = await initialize_mcp_servers(
                self._mcp_servers,
                workspace_key=self.workspace_dir,
            )
        except Exception:
            logger.warning("MCP initialization failed, running without MCP tools")
            self._mcp_initialized = True
            return
        self.tool_registry.register_mcp_structured_tools(mcp_data["tools"])

        manager = mcp_data["manager"]
        self._mcp_manager = manager

        # 注册 tools/list_changed 回调：工具变更时更新注册表
        # AgentRunner 在 _run_react() 中每次新建时自动获取最新工具
        async def _rebuild_agent_on_tools_changed(server_name: str) -> None:
            logger.info(
                "Updating MCP tools after change on '%s'",
                server_name,
            )
            fresh_tools = manager.get_langchain_tools()
            self.tool_registry.register_mcp_structured_tools(fresh_tools)

        for srv_name in manager.server_names():
            manager.set_on_tools_changed(srv_name, _rebuild_agent_on_tools_changed)

        logger.info(
            "MCP tools registered: %d tools",
            len(self._build_langchain_tools()),
        )
        self._mcp_initialized = True

    async def _run_prelude(self, user_input: str | list[dict]) -> str | None:
        """run() 的公共前置处理。返回 None 表示继续，非空串表示提前返回。"""
        # 延迟初始化 MCP 服务器（首次 run 时初始化）
        if self._mcp_servers and not self._mcp_initialized:
            await self._init_mcp_async()

        # 捕获事件循环引用
        if not hasattr(self, "_loop") or self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

        # 空消息检查（仅对 str 做 empty check，list content 视为非空）
        if isinstance(user_input, str):
            if not user_input or not user_input.strip():
                logger.debug("[AgentLoop.run] 跳过空消息")
                return ""

        # 重置 Token 累积
        self.ctx_mgr._accumulated_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

        return None  # 继续正常流程

    async def _run_react(
        self,
        user_input: str | list[dict],
        langfuse_cb: Any,
        langfuse_trace_id: str,
        session_id: str = "",
        received_at_ms: int = 0,
    ) -> str:
        """ReAct 模式处理 — 使用 AgentRunner 替代直调 LangGraph astream。"""
        self.ctx_mgr.set_time_anchor(received_at_ms)

        existing = self.context.messages
        # 多模态 content: 文件路径 → base64
        if isinstance(user_input, list):
            user_input = await resolve_content_blocks(user_input)
        # dedup 检查：str 比较，list 内容总是追加
        is_dup = (
            isinstance(user_input, str)
            and existing
            and existing[-1].get("role") == "user"
            and existing[-1].get("content") == user_input
        )
        if is_dup:
            logger.debug(f"[AgentLoop.run] 跳过重复用户消息: {user_input[:50]}")
        else:
            self.context.add_user(user_input)

        messages = self.context.get_messages()

        clean_response = ""
        thinking_parts: list[str] = []
        _last_rc = ""
        from .subagent.registry import get_global_registry

        _sub_reg = get_global_registry()

        try:
            while True:
                # 构建 AgentRunner（每次都重建以确保最新工具）
                langchain_tools = self.tool_registry.build_langchain_tools()
                runner = AgentRunner(
                    self.llm,
                    langchain_tools,
                    config=AgentLoopConfig(max_tool_rounds=self._max_tool_rounds),
                )

                # Context compression hook
                async def _transform(msgs: list[dict]) -> list[dict]:
                    msgs, _ = await self.ctx_mgr.compact_if_needed(msgs)
                    msgs = self.ctx_mgr.prune(msgs)
                    msgs = await self.ctx_mgr.hard_cap_safety_net(msgs)
                    return msgs

                # 重试 hook
                async def _retry(error: Exception, attempt: int) -> bool:
                    logger.warning(f"[Agent] LLM 调用失败 (attempt {attempt}/6): {error}")
                    err_str = str(error)
                    # 413 RequestTooLarge — 重试无意义，立即报错
                    if "413" in err_str or "RequestTooLarge" in err_str or "too large" in err_str.lower():
                        return False
                    return attempt <= 5

                # 子 agent pending 停止 hook
                async def _should_stop(r: int, m: list) -> bool:
                    return self.subagent_orch.has_pending()

                # 执行 ReAct
                result = await runner.run(
                    messages=messages,
                    emit=None,
                    retry_check=_retry,
                    transform_context=_transform,
                    should_stop=_should_stop,
                    callbacks=[langfuse_cb] if langfuse_cb else None,
                )

                if result.stop_reason in ("error", "retry_exhausted"):
                    raise RuntimeError(result.error or f"Agent failed: {result.stop_reason}")

                # 处理结果
                clean_response = result.response

                # 将中间消息加入 context
                for m in result.messages:
                    if m["role"] == "assistant":
                        self.context.add_assistant(
                            content=m["content"],
                            tool_calls=m.get("tool_calls"),
                        )
                    elif m["role"] == "tool":
                        self.context.add_tool(
                            content=m["content"],
                            tool_call_id=m["tool_call_id"],
                            name=m.get("name", ""),
                        )
                        # Bootstrap monitor 检查工具输出
                        if self.bootstrap_monitor.check_output_for_refresh(m.get("name", ""), m["content"]):
                            self._refresh_system_prompt()

                # 从 AgentResult 的 usage 累积 token 用量
                if result.usage:
                    self.ctx_mgr.append_usage(result.usage)

                # 追踪 reasoning_content
                for m in reversed(result.messages):
                    if m["role"] == "assistant" and m.get("reasoning_content"):
                        _last_rc = m["reasoning_content"]
                        thinking_parts = [_last_rc]
                        break

                # 子 agent 编排（保持不变）
                if self.subagent_orch.has_pending():
                    for sid, res in self.subagent_orch.drain_pending():
                        self.context.add_user(res)
                    messages = self.context.get_messages()
                    continue

                active = _sub_reg.list_active_by_parent(self.session_id)
                if active:
                    logger.debug(f"[Subagent] waiting for {len(active)} subagent(s)...")
                    results = await self.subagent_orch.wait_for_active(_sub_reg)
                    for sid, res in results:
                        self.context.add_user(res)
                    messages = self.context.get_messages()
                    continue

                break

            # 后处理
            result_pp = await self.post_processor.process(
                raw_text=clean_response,
                llm_last_msg=None,  # AgentRunner 不返回原始 LC message
            )
            clean_response = result_pp["response"]
            thinking_parts = result_pp["thinking_parts"]

            # PostProcessor 因 llm_last_msg=None 无法提取 reasoning_content，
            # 从 AgentRunner 的 result.messages 中补回
            if _last_rc and _last_rc not in thinking_parts:
                thinking_parts = [_last_rc] + thinking_parts

        except Exception as e:
            logger.error(f"AgentRunner failed: {e}", exc_info=True)
            raise

        # 持久化（使用 result.messages）
        turn_messages: list[dict] = [{"role": "user", "content": user_input}]
        turn_messages.extend(result.messages)
        if _last_rc:
            turn_messages[-1]["reasoning_content"] = _last_rc

        await self.ctx_mgr.persist_turn(turn_messages)
        self._last_thinking_parts = thinking_parts
        self._last_activity_time = time.time()

        return clean_response

    async def _run_plan(
        self,
        user_input: str | list[dict],
        langfuse_cb: Any,
        langfuse_trace_id: str,
        session_id: str = "",
        received_at_ms: int = 0,
    ) -> str:
        """Plan-and-Execute 模式处理。"""
        assert self._plan_graph is not None, "Plan graph not initialized — check startup logs"

        # 注入时间锚点（用入队时间，不对用户消息做任何修改）
        self.ctx_mgr.set_time_anchor(received_at_ms)

        existing = self.context.messages
        is_dup = (
            isinstance(user_input, str)
            and existing
            and existing[-1].get("role") == "user"
            and existing[-1].get("content") == user_input
        )
        if is_dup:
            logger.debug(f"[AgentLoop._run_plan] 跳过重复用户消息: {str(user_input)[:50]}")
        else:
            self.context.add_user(user_input)

        messages = self.context.get_messages()
        from ..llm.lc_bridge import dict_messages_to_lc
        from langgraph.types import Command

        clean_response = ""
        thread_config = {
            "configurable": {"thread_id": self.session_id},
            "recursion_limit": self._max_tool_rounds,
            "callbacks": [langfuse_cb] if langfuse_cb else [],
        }

        # 设置 Plan-and-Execute 阶段 Langfuse callback，供 executor_node 读取
        self._plan_lf_cb = langfuse_cb

        # ── 检测待审批计划（跨 AgentLoop 恢复）──
        _pending_plan = _extract_pending_plan(messages)
        _is_approval = isinstance(user_input, str) and user_input.strip().lower() in (
            "好的",
            "可以",
            "执行",
            "批准",
            "同意",
            "ok",
            "yes",
            "approve",
            "开始",
            "go",
        )
        if _pending_plan and _is_approval and not self._graph_interrupted:
            self._graph_interrupted = False
            logger.debug(f"[Plan] approving pending plan, executing {len(_parse_plan_steps(_pending_plan))} steps")
            steps = _parse_plan_steps(_pending_plan)
            step_results: list[str] = []
            for i, step_text in enumerate(steps):
                prev = "\n".join(f"步骤{j + 1}: {r}" for j, r in enumerate(step_results)) if step_results else "无"
                prompt = (
                    f"你正在执行计划的第 {i + 1}/{len(steps)} 步。\n\n"
                    f"## 计划上下文\n{_pending_plan}\n\n"
                    f"## 当前步骤目标\n{step_text}\n\n"
                    f"## 已完成步骤结果\n{prev}\n\n"
                    f"请完成此步骤。完成后总结你做了什么。"
                )
                r = await self._execute_plan_step(prompt, langfuse_cb=langfuse_cb)
                step_results.append(r)
            summary = "[执行完成] 已按计划完成：\n" + "\n".join(
                f"步骤 {i + 1}: {step_results[i]}" for i in range(len(steps))
            )
            clean_response = summary
            await self.ctx_mgr.persist_turn(
                [
                    {"role": "user", "content": user_input},
                    {"role": "assistant", "content": clean_response},
                ]
            )
            self._last_thinking_parts = []
            self._last_activity_time = time.time()
            return clean_response

        try:
            if self._graph_interrupted:
                stream = self._plan_graph.astream(
                    Command(resume=user_input),
                    stream_mode="updates",
                    config=thread_config,
                )
            else:
                lc_messages = dict_messages_to_lc(messages)
                stream = self._plan_graph.astream(
                    {
                        "messages": lc_messages,
                        "plan": None,
                        "plan_approved": False,
                        "current_step_index": 0,
                        "step_results": [],
                    },
                    stream_mode="updates",
                    config=thread_config,
                )

            async for chunk in stream:
                for node_name, node_output in chunk.items():
                    if node_name == "__interrupt__":
                        # node_output is a tuple of Interrupt objects
                        if isinstance(node_output, (tuple, list)) and len(node_output) > 0:
                            plan_text = str(node_output[0].value)
                        elif hasattr(node_output, "value"):
                            plan_text = str(node_output.value)
                        else:
                            plan_text = str(node_output)
                        self._graph_interrupted = True
                        # 持久化待审批计划到磁盘，跨 AgentLoop 恢复
                        self.context.add_assistant(content=f"[pending_plan]\n{plan_text}\n[/pending_plan]")
                        await self.ctx_mgr.persist_turn(
                            [
                                {"role": "user", "content": user_input},
                                {"role": "assistant", "content": f"[pending_plan]\n{plan_text}\n[/pending_plan]"},
                            ]
                        )
                        clean_response = f"**📋 计划方案（待审批）**\n\n{plan_text}\n\n---\n请审批（回复「好的」/「执行」/「批准」）或给出修改意见。"
                        return clean_response

                    elif node_name == "planner":
                        po = node_output if isinstance(node_output, dict) else {}
                        if po.get("plan_approved"):
                            self._graph_interrupted = False

                    elif node_name == "executor":
                        self._graph_interrupted = False
                        msgs = node_output.get("messages", [])
                        for msg in msgs:
                            content = getattr(msg, "content", "")
                            if content:
                                clean_response = str(content) if isinstance(content, str) else ""
                        if not clean_response:
                            sr = node_output.get("step_results", [])
                            if sr:
                                clean_response = "执行完成:\n" + "\n".join(f"- {r}" for r in sr)

        finally:
            self._plan_lf_cb = None

        if clean_response:
            await self.ctx_mgr.persist_turn(
                [
                    {"role": "user", "content": user_input},
                    {"role": "assistant", "content": clean_response},
                ]
            )
        self._last_thinking_parts = []
        self._last_activity_time = time.time()

        return clean_response or "计划执行完成。"

    async def run(
        self,
        user_input: str | list[dict],
        trace_id: str = "",
        session_id: str = "",
        execution_mode: Literal["react", "plan"] = "react",
        received_at_ms: int = 0,
    ) -> str:
        """处理用户输入，返回助手回复。

        根据 execution_mode 路由到 ReAct 或 Plan-and-Execute 模式。

        Args:
            user_input: 用户消息正文（纯文本 ``str`` 或多模态 ``list[dict]``）
            trace_id: 可选的 trace ID（由 dispatch_message 生成，用于关联 Langfuse）
            session_id: 可选的会话 ID，用于 Langfuse CallbackHandler
            execution_mode: "react" 或 "plan"
            received_at_ms: 消息入队时间戳（毫秒），用于时间锚点

        Returns:
            助手可见回复
        """
        # ── Langfuse: AgentLoop 内部闭环 ──
        from aion.log import generate_traceid

        langfuse_trace_id = trace_id or generate_traceid()

        _lf_cb = Tracer.create_callback(
            trace_id=langfuse_trace_id,
            session_id=session_id or "",
        )
        self._current_trace_id = langfuse_trace_id
        self._current_lf_session_id = session_id

        _result = ""
        _loop_token = None  # tools 层 ContextVar token
        try:
            # tools 层 ContextVar：供 sessions_spawn 等工具获取当前 AgentLoop
            from aion.tools._context import set_agent_loop

            _loop_token = set_agent_loop(self)

            prelude = await self._run_prelude(user_input)
            if prelude is not None:
                return prelude

            if execution_mode == "plan":
                _result = await self._run_plan(
                    user_input, _lf_cb, langfuse_trace_id, session_id, received_at_ms=received_at_ms
                )
            else:
                _result = await self._run_react(
                    user_input, _lf_cb, langfuse_trace_id, session_id, received_at_ms=received_at_ms
                )
            return _result
        finally:
            if _loop_token is not None:
                from aion.tools._context import reset_agent_loop

                reset_agent_loop(_loop_token)

            # 最后一个观测决定 trace name 并设 input/output
            _input_snippet = (
                user_input[:20]
                if isinstance(user_input, str)
                else next((b.get("text", "")[:20] for b in user_input if b.get("type") == "text"), "[multimodal]")
            )
            _input_full = user_input[:500] if isinstance(user_input, str) else str(user_input)[:500]

            # session_id 通过 propagate_attributes 传播给子观测
            _session_id = session_id or ""
            _kw = {"session_id": _session_id} if _session_id else {}
            with Tracer.propagate_attributes(**_kw):
                _name_obs = Tracer.start_observation(
                    trace_id=langfuse_trace_id,
                    name=_input_snippet,
                    input=_input_full,
                    as_type="agent",
                    session_id=session_id,
                )
                if hasattr(_name_obs, "_span"):
                    if _result:
                        _name_obs.set_output(str(_result)[:500])
                    _name_obs._span.end()
            Tracer.flush()

    def _resolve_child_llm(self, target_agent_id: str) -> Any:
        """为子 agent 解析 LLM 实例：不同 agent 使用各自的 provider 配置。"""
        if target_agent_id == (self.agent_id or "main"):
            return self.llm

        from aion.config.loader import load_config
        from aion.llm.factory import create_llm

        try:
            config = load_config()
            ws_name = self.workspace_dir.name
            ws_config = config.get_workspace(ws_name)
            if ws_config:
                agent_cfg = ws_config.get_agent_config(target_agent_id)
                if agent_cfg:
                    provider_name = agent_cfg.get("provider", "")
                    if provider_name:
                        llm_cfg = config.get_model_config(provider_name)
                        if llm_cfg:
                            return create_llm(provider_name, llm_cfg)
        except Exception:
            pass

        return self.llm

    async def execute_subagent(
        self,
        task: str,
        agent_id: str | None = None,
    ) -> str:
        """派生子 agent 并同步等待返回。"""
        from .agent_runner import AgentRunner, TurnEnd, ToolEnd
        from .subagent.session import SubagentSession
        from .subagent.prompt import build_subagent_system_prompt

        child_session_id = f"sub-{uuid.uuid4().hex[:12]}"
        target_agent_id = agent_id or self.agent_id or "main"

        child_llm = self._resolve_child_llm(target_agent_id) if agent_id else self.llm

        # 1-2. Session 文件 + 初始消息
        session = SubagentSession(
            child_session_id,
            target_agent_id,
            self.workspace_dir,
        )
        sys_prompt = build_subagent_system_prompt(
            task=task,
            child_session_id=child_session_id,
            parent_session_id=self.session_id,
        )
        session.append_messages(
            [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": task},
            ]
        )

        # 3. 工具 — 使用 ToolRegistry 确保 bootstrap 安全包装，排除 sessions_spawn
        tools: list = [t for t in self.tool_registry.build_langchain_tools() if t.name != "sessions_spawn"]

        # 4. emit 回调 — 实时持久化
        async def _on_event(event):
            if isinstance(event, TurnEnd) and event.message:
                session.append_messages([{"role": "assistant", **event.message}])
            elif isinstance(event, ToolEnd):
                session.append_messages(
                    [
                        {
                            "role": "tool",
                            "content": (event.result or {}).get("content", ""),
                            "tool_call_id": event.tool_call_id,
                            "name": event.tool_name,
                        }
                    ]
                )

        # 5. AgentRunner 执行 ReAct
        # ── Langfuse: 子 agent trace 继承父 trace_id ──
        from aion.log import generate_traceid

        _child_lf_cb = Tracer.create_callback(
            trace_id=self._current_trace_id or generate_traceid(),
            session_id=child_session_id or "",
        )
        runner = AgentRunner(child_llm, tools)
        result = await runner.run(
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": task},
            ],
            emit=_on_event,
            callbacks=[_child_lf_cb] if _child_lf_cb else None,
        )

        # 6-7. 标记删除 + 返回
        Tracer.flush()
        session.mark_deleted()
        return result.response or (f"Error: {result.error}" if result.error else "No response")

    def _build_isolated_tool(self, tool: BaseTool, blocked_prefixes: list[str]) -> StructuredTool:
        """包装文件工具，禁止访问 blocked_prefixes 路径。"""
        import inspect
        from pathlib import Path

        original_func = tool.func or tool.coroutine  # type: ignore[attr-defined]

        async def _isolated_func(**kwargs):
            for key in ("path", "paths", "file_path", "directory"):
                value = kwargs.get(key)
                if value is None:
                    continue
                if isinstance(value, str):
                    resolved = str(Path(value).expanduser().resolve())
                    for prefix in blocked_prefixes:
                        prefix_slash = prefix.rstrip("/") + "/"
                        if resolved == prefix.rstrip("/") or resolved.startswith(prefix_slash):
                            return "拒绝访问: 无权访问其他 agent 目录下的文件"
                elif isinstance(value, list):
                    for v in value:
                        resolved = str(Path(v).expanduser().resolve())
                        for prefix in blocked_prefixes:
                            prefix_slash = prefix.rstrip("/") + "/"
                            if resolved == prefix.rstrip("/") or resolved.startswith(prefix_slash):
                                return "拒绝访问: 无权访问其他 agent 目录下的文件"
            if inspect.iscoroutinefunction(original_func):
                return await original_func(**kwargs)
            return original_func(**kwargs)

        return StructuredTool.from_function(
            func=None,
            name=tool.name,
            description=tool.description,
            args_schema=tool.args_schema,
            coroutine=_isolated_func,
        )

    async def execute_agent_send(self, agent_id: str, task: str) -> str:
        """将任务发送给 workspace 中的 member agent 执行。

        Member agent 有自己独立的 CONFIG.md、记忆文件、session 文件。
        每次调用创建独立新会话（fresh session）。
        Member agent 可以访问工作空间文件，但不能访问其他 agent 的 agents/ 目录。
        """
        from datetime import datetime
        import uuid

        # 1. 校验 agent_id 并获取成员列表
        from aion.config.loader import load_config

        try:
            config = load_config()
            ws_name = self.workspace_dir.name
            ws_config = config.get_workspace(ws_name)
        except Exception:
            return "Error: 无法加载工作空间配置"

        if not ws_config:
            return "Error: 无法找到工作空间配置"

        leader_id = ws_config.get_leader()
        all_agent_ids: list[str] = [k for k, v in ws_config.agents.items() if k != "leader" and isinstance(v, dict)]

        if agent_id not in all_agent_ids:
            available = ", ".join(all_agent_ids)
            return f"Error: 未知的 agent_id '{agent_id}'，可用: {available}"

        if agent_id == leader_id or agent_id == self.agent_id:
            return "Error: 不能向自己发送任务"

        # 2. 解析 member agent 的 LLM
        child_llm = self._resolve_child_llm(agent_id)

        # 3. 构建 system prompt（bootstrap 已加载 CONFIG.md，含 Soul 段；无需再单独读取）
        from .prompt import build_system_prompt

        sections = build_system_prompt(
            workspace_dir=self.workspace_dir,
            agent_id=agent_id,
            memory_config=self.memory_config,
            is_subagent=False,
            is_leader=False,
        )

        system_prompt = "\n\n".join(s.strip() for s in sections if s and s.strip())

        # 4. 构建工具列表 + 文件隔离
        from .agent_runner import AgentRunner, TurnEnd, ToolEnd

        tools = self.tool_registry.build_langchain_tools()

        blocked_prefixes = [
            str((self.workspace_dir / "agents" / other_id).resolve())
            for other_id in all_agent_ids
            if other_id != agent_id
        ]

        isolated_tools: list = []
        _PATH_TOOL_NAMES = frozenset({"read", "write", "edit", "delete", "ls", "grep", "trash"})

        for tool in tools:
            if tool.name not in _PATH_TOOL_NAMES:
                isolated_tools.append(tool)
            else:
                isolated_tools.append(self._build_isolated_tool(tool, blocked_prefixes))

        # 5. 创建 session 文件（持久保留，不标记 .delete）
        child_session_id = str(uuid.uuid4())
        session_dir = self.workspace_dir / "agents" / agent_id / "sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        session_file = session_dir / f"{timestamp}_{child_session_id}.jsonl"
        session_file.touch()

        # 写入初始消息使 session 文件完整可追溯
        ts = datetime.now().isoformat()
        import json

        init_entries = [
            json.dumps(
                {"type": "message", "message": {"role": "system", "content": system_prompt, "timestamp": ts}},
                ensure_ascii=False,
            ),
            json.dumps(
                {"type": "message", "message": {"role": "user", "content": task, "timestamp": ts}}, ensure_ascii=False
            ),
        ]
        with open(session_file, "a") as f:
            f.write("\n".join(init_entries) + "\n")

        async def _write_session(messages: list[dict]) -> None:
            import json

            ts = datetime.now().isoformat()
            chunks: list[str] = []
            for msg in messages:
                entry = dict(msg)
                entry.setdefault("timestamp", ts)
                chunks.append(json.dumps({"type": "message", "message": entry}, ensure_ascii=False))
            with open(session_file, "a") as f:
                f.write("\n".join(chunks) + "\n")

        async def _on_event(event):
            if isinstance(event, TurnEnd) and event.message:
                await _write_session([{"role": "assistant", **event.message}])
            elif isinstance(event, ToolEnd):
                await _write_session(
                    [
                        {
                            "role": "tool",
                            "content": (event.result or {}).get("content", ""),
                            "tool_call_id": event.tool_call_id,
                            "name": event.tool_name,
                        }
                    ]
                )

        # 6. AgentRunner 执行 ReAct
        # ── Langfuse: member agent trace 继承父 trace_id ──
        from aion.log import generate_traceid

        _child_lf_cb = Tracer.create_callback(
            trace_id=self._current_trace_id or generate_traceid(),
            session_id=child_session_id or "",
        )
        runner = AgentRunner(child_llm, isolated_tools)
        result = await runner.run(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task},
            ],
            emit=_on_event,
            callbacks=[_child_lf_cb] if _child_lf_cb else None,
        )

        # 7. 返回结果（session 保留，不标记删除）
        Tracer.flush()
        return result.response or (f"Error: {result.error}" if result.error else "No response")

    # ── 多模态辅助方法 ──


def _extract_pending_plan(messages: list[dict]) -> str | None:
    """从 context messages 中提取待审批计划（[pending_plan]...[/pending_plan]）。"""
    for m in reversed(messages):
        content = m.get("content", "") or ""
        import re

        m2 = re.search(r"\[pending_plan\](.*?)\[/pending_plan\]", content, re.DOTALL)
        if m2:
            return m2.group(1).strip()
    return None


def _parse_plan_steps(plan_text: str) -> list[str]:
    """从计划文本中提取步骤列表（按数字编号分割）。"""
    import re

    lines = plan_text.strip().split("\n")
    steps = []
    for line in lines:
        line = line.strip()
        if re.match(r"^\d+[.)]\s", line):
            steps.append(re.sub(r"^\d+[.)]\s*", "", line))
    if not steps:
        steps = [plan_text.strip()]
    return steps


def _is_valid_response(response: AIMessage) -> bool:
    """检查 LLM 返回是否有效，无效时将触发 _call_agent 重试。

    无效条件：
    - ``finish_reason=stop`` + ``content=""``（空回复）
    - ``finish_reason=tool_calls`` + 无实际 ``tool_calls``（工具调用丢失/截断）
    """
    if not isinstance(response, AIMessage):
        return False
    # Has tool calls -> valid
    if getattr(response, "tool_calls", None):
        return True

    content = response.content
    content_text = content if isinstance(content, str) else ""
    fr = response.response_metadata.get("finish_reason", "")

    # ── 无效条件 1：finish_reason=stop + content=""（空回复）──
    if fr in ("stop", "") and not content_text.strip():
        return False

    # ── 无效条件 2：finish_reason=tool_calls + 无 tool_calls（工具调用丢失/截断）──
    has_tool_calls = bool(getattr(response, "tool_calls", None))
    if fr == "tool_calls" and not has_tool_calls:
        return False

    # Non-empty content -> valid
    return True
