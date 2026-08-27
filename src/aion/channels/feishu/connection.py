"""飞书连接管理（WebSocket + Webhook）

设计文档: docs/design/feishu-channel.md 第 3 节
"""

import asyncio
import inspect
import json
import logging
import os
import http.server
import socketserver
import threading
from typing import Optional, Callable, Any

from .config import FeishuAccountConfig

logger = logging.getLogger(__name__)

WS_RECONNECT_INITIAL_DELAY = 5
WS_RECONNECT_MAX_DELAY = 60
WS_THREAD_JOIN_TIMEOUT = 2


class FeishuWebhookHandler(http.server.BaseHTTPRequestHandler):
    """飞书 Webhook 请求处理器"""

    def do_POST(self):
        """处理飞书 Webhook POST 请求

        设计要点：
        - 先解析 payload、处理 challenge（同步）
        - 然后立即返回 HTTP 200（让飞书显示 ✓ ACK 确认图标）
        - 最后在后台异步执行实际事件处理

        参考设计文档：plans/feat-feishu-ack/design.md

        Returns:
            None
        """
        handler = self.server.feishu_handler
        if handler is None:
            self.send_error(500, "Handler not configured")
            return

        # 读取请求体
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length)

        # 解析 JSON
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Invalid JSON")
            return

        # 处理 challenge（飞书验证 Webhook URL 合法性）
        if self._handle_challenge(payload):
            return  # challenge 响应已完成

        # ★ 立即返回 200，让飞书显示 ACK 确认图标
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"code":0}')
        self.wfile.flush()

        # ★ 启动后台任务处理实际消息（不阻塞 HTTP 响应）
        asyncio.create_task(self._background_handle_event(handler, payload))

    async def _background_handle_event(self, handler: "WebhookEventHandler", payload: dict):
        """后台异步处理飞书事件

        异常处理：所有异常被捕获并记录，不向飞书发送额外响应。
        （因为 ACK 已发出，飞书不会重试）

        Args:
            handler: Webhook 事件处理器
            payload: 飞书事件 JSON

        Returns:
            None
        """
        try:
            # 发送 Typing Indicator（消息已入队列/处理流程，代表 aion 正式接收）
            from .message import extract_message_id, extract_chat_id

            message_id = extract_message_id(payload)
            chat_id = extract_chat_id(payload)
            if message_id and chat_id:
                from .client import add_typing_indicator

                add_typing_indicator(message_id, chat_id)
            await handler.handle_event(payload)
        except Exception as e:
            logger.error(f"[_background_handle_event] event handle error: {e}")

    def _handle_challenge(self, payload: dict) -> bool:
        """处理飞书挑战验证（URL 校验）

        Args:
            payload: 含 challenge 字段的请求体

        Returns:
            bool: 已处理 challenge 并响应时返回 True，否则 False
        """
        # 挑战响应是飞书验证 Webhook URL 合法性的机制
        challenge = payload.get("challenge")
        if challenge:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"challenge": challenge}).encode())
            return True
        return False

    def log_message(self, format, *args):
        """自定义 HTTP 访问日志格式

        Args:
            format: 日志格式字符串
            *args: 格式参数

        Returns:
            None
        """
        logger.info(f"{self.address_string()} - {format % args}")


class FeishuWebhookServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """飞书 Webhook 服务器

    多线程 HTTP 服务，将 feishu_handler 注入到请求处理器。
    """

    allow_reuse_address = True  # 允许端口复用
    daemon_threads = True  # 工作线程随主进程退出

    def __init__(
        self,
        host: str,
        port: int,
        handler: Optional["WebhookEventHandler"] = None,
    ):
        """创建 Webhook 服务器

        Args:
            host: 监听地址
            port: 监听端口
            handler: Webhook 事件处理器，供 FeishuWebhookHandler 调用
        """
        self.feishu_handler = handler
        super().__init__((host, port), FeishuWebhookHandler)


class WebhookEventHandler:
    """Webhook 事件处理器

    校验配置有效性并将事件回调转发给上层 handler。
    """

    def __init__(
        self,
        config: FeishuAccountConfig,
        event_callback: Callable[[dict], Any],
    ):
        """初始化 Webhook 处理器

        Args:
            config: 飞书账号配置
            event_callback: 收到合法事件后的异步/同步回调

        Raises:
            ValueError: Webhook 模式缺少 encryptKey 或 verificationToken
        """
        self.config = config
        self.event_callback = event_callback

        if config.connectionMode == "webhook":
            if not config.encryptKey:
                raise ValueError("Webhook mode requires encryptKey")
            if not config.verificationToken:
                raise ValueError("Webhook mode requires verificationToken")

    async def handle_event(self, payload: dict) -> None:
        """处理接收到的飞书事件

        Args:
            payload: 飞书事件 JSON

        Returns:
            None
        """
        try:
            await self.event_callback(payload)
        except Exception as e:
            logger.error(f"Error handling webhook event: {e}")


