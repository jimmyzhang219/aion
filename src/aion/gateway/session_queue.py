"""Session 消息队列模块

所有 Channel 消息经 dispatch_message 汇聚后，通过模块级函数
按 session_id 排队串行执行。每个 session 一个后台 Worker 消费 asyncio.Queue。
"""

import asyncio
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Literal, Optional

from ..channels.types import DispatchResult, MessageContext
from ..core.context import current_workspace
from ..log import get_trace_logger, set_traceid

logger = get_trace_logger(__name__)


@dataclass
class QueueItem:
    """队列中的一个消息单元。

    Worker 所需上下文一次性传入。
    - agent_loop / workspace_name 已移除（Worker 侧解析）
    - channel 供 Worker 调用 respond()
    """

    ctx: MessageContext
    session_id: str
    channel: Any  # ChannelPlugin 实例
    traceid: str
    execution_mode: Literal["react", "plan"] = "react"
    received_at_ms: int = 0  # 消息入队时间戳（毫秒）


class SessionQueue:
    """Per-session 消息队列（纯队列容器，无 AgentLoop 知识）。

    仅提供 asyncio.Queue 的薄封装 + drain shutdown。
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._queue: asyncio.Queue[QueueItem] = asyncio.Queue()

    async def put(self, item: QueueItem) -> None:
        await self._queue.put(item)

    async def get(self) -> QueueItem:
        """从队列中取出一个 item。如果队列为空，阻塞等待。"""
        return await self._queue.get()

    def task_done(self) -> None:
        """标记一个 item 已被处理完毕。"""
        self._queue.task_done()

    def qsize(self) -> int:
        return self._queue.qsize()

    async def shutdown(self) -> None:
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

    def get_status(self) -> dict:
        return {"queue_size": self._queue.qsize()}


# ── 模块级状态（取代 SessionQueueManager.__init__ 里的 self.xxx） ──
_queues: dict[str, SessionQueue] = {}
_workers: dict[str, asyncio.Task] = {}
_lock = asyncio.Lock()
_loop_factory: Optional[Callable] = None


def _ensure_worker(session_id: str, sq: SessionQueue) -> None:
    """如果 Worker 未启动或已结束，启动新 Worker。"""
    if session_id not in _workers or _workers[session_id].done():
        name = f"session-wk-{session_id[:8]}"
        _workers[session_id] = asyncio.create_task(_run_session_worker(session_id, sq), name=name)


async def _run_session_worker(session_id: str, sq: SessionQueue) -> None:
    """Worker 主循环：串行消费队列，处理后通过 channel.respond() 发送。"""
    while True:
        try:
            item: QueueItem = await sq.get()
        except asyncio.CancelledError:
            break

        try:
            set_traceid(item.traceid)

            workspace_dir = item.ctx.workspace_dir
            if workspace_dir is None:
                raise RuntimeError("workspace_dir is None")
            _ws_token = current_workspace.set(workspace_dir)

            try:
                agent_loop = await _resolve_loop(item)
                if agent_loop is None:
                    raise RuntimeError(f"Cannot resolve AgentLoop for session={item.session_id}")

                logger.info("Session worker processing message session=%s", session_id[:8])
                response = await agent_loop.run(
                    item.ctx.content,
                    trace_id=item.traceid,
                    session_id=item.session_id,
                    execution_mode=item.execution_mode,
                    received_at_ms=item.received_at_ms,
                )

                thinking_parts = getattr(agent_loop, "last_thinking_parts", [])

                model_name = getattr(getattr(agent_loop, "llm", None), "model", "unknown")
                usage = getattr(agent_loop, "accumulated_usage", None)
                tokens = usage["total_tokens"] if usage else 0
                balance = None
                try:
                    balance = await agent_loop.get_balance()
                except Exception:
                    pass
                footer = item.channel.build_footer(
                    workspace_name=workspace_dir.name,
                    model_name=model_name,
                    tokens=tokens,
                    balance=balance,
                    traceid=item.traceid,
                )

                dr = DispatchResult(
                    thinking_parts=thinking_parts,
                    response=response,
                    footer=footer,
                    session_id=item.session_id,
                    traceid=item.traceid,
                )

                await item.channel.respond(item.ctx, dr)

            finally:
                current_workspace.reset(_ws_token)

        except Exception as e:
            logger.error(f"session queue worker error: session={item.session_id} err={e}")
            traceback.print_exc()
            error_dr = DispatchResult(
                error=f"{type(e).__name__}: {e}",
                session_id=item.session_id,
                traceid=item.traceid,
            )
            try:
                await item.channel.respond(item.ctx, error_dr)
            except Exception:
                pass
        finally:
            sq.task_done()


async def _resolve_loop(item: QueueItem):
    """通过工厂获取 AgentLoop 实例。"""
    if _loop_factory is None:
        logger.error("SessionQueueManager has no loop_factory set")
        return None
    return await _loop_factory(item.session_id, item.ctx.workspace_dir)


async def enqueue(session_id: str, item: QueueItem) -> None:
    """入队消息并确保 Worker 在运行。不再阻塞等待结果。"""
    async with _lock:
        if session_id not in _queues:
            _queues[session_id] = SessionQueue(session_id)
        sq = _queues[session_id]
    await sq.put(item)
    _ensure_worker(session_id, sq)


def set_loop_factory(factory: Optional[Callable]) -> None:
    """设置 AgentLoop 工厂/解析器。"""
    global _loop_factory
    _loop_factory = factory


def get_all_status() -> dict[str, dict]:
    """返回所有活跃 session 的状态。"""
    status = {}
    for sid, sq in list(_queues.items()):
        worker = _workers.get(sid)
        status[sid] = {
            "queue_size": sq.qsize(),
            "processing": worker is not None and not worker.done(),
        }
    return status


async def shutdown_all() -> None:
    """关闭所有 SessionQueue Worker 并清空队列。"""
    for sid, worker in list(_workers.items()):
        if not worker.done():
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
    _workers.clear()
    for sq in list(_queues.values()):
        try:
            await sq.shutdown()
        except Exception:
            logger.warning(f"shutdown_all error for session {sq.session_id}", exc_info=True)
    _queues.clear()


def _reset() -> None:
    """清除所有 Worker 和队列（仅测试用）。"""
    for sid, worker in list(_workers.items()):
        if not worker.done():
            worker.cancel()
    _workers.clear()
    _queues.clear()
