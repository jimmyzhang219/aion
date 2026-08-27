"""Gateway HTTP Server

多工作空间配置 v4：
- models: 直接是模型字典
- workspaces: dict 格式
- agents: leader 是字符串引用 + 具体 agent 配置
- log_level: 全局日志级别

架构：
- Gateway 是纯路由层，不知道具体 Channel 实现细节
- 通过 ChannelPlugin 接口管理所有 Channel
- Channel 负责协议转换和格式提示
- Agent 处理通用命令逻辑
- HttpChannel 由调用方（CLI）注入，Gateway 不直接 import 具体 channel 实现
"""

import asyncio
import importlib
import json
import os
import signal
import threading
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from types import ModuleType
from typing import TYPE_CHECKING, Optional
from urllib.parse import urlparse

from .scheduler import SystemScheduler
from .session_queue import get_all_status
from ..channels import (
    MessageContext,
    get_channel_registry,
)
from ..channels.adapters import ChannelPlugin
from ..config.loader import load_config, resolve_workspace_dir
from ..core.constants import DEFAULT_WORKSPACES_DIR
from ..observability import Tracer
from ..session.manager import SessionLister

_CHANNEL_THREAD_JOIN_TIMEOUT = 8  # 保留用于 ChannelRuntime.join
_PS_TIMEOUT = 2
_SHUTDOWN_FALLBACK_TIMEOUT = 15

if TYPE_CHECKING:
    pass

from ..log import get_trace_logger

websockets: Optional[ModuleType] = None
try:
    import websockets  # noqa: F811
except ImportError:
    pass

logger = get_trace_logger(__name__)


