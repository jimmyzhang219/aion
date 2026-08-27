"""SessionQueue 单元测试 — 验证纯队列行为，无 AgentLoop 耦合"""

import pytest
from aion.gateway.session_queue import SessionQueue, QueueItem, enqueue, get_all_status, shutdown_all, _reset, set_loop_factory
from aion.channels.types import MessageContext


@pytest.fixture(autouse=True)
def reset_session_queue():
    yield
    _reset()


@pytest.mark.asyncio
async def test_session_queue_put_get():
    """put 后能通过 get 取出相同的 item"""
    q = SessionQueue("test-session")
    ctx = MessageContext(channel_id="test", chat_id="c1", message_id="m1", sender_id="u1", workspace_dir=None)
    item = QueueItem(ctx=ctx, session_id="test-session", channel=None, traceid="trace1")
    await q.put(item)
    got = await q.get()
    assert got is item
    q.task_done()


@pytest.mark.asyncio
async def test_session_queue_qsize():
    """qsize 初始为 0，put 后为 1"""
    q = SessionQueue("test-session")
    assert q.qsize() == 0
    ctx = MessageContext(channel_id="test", chat_id="c1", message_id="m1", sender_id="u1", workspace_dir=None)
    await q.put(QueueItem(ctx=ctx, session_id="s1", channel=None, traceid="t1"))
    assert q.qsize() == 1


@pytest.mark.asyncio
async def test_session_queue_shutdown_drains():
    """shutdown 应清空队列中的所有 item"""
    q = SessionQueue("test-session")
    ctx = MessageContext(channel_id="test", chat_id="c1", message_id="m1", sender_id="u1", workspace_dir=None)
    await q.put(QueueItem(ctx=ctx, session_id="s1", channel=None, traceid="t1"))
    await q.put(QueueItem(ctx=ctx, session_id="s1", channel=None, traceid="t2"))
    assert q.qsize() == 2
    await q.shutdown()
    assert q.qsize() == 0


@pytest.mark.asyncio
async def test_enqueue_creates_queue():
    """enqueue 后 queue 应存在且包含 item"""
    ctx = MessageContext(channel_id="test", chat_id="c1", message_id="m1", sender_id="u1", workspace_dir=None)
    item = QueueItem(ctx=ctx, session_id="s1", channel=None, traceid="t1")
    await enqueue("s1", item)
    status = get_all_status()
    assert "s1" in status
    assert status["s1"]["queue_size"] == 1


@pytest.mark.asyncio
async def test_enqueue_same_session():
    """同一 session 的多次 enqueue 共享同一个 SessionQueue"""
    ctx = MessageContext(channel_id="test", chat_id="c1", message_id="m1", sender_id="u1", workspace_dir=None)
    await enqueue("s1", QueueItem(ctx=ctx, session_id="s1", channel=None, traceid="t1"))
    await enqueue("s1", QueueItem(ctx=ctx, session_id="s1", channel=None, traceid="t2"))
    status = get_all_status()
    assert status["s1"]["queue_size"] == 2


@pytest.mark.asyncio
async def test_worker_lifecycle():
    """enqueue 后应创建 worker，shutdown_all 后 worker 应停止"""
    from aion.gateway.session_queue import _workers, _queues

    assert len(_workers) == 0
    ctx = MessageContext(channel_id="test", chat_id="c1", message_id="m1", sender_id="u1", workspace_dir=None)
    await enqueue("s1", QueueItem(ctx=ctx, session_id="s1", channel=None, traceid="t1"))
    assert len(_workers) == 1
    assert not _workers["s1"].done()
    await shutdown_all()
    assert len(_workers) == 0
    assert len(_queues) == 0


@pytest.mark.asyncio
async def test_get_all_status():
    """get_all_status 返回正确的 queue_size 和 processing 状态"""
    ctx = MessageContext(channel_id="test", chat_id="c1", message_id="m1", sender_id="u1", workspace_dir=None)
    await enqueue("s1", QueueItem(ctx=ctx, session_id="s1", channel=None, traceid="t1"))
    status = get_all_status()
    assert "s1" in status
    assert "queue_size" in status["s1"]
    assert "processing" in status["s1"]


@pytest.mark.asyncio
async def test_set_loop_factory():
    """set_loop_factory 设置工厂后 enqueue 正常工作"""

    async def fake_factory(sid, wd):
        return None

    set_loop_factory(fake_factory)
    ctx = MessageContext(channel_id="test", chat_id="c1", message_id="m1", sender_id="u1", workspace_dir=None)
    await enqueue("s1", QueueItem(ctx=ctx, session_id="s1", channel=None, traceid="t1"))
    status = get_all_status()
    assert status["s1"]["queue_size"] == 1
