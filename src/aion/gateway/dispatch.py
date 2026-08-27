"""统一消息调度

所有消息来源（Channel / HTTP / CLI）通过 dispatch_message() 统一处理。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from .commands import handle_slash_command
from .session_queue import QueueItem, enqueue, set_loop_factory
from ..channels.adapters import (
    ChannelPlugin,
)
from ..channels.types import MessageContext, DispatchResult
from ..config.loader import load_config
from ..llm.capabilities import check_modality_support
from ..log import set_traceid, reset_traceid, get_trace_logger, generate_traceid
from aion.channels.constants import ContentBlockType
from ..session.binder import SessionBinder

if TYPE_CHECKING:
    from ..agent.loop import AgentLoop

logger = get_trace_logger(__name__)


async def _check_modality_and_reject(
    ctx: MessageContext,
    ws_config: Any,
    config: Any,
    traceid: str,
) -> DispatchResult | None:
    """检查 ctx.content 是否包含模型不支持的模态，是则返回拒绝 DispatchResult。

    Args:
        ctx: 统一消息上下文
        ws_config: 工作空间配置（可能为 None）
        config: 全局配置
        traceid: 当前 trace ID

    Returns:
        模型不支持时返回含错误提示的 DispatchResult，否则返回 None
    """
    if not isinstance(ctx.content, list):
        return None
    required = {
        b["type"]
        for b in ctx.content
        if b.get("type") in frozenset({ContentBlockType.IMAGE, ContentBlockType.VIDEO, ContentBlockType.AUDIO})
    }
    if not required:
        return None

    leader_id = ws_config.get_leader() if ws_config else ""
    leader_cfg = ws_config.get_agent_config(leader_id) if ws_config else None
    provider_name = leader_cfg.get("provider", "") if leader_cfg else ""
    llm_cfg = config.get_model_config(provider_name) if provider_name else {}
    model_name = llm_cfg.get("model", "") if llm_cfg else ""
    if not model_name:
        return None

    ok, unsupported = check_modality_support(model_name, required)
    if not ok:
        mod_str = "、".join(sorted(unsupported))
        return DispatchResult(
            command_handled=True,
            command_response=(f"当前模型（{model_name}）不支持 {mod_str} 类型内容。请切换到支持多模态的模型后重试。"),
            session_id="",
            traceid=traceid,
        )
    return None


async def dispatch_message(
    ctx: MessageContext,
    channel: ChannelPlugin,
) -> DispatchResult:
    """统一消息调度入口 — 入队后立即返回轻量 ack。"""
    traceid = generate_traceid()
    _trace_token = set_traceid(traceid)

    if ctx.workspace_dir is None:
        raise ValueError("MessageContext.workspace_dir is required")

    logger.info(
        "dispatch_message: channel=%s chat=%s sender=%s content=%s",
        ctx.channel_id,
        ctx.chat_id,
        ctx.sender_id,
        ctx.content[:200]
        if isinstance(ctx.content, str)
        else f"[{len(ctx.content)} blocks] {str([{k: b.get(k) for k in ('type', 'data', 'mimeType', 'text') if k in b} for b in ctx.content])[:200]}",
    )

    try:
        config = load_config()
        ws_config = config.get_workspace(ctx.workspace_dir.name)
        if ws_config:
            execution_mode: Literal["react", "plan"] = ws_config.execution_mode
            agent_id = ws_config.get_leader()
        else:
            execution_mode = "react"
            agent_id = "main"

        # ── slash command 拦截（保持不变）──
        content = ctx.content or ""
        if isinstance(content, str) and content.startswith("/"):
            cmd_result = await handle_slash_command(
                ctx=ctx,
                channel=channel,
                config=config,
                workspace_name=ctx.workspace_dir.name,
                agent_id=agent_id,
                session_loops=None,  # Phase B: 不再缓存 AgentLoop
            )
            if cmd_result is not None:
                return cmd_result

        # ── 多模态能力检测 ──
        reject = await _check_modality_and_reject(ctx, ws_config, config, traceid)
        if reject is not None:
            return reject

        # ── session 解析 ──
        session_key = channel.build_session_key(ctx, agent_id)
        binder = SessionBinder(ctx.workspace_dir)
        session_id = binder.get_or_create_session_id(session_key)

        # ── 入队（Langfuse 由 AgentLoop 内部管理）──
        item = QueueItem(
            ctx=ctx,
            session_id=session_id,
            channel=channel,
            traceid=traceid,
            execution_mode=execution_mode,
            received_at_ms=int(time.time() * 1000),
        )
        await enqueue(session_id, item)

        # 返回轻量 ack
        return DispatchResult(
            session_id=session_id,
            traceid=traceid,
        )

    except Exception as e:
        logger.error(f"dispatch_message error: {e}")
        import traceback

        traceback.print_exc()
        return DispatchResult(
            error=f"{type(e).__name__}: {e}",
            session_id="",
            traceid=traceid,
        )
    finally:
        reset_traceid(_trace_token)


def shutdown_all_loops() -> None:
    """Phase B: AgentLoop 不再跨消息缓存，此函数已为 no-op。"""
    pass


async def _resolve_agent_loop(session_id: str, workspace_dir: Path) -> AgentLoop:
    """供 SessionQueue Worker 使用的 AgentLoop 解析器 — 每次创建新实例。"""
    from ..agent.factory import create_agent_loop

    return create_agent_loop(session_id, workspace_dir.name)


set_loop_factory(_resolve_agent_loop)