class GatewayServer:
    """Gateway HTTP Server

    提供 HTTP API 供外部调用，支持：
    - POST /: 发送消息给 Agent
    - GET /sessions: 列出历史 Session

    同时管理所有 Channel 的启动和消息路由。

    Attributes:
        host: HTTP 服务监听地址
        port: HTTP 服务监听端口
        agent_loop: 默认 AgentLoop 实例
        _server: HTTPServer 实例
        _session_manager: SessionLister 实例
        _channel_registry: Channel 注册中心
        _scheduler: SystemScheduler 实例
    """

    def __init__(self, port: int | None = None, http_channel: ChannelPlugin | None = None):
        """初始化 Gateway

        Args:
            port: HTTP 监听端口，默认从 aion.json 读取，兜底 19527
            http_channel: HttpChannel 实例（由 CLI 层注入，避免 Gateway import 具体 channel 实现）
        """
        self.host = "127.0.0.1"
        self._server: HTTPServer | None = None  # HTTPServer 实例
        self._http_channel = http_channel  # 由调用方注入，用于 do_POST
        # 初始化 SessionLister，从配置读取当前工作空间和 leader agent
        config = load_config()
        self.port = port if port is not None else config.gateway.port
        workspace_name = config.workspaces.current
        workspace_dir = DEFAULT_WORKSPACES_DIR / workspace_name
        # leader agent 的 id 就是 config 中 agents 的 key
        ws_config = config.get_current_workspace()
        agent_id = ws_config.get_leader() if ws_config else "main"
        self._session_manager = SessionLister(workspace_dir=workspace_dir, agent_id=agent_id)
        self._channel_registry = get_channel_registry()
        self._scheduler: SystemScheduler | None = None
        self._shutting_down = False
        self._sigterm_received = False  # signal handler 重入保护（与 _shutting_down 分开，后者由 stop 线程设置）

    async def _start_channels(self):
        """启动所有配置中启用的 Channel（在 scheduler loop 上运行）"""
        config = load_config()

        try:
            Tracer.init(config.langfuse)
        except Exception:
            logger.warning("Tracer.init failed, continuing without observability")
        workspace_name = config.workspaces.current
        workspace_dir = DEFAULT_WORKSPACES_DIR / workspace_name
        for channel_id, ch_cfg in config.channels.items():
            if not ch_cfg.get("enabled"):
                logger.info(f"Channel '{channel_id}' not enabled, skip")
                continue
            try:
                channel = await self._load_channel(
                    channel_id,
                    ch_cfg,
                    workspace_dir,
                )
                channel.set_gateway(self)
                if hasattr(channel, "set_scheduler_loop"):
                    channel.set_scheduler_loop(self._scheduler.loop)
                self._channel_registry.register(channel)
                self._scheduler.register_channel(channel_id, channel)
                self._scheduler.start_channel(channel_id)
                logger.info(f"Channel '{channel_id}' registered with scheduler")
            except Exception as e:
                self._channel_registry.register_failure(channel_id, str(e))
                import traceback

                traceback.print_exc()

    async def _load_channel(self, channel_id: str, ch_cfg: dict, workspace_dir, **kwargs):
        """动态加载 channel 模块并创建实例"""
        module = importlib.import_module(f".channels.{channel_id}", package="aion")
        return await module.create_channel(ch_cfg, workspace_dir=workspace_dir, **kwargs)

    async def _ws_handler(self, websocket):
        """处理 WebSocket 连接 — 每条消息 dispatch 到 Agent。"""
        from ..channels.ws_channel import WebSocketChannel

        channel = WebSocketChannel(websocket)
        async for raw_msg in websocket:
            try:
                data = json.loads(raw_msg)
                message = data.get("message", "")
                if not message.strip():
                    await websocket.send(json.dumps({"type": "error", "content": "empty message"}))
                    continue

                from .dispatch import dispatch_message

                config = load_config()
                workspace_dir = resolve_workspace_dir(config=config)

                ctx = MessageContext(
                    channel_id="ws",
                    chat_id="ws-cli",
                    message_id=f"ws-{uuid.uuid4().hex[:8]}",
                    sender_id="cli",
                    content=message,
                    chat_type="p2p",
                    workspace_dir=workspace_dir,
                )
                await dispatch_message(ctx=ctx, channel=channel)
                # 响应通过 channel.send_message()（即 WebSocket.send）推送
            except Exception as e:
                import traceback

                traceback.print_exc()
                await websocket.send(json.dumps({"type": "error", "content": str(e)}))

    def _start_server(self, blocking: bool = True):
        """启动 Gateway Server（公共逻辑）

        Args:
            blocking: 是否阻塞模式启动（True = 前台，False = 后台）
        """

        # ── 启动 SystemScheduler ──
        self._scheduler = SystemScheduler(gateway=self)
        assert self._scheduler is not None
        self._scheduler.start()

        # ── 异步加载并启动所有 Channel（External Plugins，不阻塞核心启动）──
        async def load_and_start():
            await self._start_channels()

        scheduler_loop = self._scheduler.loop
        assert scheduler_loop is not None
        asyncio.run_coroutine_threadsafe(load_and_start(), scheduler_loop)

        self._server = HTTPServer((self.host, self.port), self._RequestHandler)
        assert self._server is not None
        self._server.session_manager = self._session_manager  # type: ignore[attr-defined]
        self._server.gateway = self  # type: ignore[attr-defined]  # 供 _RequestHandler 访问关闭状态，替代直接属性注入

        # 将注入的 HttpChannel 实例传给 _RequestHandler，避免在 do_POST 中 import 具体 channel
        if self._http_channel is None:
            from ..channels.http import HttpChannel

            self._server.http_adapter = HttpChannel()  # type: ignore[attr-defined]
        else:
            self._server.http_adapter = self._http_channel  # type: ignore[attr-defined]

        import time

        self.start_time = time.time()

        # ── 启动 WebSocket 服务器 ──
        loop = self._scheduler.loop
        if websockets is not None:
            ws_port = self.port + 1

            async def _serve_ws():
                return await websockets.serve(
                    self._ws_handler,
                    self.host,
                    ws_port,
                )

            assert loop is not None
            _ = asyncio.run_coroutine_threadsafe(_serve_ws(), loop).result(timeout=10)
            print(f"  WebSocket  ws://{self.host}:{ws_port}")
        else:
            print("  WebSocket  [disabled — install websockets]")

        mode_label = "前台" if blocking else "后台"
        banner = f"""
╭─────────────────────────────────────────────╮
│  █████  ██  █████  ███   ██                 │
│ ██   ██ ██ ██   ██ ████  ██                 │
│ ███████ ██ ██   ██ ██ ██ ██                 │
│ ██   ██ ██ ██   ██ ██  ████                 │
│ ██   ██ ██  █████  ██   ███                 │
│                                             │
│  Gateway · {mode_label}模式                    │
│  HTTP      http://{self.host}:{self.port:<5}   │
╰─────────────────────────────────────────────╯"""
        print(banner)

        if blocking:
            print("按 Ctrl+C 停止服务")
            print()
            logger.info(f"AION Gateway 启动 http://{self.host}:{self.port}")
            self._server.serve_forever()
        else:
            self._server_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._server_thread.start()
            logger.info(f"AION Gateway 启动 http://{self.host}:{self.port}")
            return self._server_thread

    def run(self):
        """前台启动 Gateway

        在当前线程启动 HTTP Server，并在后台线程启动 Channel。
        此方法会阻塞直到 Server 关闭。
        """
        import sys

        self._stop_thread: threading.Thread | None = None

        # 注册 SIGTERM 信号处理器：后台线程执行 stop()，避免阻塞信号处理器造成死锁
        def sigterm_handler(signum, frame):  # noqa: ARG001
            if self._sigterm_received:
                # 已在关闭中，重复 SIGTERM 不再处理（launchctl bootout + os.kill 可能发两次）
                return
            self._sigterm_received = True  # 在 signal handler 线程立即设标志，不等 stop 线程

            my_pid = os.getpid()
            my_ppid = os.getppid() if hasattr(os, "getppid") else 0
            my_pgid = os.getpgid(my_pid) if hasattr(os, "getpgid") else 0

            sig_name = getattr(signal, f"SIG{signum}", signum) if isinstance(signum, int) else signum
            logger.info(f"收到信号 {sig_name}({signum})，pid={my_pid} ppid={my_ppid} pgid={my_pgid}，开始优雅关闭...")

            # 异步查询信号来源
            def query_source():
                import subprocess

                try:
                    if sys.platform == "win32":
                        return
                    ps_result = subprocess.run(
                        ["ps", "-o", "pid,ppid,uid,comm", "-p", str(my_ppid)],
                        capture_output=True,
                        text=True,
                        timeout=_PS_TIMEOUT,
                    )
                    if ps_result.returncode == 0:
                        lines = ps_result.stdout.strip().split("\n")
                        if len(lines) >= 2:
                            logger.info(f"信号来源: {lines[1].strip()}")
                except Exception:
                    pass

            threading.Thread(target=query_source, daemon=True).start()

            # 后台线程执行 stop() 避免死锁：stop() 调 server.shutdown() 等待
            # serve_forever() 响应，而后者需信号处理器返回后才恢复运行。
            stop_thread = threading.Thread(target=self.stop, daemon=True)
            self._stop_thread = stop_thread
            stop_thread.start()

            # 兜底：优雅关闭超时后强制退出
            shutdown_timer = threading.Timer(_SHUTDOWN_FALLBACK_TIMEOUT, lambda: os._exit(0))
            shutdown_timer.daemon = True
            shutdown_timer.start()
            self._shutdown_timer = shutdown_timer

        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, sigterm_handler)

        self._start_server(blocking=True)

        # 如果是 SIGTERM 触发的关闭，等待 stop 线程的剩余清理完成
        if self._shutting_down:
            # 关闭兜底计时器（已优雅完成，不需要 os._exit）
            if hasattr(self, "_shutdown_timer"):
                self._shutdown_timer.cancel()
            stop_thread = self._stop_thread
            if stop_thread and stop_thread.is_alive():
                stop_thread.join(timeout=_SHUTDOWN_FALLBACK_TIMEOUT + 5)

    def start_background(self):
        """后台启动 Gateway

        在后台线程启动 HTTP Server 和 Channel，
        立即返回调用者。

        Returns:
            threading.Thread: Server 线程对象
        """
        return self._start_server(blocking=False)

    def stop(self):
        if self._shutting_down:
            return
        self._shutting_down = True
        logger.info("正在停止 Gateway...")

        # ── 1. 委托 scheduler 停止所有 Channel ──
        if self._scheduler:
            self._scheduler.stop()

        # ── 2. 关闭 HTTP Server ──
        if self._server:
            self._server.shutdown()
            logger.info("HTTP Server 已关闭")

        # ── 3. AgentLoop — Phase B 不再缓存，无需清理 ──

        # ── 4. 刷新 Langfuse 事件队列 ──
        from ..observability import LangfuseClient

        LangfuseClient.flush()

        print("✓ Gateway 已停止", flush=True)

    class _RequestHandler(BaseHTTPRequestHandler):
        """Gateway HTTP 请求处理器

        处理两种请求：
        - POST /: 发送消息给 Agent，返回响应
        - GET /sessions: 列出历史 Session
        """

        def do_POST(self):
            # 关闭中拒绝新请求
            if self.server.gateway._shutting_down:
                self._send_json({"response": "Server is shutting down", "session_id": ""})
                return

            import uuid
            import asyncio

            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            data = json.loads(body)
            message = data.get("message") or ""
            session_id = data.get("session_id") or "cli"

            if not message.strip():
                self._send_json({"response": "请提供消息内容", "session_id": session_id})
                return

            from .dispatch import dispatch_message

            config = load_config()
            workspace_dir = resolve_workspace_dir(config=config)

            ctx = MessageContext(
                channel_id="http",
                chat_id=session_id,
                message_id=f"http-{uuid.uuid4().hex[:8]}",
                sender_id="cli",
                content=message,
                chat_type="p2p",
                workspace_dir=workspace_dir,
            )
            http_adapter = self.server.http_adapter

            # 复用 scheduler 的持久 event loop，避免 per-request loop 关闭
            # 导致 SessionQueue Worker 被销毁（Worker 捕获了创建时的 loop 引用）。
            scheduler = self.server.gateway._scheduler
            loop = scheduler.loop if scheduler else None
            if loop is None or loop.is_closed():
                logger.error("Scheduler loop not available")
                self._send_json({"error": "Server not ready", "session_id": session_id})
                return

            async def _enqueue_only():
                return await dispatch_message(ctx=ctx, channel=http_adapter)

            future = asyncio.run_coroutine_threadsafe(_enqueue_only(), loop)
            result = future.result(timeout=10)

            self._send_json(
                {
                    "session_id": result.session_id or session_id,
                    "status": "queued",
                    "message": "消息已入队，响应将通过 WebSocket 推送",
                }
            )

        def _send_json(self, data: dict) -> None:
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())
            except OSError:
                # 关闭过程中客户端断开连接是正常现象，静默忽略
                pass

        def do_GET(self):
            """处理 GET 查询请求。

            支持路径：
            - ``GET /sessions``：返回最近 20 个 Session 列表
            - ``GET /status``：返回 Gateway 健康状态（含 Channel 连接信息）
            - ``GET /``：返回 ``{"status": "ok"}`` 健康检查

            Returns:
                None（HTTP 响应直接写入 ``self.wfile``）。
            """
            parsed = urlparse(self.path)
            if parsed.path == "/sessions":
                server = self.server
                sessions = server.session_manager.list_recent(limit=20)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"sessions": sessions}).encode())
            elif parsed.path == "/status":
                self._handle_status()
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok"}).encode())

        def _handle_status(self):
            channels = {}
            registry = get_channel_registry()
            for channel_id, channel in registry.get_all_channels().items():
                status = channel.get_status()
                # 如果该 channel 有启动失败记录，补充错误信息
                failed = registry.get_failed_channels().get(channel_id)
                if failed:
                    status["status"] = "failed"
                    status["error"] = failed
                channels[channel_id] = status
            # 追加完全未注册成功（启动前即失败）的 channel
            for channel_id, error_msg in registry.get_failed_channels().items():
                if channel_id not in channels:
                    channels[channel_id] = {"status": "failed", "error": error_msg}

            # 会话队列状态
            try:
                session_queues = get_all_status()
            except Exception:
                session_queues = {}

            self._send_json(
                {
                    "status": "ok",
                    "services": {"http": "running"},
                    "start_time": self.server.gateway.start_time,
                    "channels": channels,
                    "session_queues": session_queues,
                }
            )

        def log_message(self, format, *args):
            """抑制 BaseHTTPRequestHandler 默认的请求访问日志输出。

            Args:
                format: 日志格式字符串（未使用）。
                *args: 格式参数（未使用）。

            Returns:
                None
            """
            pass
