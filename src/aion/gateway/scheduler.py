"""Channel 运行时调度器

为每个 Channel 创建独立线程 + asyncio EventLoop，实现线程隔离。
SystemScheduler 是系统级调度循环，管理所有 Channel 生命周期。
"""

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import Optional

from ..channels.adapters import ChannelPlugin
from .session_queue import shutdown_all

logger = logging.getLogger(__name__)

_CHANNEL_THREAD_JOIN_TIMEOUT = 8
_HEALTH_CHECK_INTERVAL = 30
_MAX_RECONNECT_RETRIES = 5


@dataclass
class ChannelRuntime:
    """封装单个 Channel 的独立线程+asyncio 事件循环"""

    channel_id: str
    channel: ChannelPlugin
    scheduler_loop: asyncio.AbstractEventLoop
    thread: Optional[threading.Thread] = None
    loop: Optional[asyncio.AbstractEventLoop] = None
    status: str = "created"  # created | running | stopped | failed
    start_count: int = 0

    def start(self) -> None:
        """启动独立线程，在该线程上创建 asyncio loop 并执行 channel.start()"""
        if self.status == "running":
            return
        self.status = "starting"
        self.thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"ch-{self.channel_id}",
        )
        self.thread.start()

    def _run(self) -> None:
        """线程主函数：创建 loop  start channel  run_forever"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self.channel.start())
            self.status = "running"
            self.start_count += 1
            logger.info(f"Channel {self.channel_id} started (attempt #{self.start_count})")
            self.loop.run_forever()
        except Exception as e:
            logger.error(f"Channel {self.channel_id} error: {e}", exc_info=True)
            self.status = "failed"
        finally:
            try:
                self.loop.close()
            except Exception:
                pass
            self.loop = None

    async def stop(self) -> None:
        """停止 channel  停止 loop  join 线程"""
        if self.status == "stopped" or self.loop is None or self.thread is None:
            return
        self.status = "stopped"
        if self.loop and not self.loop.is_closed():
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self.channel.stop(),
                    self.loop,
                )
                future.result(timeout=10)
            except Exception as e:
                logger.warning(f"Error stopping channel {self.channel_id}: {e}")
            try:
                self.loop.call_soon_threadsafe(self.loop.stop)
            except Exception:
                pass
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=_CHANNEL_THREAD_JOIN_TIMEOUT)
        self.thread = None
        self.loop = None

    def is_alive(self) -> bool:
        """检查线程存活且 loop 正常"""
        return (
            self.thread is not None and self.thread.is_alive() and self.loop is not None and not self.loop.is_closed()
        )

    def get_status(self) -> dict:
        return {
            "channel_id": self.channel_id,
            "status": self.status,
            "alive": self.is_alive(),
            "start_count": self.start_count,
        }


class ChannelLifecycleManager:
    """管理所有 ChannelRuntime 的生命周期"""

    def __init__(self, scheduler_loop: asyncio.AbstractEventLoop):
        self._scheduler_loop = scheduler_loop
        self._runtimes: dict[str, ChannelRuntime] = {}

    def add_runtime(self, runtime: ChannelRuntime) -> None:
        self._runtimes[runtime.channel_id] = runtime

    def get_runtime(self, channel_id: str) -> Optional[ChannelRuntime]:
        return self._runtimes.get(channel_id)

    def get_all_runtimes(self) -> dict[str, ChannelRuntime]:
        return dict(self._runtimes)

    def start_channel(self, channel_id: str) -> None:
        runtime = self._runtimes.get(channel_id)
        if runtime:
            runtime.start()

    async def stop_channel(self, channel_id: str) -> None:
        runtime = self._runtimes.get(channel_id)
        if runtime:
            await runtime.stop()

    async def stop_all(self) -> None:
        for runtime in self._runtimes.values():
            try:
                await runtime.stop()
            except Exception:
                logger.warning(f"Error stopping {runtime.channel_id}", exc_info=True)

    async def health_check(self) -> list[str]:
        """返回不存活的 running channel 列表"""
        failed = []
        for cid, rt in self._runtimes.items():
            if rt.status == "running" and not rt.is_alive():
                failed.append(cid)
        return failed

    async def reconnect(self, channel_id: str) -> bool:
        runtime = self._runtimes.get(channel_id)
        if not runtime:
            return False
        if runtime.start_count >= _MAX_RECONNECT_RETRIES:
            logger.error(f"Channel {channel_id} exceeded max reconnect retries ({_MAX_RECONNECT_RETRIES})")
            return False
        await runtime.stop()
        # 重置状态
        runtime.status = "created"
        runtime.start()
        return True

    def get_all_status(self) -> dict[str, dict]:
        return {cid: rt.get_status() for cid, rt in self._runtimes.items()}


class SystemScheduler:
    """系统调度循环

    运行在独立线程的 asyncio EventLoop 上，负责：
    - 管理所有 Channel 的生命周期（通过 ChannelLifecycleManager）
    - 管理 SessionQueue Worker（在此 loop 上运行）
    - 定期健康检查和自动重连
    - 协调优雅关闭
    """

    def __init__(self, gateway=None):
        self._gateway = gateway
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lifecycle: Optional[ChannelLifecycleManager] = None
        self._shutdown = False
        self._health_check_task = None

    def start(self) -> None:
        """启动调度线程（非阻塞，等待 loop 就绪后返回）"""
        self._thread = threading.Thread(target=self._run, daemon=True, name="scheduler")
        self._thread.start()
        # 等待 loop 和 lifecycle 就绪（最多 5s）
        import time

        deadline = time.monotonic() + 5
        while (self._loop is None or self._lifecycle is None) and time.monotonic() < deadline:
            time.sleep(0.01)

    def _run(self) -> None:
        """调度线程主函数"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_init())
            self._health_check_task = self._loop.create_task(self._health_check_loop())
            self._loop.run_forever()
        except Exception as e:
            logger.error(f"Scheduler error: {e}", exc_info=True)
        finally:
            try:
                self._loop.close()
            except Exception:
                pass
            self._loop = None

    async def _async_init(self) -> None:
        """在 scheduler loop 上初始化异步组件"""
        assert self._loop is not None, "Event loop not initialized"
        self._lifecycle = ChannelLifecycleManager(self._loop)

    def register_channel(self, channel_id: str, channel: ChannelPlugin) -> None:
        """注册 Channel 到生命周期管理器"""
        if not self._lifecycle:
            raise RuntimeError("Scheduler not initialized")
        assert self._loop is not None, "Event loop not initialized"
        runtime = ChannelRuntime(channel_id, channel, self._loop)
        self._lifecycle.add_runtime(runtime)

    def start_channel(self, channel_id: str) -> None:
        """启动指定 Channel"""
        if not self._lifecycle:
            raise RuntimeError("Scheduler not initialized")
        self._lifecycle.start_channel(channel_id)

    async def _health_check_loop(self) -> None:
        """定期健康检查（每 30 秒）"""
        while not self._shutdown:
            await asyncio.sleep(_HEALTH_CHECK_INTERVAL)
            if not self._lifecycle:
                continue
            failed = await self._lifecycle.health_check()
            for channel_id in failed:
                logger.info(f"Reconnecting channel: {channel_id}")
                await self._lifecycle.reconnect(channel_id)

    def stop(self) -> None:
        """优雅关闭"""
        if self._shutdown:
            return
        self._shutdown = True
        if self._loop and self._loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._async_shutdown(),
                    self._loop,
                )
                future.result(timeout=30)
            except Exception as e:
                logger.warning(f"Scheduler shutdown error: {e}")
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=15)

    async def _async_shutdown(self) -> None:
        """异步关闭：停止所有 Channel  清空 SessionQueue"""
        # 取消健康检查任务
        if self._health_check_task and not self._health_check_task.done():
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
        if self._lifecycle:
            await self._lifecycle.stop_all()
        await shutdown_all()

    @property
    def loop(self) -> Optional[asyncio.AbstractEventLoop]:
        return self._loop

    def get_all_channel_status(self) -> dict:
        if self._lifecycle:
            return self._lifecycle.get_all_status()
        return {}
