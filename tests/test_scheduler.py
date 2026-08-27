"""tests/test_scheduler.py"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.aion.gateway.scheduler import ChannelRuntime, SystemScheduler


@pytest.mark.asyncio
async def test_channel_runtime_start_stop():
    """验证 ChannelRuntime 启动后创建线程+loop，停止后清理"""
    mock_channel = MagicMock()
    mock_channel.channel_id = "test"
    mock_channel.start = AsyncMock()
    mock_channel.stop = AsyncMock()
    mock_channel.is_running = MagicMock(return_value=True)

    scheduler_loop = asyncio.get_running_loop()
    runtime = ChannelRuntime("test", mock_channel, scheduler_loop)

    assert runtime.status == "created"
    runtime.start()

    # 等待线程启动
    import time

    time.sleep(0.1)

    assert runtime.thread is not None
    assert runtime.thread.is_alive()
    assert runtime.loop is not None
    assert not runtime.loop.is_closed()
    assert runtime.is_alive()
    mock_channel.start.assert_awaited_once()

    await runtime.stop()
    assert runtime.status == "stopped"
    assert runtime.thread is None or not runtime.thread.is_alive()


@pytest.mark.asyncio
async def test_system_scheduler_start_stop():
    """验证 SystemScheduler 启动后 loop 可用，停止后线程退出"""
    scheduler = SystemScheduler()
    scheduler.start()
    assert scheduler.loop is not None
    assert not scheduler.loop.is_closed()
    assert scheduler._thread is not None
    assert scheduler._thread.is_alive()

    scheduler.stop()
    # 线程应已退出
    if scheduler._thread:
        assert not scheduler._thread.is_alive()
    # lifecycle 应已清理
    assert scheduler._lifecycle is not None or scheduler._shutdown


@pytest.mark.asyncio
async def test_system_scheduler_channel_lifecycle():
    """验证通过 scheduler 注册和启动 Channel 正常工作"""
    scheduler = SystemScheduler()
    scheduler.start()

    mock_channel = MagicMock()
    mock_channel.channel_id = "test"
    mock_channel.start = AsyncMock()
    mock_channel.stop = AsyncMock()
    mock_channel.is_running = MagicMock(return_value=True)

    scheduler.register_channel("test", mock_channel)
    scheduler.start_channel("test")

    import time

    time.sleep(0.2)

    status = scheduler.get_all_channel_status()
    assert "test" in status
    assert status["test"]["alive"] is True

    scheduler.stop()


import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.mark.asyncio
async def test_scheduler_full_lifecycle():
    """验证完整生命周期：scheduler 启动 → channel 注册 → 健康检查 → 停止"""
    scheduler = SystemScheduler()
    scheduler.start()
    assert scheduler.loop is not None

    # 模拟一个 channel
    mock_channel = MagicMock()
    mock_channel.channel_id = "test-ch"
    mock_channel.start = AsyncMock()
    mock_channel.stop = AsyncMock()
    mock_channel.is_running = MagicMock(return_value=True)

    # 注册和启动
    scheduler.register_channel("test-ch", mock_channel)
    scheduler.start_channel("test-ch")

    import time

    time.sleep(0.3)  # 等待 channel 线程启动

    # 验证 channel 已启动
    status = scheduler.get_all_channel_status()
    assert "test-ch" in status
    assert status["test-ch"]["alive"] is True
    assert status["test-ch"]["status"] == "running"
    mock_channel.start.assert_awaited_once()

    # 停止
    scheduler.stop()

    # 验证清理
    time.sleep(0.1)
    assert scheduler._loop is None or scheduler._loop.is_closed()


@pytest.mark.asyncio
async def test_scheduler_reconnect():
    """验证健康检查发现 channel 失败后自动重连"""
    scheduler = SystemScheduler()
    scheduler.start()
    assert scheduler.loop is not None

    mock_channel = MagicMock()
    mock_channel.channel_id = "flaky-ch"
    mock_channel.start = AsyncMock()
    mock_channel.stop = AsyncMock()
    mock_channel.is_running = MagicMock(return_value=True)

    scheduler.register_channel("flaky-ch", mock_channel)
    scheduler.start_channel("flaky-ch")

    import time

    time.sleep(0.2)

    # 模拟 channel 线程退出（模拟 crash）
    runtime = scheduler._lifecycle.get_runtime("flaky-ch")
    assert runtime is not None
    runtime.thread = None
    runtime.loop = None

    # 触发健康检查
    failed = await scheduler._lifecycle.health_check()
    assert "flaky-ch" in failed

    # 重连
    ok = await scheduler._lifecycle.reconnect("flaky-ch")
    assert ok is True

    time.sleep(0.2)
    # 新线程已启动
    assert runtime.thread is not None
    assert runtime.thread.is_alive()
    assert runtime.status == "running" or runtime.status == "starting"

    scheduler.stop()