class ConnectionManager:
    """飞书连接管理器

    统一管理 WebSocket 和 Webhook 两种连接模式。
    使用线程而非 asyncio Task，避免 SIGINT 无法打断阻塞调用的问题。
    """

    def __init__(self, config: FeishuAccountConfig):
        """初始化连接管理器

        Args:
            config: 飞书账号配置（含 connectionMode 等）
        """
        self.config = config
        self._webhook_server: Optional[FeishuWebhookServer] = None
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None  # WS 线程事件循环
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None  # Gateway 主循环
        self._running = False
        self._event_handler: Optional[Callable[[dict], Any]] = None  # 事件分发回调
        self._shutdown_event = threading.Event()  # 线程间停止信号
        self._ws_thread: Optional[threading.Thread] = None  # WS 重连线程

    def _schedule_event_dispatch(self, event_payload: dict) -> None:
        """从 WS 线程把事件投递到 Gateway 所在的事件循环（不得在此线程 asyncio.run）。

        Args:
            event_payload: 标准化后的事件字典

        Returns:
            None
        """
        if not self._event_handler:
            return
        loop = self._main_loop
        if loop is None or not loop.is_running():
            logger.warning("[WSClient] main asyncio loop unavailable, event dropped")
            return

        def _log_result(fut: asyncio.Future) -> None:
            """记录跨线程投递任务的异常

            Args:
                fut: run_coroutine_threadsafe 返回的 Future

            Returns:
                None
            """
            try:
                fut.result()
            except Exception as e:
                logger.error(f"Error in event dispatch: {e}", exc_info=True)

        fut = asyncio.run_coroutine_threadsafe(
            self._dispatch_event(self._event_handler, event_payload),
            loop,
        )
        fut.add_done_callback(_log_result)  # type: ignore[arg-type]

    def _run_ws_once(self) -> bool:
        """在本 WS 线程内运行 lark_oapi 长连接（单线程 + 独立事件循环）。

        lark_oapi.ws.client 在 import 时绑定全局 loop；主进程已启动 asyncio 时
        该全局 loop 会指向错误对象。此处为当前线程新建 loop 并覆盖模块级 loop。

        Returns:
            True: 连接正常结束（对端关闭等）
            False: 异常退出或被打断
        """
        os.environ.setdefault("NO_PROXY", "*")
        from .client import resolve_domain

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._ws_loop = loop  # 保存引用，供 disconnect() 快速中断
        try:
            import lark_oapi.ws.client as lark_ws_mod

            lark_ws_mod.loop = loop

            import lark_oapi as lark

            # Patch ExpiringCache.__del__：loop 已关闭时静默忽略 RuntimeError。
            # SDK 的 cron 回调在模块清理时使用已关闭 loop 抛异常，属纯外观问题。
            try:
                from lark_oapi.core.cache.expiring_cache import ExpiringCache as _EC

                if not getattr(_EC.__del__, "_patched", False):
                    _orig = _EC.__del__

                    def _safe_del(self):
                        try:
                            _orig(self)
                        except RuntimeError:
                            pass

                    setattr(_safe_del, "_patched", True)
                    _EC.__del__ = _safe_del
            except Exception:
                pass

            def do_p2_im_message_receive_v1(data: lark.im.v1.P2ImMessageReceiveV1) -> None:
                """SDK 回调：收到 im.message.receive_v1 时序列化并投递到主循环

                Args:
                    data: lark-oapi P2 消息事件对象

                Returns:
                    None
                """
                try:
                    event_json = lark.JSON.marshal(data)
                    event_payload = {
                        "event_type": "im.message.receive_v1",
                        "payload": event_json,
                        "schema": "p2",
                    }
                    self._schedule_event_dispatch(event_payload)
                except Exception as e:
                    logger.error(f"Error handling message event: {e}")

            event_handler = (
                lark.EventDispatcherHandler.builder("", "")
                .register_p2_im_message_receive_v1(do_p2_im_message_receive_v1)
                .build()
            )

            ws_client = lark_ws_mod.Client(
                app_id=self.config.appId,
                app_secret=self.config.appSecret,
                event_handler=event_handler,
                log_level=lark.LogLevel.DEBUG,
                domain=resolve_domain(self.config.domain),
            )
            ws_client.start()
            logger.info("[_run_ws_once] WS client session ended normally")
            return True
        except Exception as e:
            if self._shutdown_event.is_set():
                # 关闭过程中 loop.stop() 导致 ws_client.start() 中断，属正常行为
                logger.info(f"[WSClient] WS client interrupted during shutdown: {e}")
            else:
                logger.error(f"[WSClient] WS client error: {e}", exc_info=True)
            return False
        finally:
            # 先释放 ws_client（如存在），让 SDK 在 loop 还活着时完成内部清理
            try:
                del ws_client
            except (NameError, UnboundLocalError):
                pass
            # 切断模块级 loop 引用（同上原因）
            try:
                import lark_oapi.ws.client as lark_ws_mod

                if lark_ws_mod.loop is loop:
                    lark_ws_mod.loop = None
            except Exception:
                pass
            # 取消 loop 上残留的 task（如 lark_oapi 的 _receive_message_loop），
            # 避免协程在 GC 时因未 await 完成而报 RuntimeError: coroutine ignored GeneratorExit
            try:
                for task in asyncio.all_tasks(loop):
                    task.cancel()
            except Exception:
                pass
            try:
                if loop.is_running():
                    loop.stop()
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass

    def _ws_reconnect_loop(self) -> None:
        """WS 重连循环（运行在独立线程中）

        连接断开后指数退避重连，直到收到 shutdown 信号。

        Returns:
            None
        """
        reconnect_delay = WS_RECONNECT_INITIAL_DELAY

        while not self._shutdown_event.is_set():
            try:
                success = self._run_ws_once()
                if success:
                    # 正常断开：重置退避间隔
                    reconnect_delay = WS_RECONNECT_INITIAL_DELAY
                else:
                    # 异常退出：指数退避后重连（上限 60 秒）
                    if self._shutdown_event.wait(timeout=reconnect_delay):
                        break
                    reconnect_delay = min(reconnect_delay * 2, WS_RECONNECT_MAX_DELAY)
            except Exception as e:
                logger.error(f"[_ws_reconnect_loop] error: {e}")
                if self._shutdown_event.wait(timeout=reconnect_delay):
                    break
                reconnect_delay = min(reconnect_delay * 2, WS_RECONNECT_MAX_DELAY)

        logger.info("[_ws_reconnect_loop] exited")

    def connect_websocket(
        self,
        event_dispatcher: Any,  # noqa: ARG002 保留参数，当前由 event_handler 处理
        event_handler: Callable[[dict], Any],
        main_loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> None:
        """通过 WebSocket 连接飞书，自动重连（在独立线程中运行）

        Args:
            event_handler: 事件处理回调
            main_loop: Gateway 主 asyncio 循环，用于跨线程投递
        """
        self._main_loop = main_loop
        self._event_handler = event_handler
        self._shutdown_event.clear()

        self._ws_thread = threading.Thread(target=self._ws_reconnect_loop, daemon=True)
        self._ws_thread.start()

    async def _dispatch_event(self, event_handler: Callable, event_data: dict):
        """在 Gateway 主事件循环中分发事件（支持 sync/async handler）

        Args:
            event_handler: 同步或异步事件处理函数
            event_data: 事件数据

        Returns:
            None
        """
        try:
            # logger.debug(f"[_dispatch_event] calling handler with event_type={event_data.get('event_type', 'unknown')}")
            if inspect.iscoroutinefunction(event_handler):
                await event_handler(event_data)
            else:
                event_handler(event_data)
            # logger.debug(f"[_dispatch_event] handler completed")
        except Exception as e:
            logger.error(f"Error in event dispatch: {e}")

    async def connect_webhook(
        self,
        event_handler: Callable[[dict], Any],
    ) -> None:
        """通过 Webhook 连接飞书

        Args:
            event_handler: 事件处理回调

        Returns:
            None
        """
        logger.info(f"Starting Feishu Webhook server on {self.config.webhookHost}:{self.config.webhookPort}")

        handler = WebhookEventHandler(self.config, event_handler)
        self._webhook_server = FeishuWebhookServer(
            host=self.config.webhookHost,
            port=self.config.webhookPort,
            handler=handler,
        )

        # 启动服务器（在独立线程中）
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            self._webhook_server.serve_forever,
        )

        logger.info("Feishu Webhook server started")

    def disconnect(self) -> None:
        """断开连接（设置停止信号，等待线程结束）

        Returns:
            None
        """
        self._running = False
        self._event_handler = None

        # 设置停止信号，让重连循环退出
        self._shutdown_event.set()

        # 停止 WS 线程的 asyncio 循环，立即中断 ws_client.start()
        if hasattr(self, "_ws_loop") and self._ws_loop and not self._ws_loop.is_closed():
            try:
                self._ws_loop.call_soon_threadsafe(self._ws_loop.stop)
            except Exception:
                pass

        # 等待重连线程结束
        if self._ws_thread and self._ws_thread.is_alive():
            self._ws_thread.join(timeout=WS_THREAD_JOIN_TIMEOUT)
            if self._ws_thread.is_alive():
                logger.warning("[disconnect] WS thread did not exit in time")

        if self._webhook_server:
            self._webhook_server.shutdown()
            self._webhook_server = None

        logger.info("Feishu connection closed")

    @property
    def is_connected(self) -> bool:
        """是否已连接

        Returns:
            bool: WS 线程存活或 Webhook 服务已启动时为 True
        """
        ws_alive = self._ws_thread is not None and self._ws_thread.is_alive()
        return ws_alive or self._webhook_server is not None
