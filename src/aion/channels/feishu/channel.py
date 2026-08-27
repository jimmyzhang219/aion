"""飞书 Channel 主入口 (实现 ChannelPlugin 接口)

设计文档: docs/design/feishu-channel.md

实现 ChannelPlugin 接口，支持多 Channel 架构。
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional, Callable, Any, TYPE_CHECKING

from ..adapters import ChannelPlugin, ChannelAgentPromptAdapter, ChannelCommandAdapter, SendResult
from ..constants import ContentBlockType
from ...config.loader import resolve_workspace_dir
from ..types import MessageContext as UnifiedMessageContext
from .config import FeishuConfig, FeishuAccountConfig
from .client import create_feishu_client, remove_typing_indicator
from .connection import ConnectionManager
from .events import FeishuEventDispatcher, create_event_dispatcher
from .sender import FeishuSender
from .dedup import FeishuDedup
from .session import build_feishu_session_key
from .types import FeishuMessageContext, FeishuSendResult
from .prompt_adapter import FeishuAgentPromptAdapter
from .commands import FeishuCommandAdapter

if TYPE_CHECKING:
    from ...gateway.server import GatewayServer
    from ..types import MessageContext, DispatchResult

logger = logging.getLogger(__name__)


class FeishuChannel(ChannelPlugin):
    """飞书 Channel (实现 ChannelPlugin 接口)

    统一管理飞书连接、事件处理和消息发送。
    实现 ChannelPlugin 接口，支持多 Channel 架构。

    使用示例:

    ```python
    from aion.channels.feishu import FeishuChannel
    from aion.channels.feishu.config import FeishuConfig

    config = FeishuConfig(
        enabled=True,
        appId="cli_xxx",
        appSecret="xxx",
        connectionMode="websocket",
    )

    channel = FeishuChannel(config, workspace_dir)
    await channel.start()
    ```
    """

    def __init__(
        self,
        config: FeishuConfig,
        workspace_dir: Optional[Path] = None,
        message_callback: Optional[Callable[[FeishuMessageContext], Any]] = None,
    ):
        """初始化飞书 Channel 实例

        Args:
            config: 飞书配置
            workspace_dir: 已废弃，FeishuChannel 改为从 aion.json 动态读取当前 workspace
            message_callback: 消息处理回调（已废弃，请使用 set_message_callback）

        Returns:
            None
        """
        self._config = config
        self._account_config = config.get_active_account()

        # 消息回调（旧接口，保持兼容）
        self._message_callback = message_callback

        # 内部组件
        self._client = None
        self._sender: Optional[FeishuSender] = None
        self._dedup: Optional[FeishuDedup] = None
        self._agent_id = "main"
        self._event_dispatcher: Optional[FeishuEventDispatcher] = None
        self._connection_manager: Optional[ConnectionManager] = None

        # 运行状态
        self._running = False

        # 适配器
        self._agent_prompt_adapter: Optional[FeishuAgentPromptAdapter] = None
        self._command_adapter: Optional[FeishuCommandAdapter] = None

        # Gateway 引用
        self._gateway: Optional["GatewayServer"] = None

    # ===== ChannelPlugin 实现 =====

    @property
    def channel_id(self) -> str:
        """Channel 唯一标识符

        Returns:
            str: 固定为 "feishu"
        """
        return "feishu"

    @property
    def channel_name(self) -> str:
        """Channel 显示名称

        Returns:
            str: "Feishu"
        """
        return "Feishu"

    def get_agent_prompt_adapter(self) -> ChannelAgentPromptAdapter:
        """获取 Agent Prompt 适配器（懒加载单例）

        Returns:
            ChannelAgentPromptAdapter: 飞书格式提示适配器
        """
        if self._agent_prompt_adapter is None:
            self._agent_prompt_adapter = FeishuAgentPromptAdapter()
        return self._agent_prompt_adapter

    def get_command_adapter(self) -> ChannelCommandAdapter:
        """获取命令处理适配器（懒加载单例）

        Returns:
            ChannelCommandAdapter: 飞书 slash 命令适配器
        """
        if self._command_adapter is None:
            self._command_adapter = FeishuCommandAdapter()
        return self._command_adapter

    async def send_message(
        self, chat_id: str, content: str, reply_in_thread: bool = False, parent_id: Optional[str] = None, **kwargs
    ) -> SendResult:
        """发送消息到飞书

        Args:
            chat_id: 聊天 ID
            content: 消息内容
            reply_in_thread: 是否在话题中回复
            parent_id: 父消息 ID
            **kwargs: 其他参数

        Returns:
            SendResult: 发送结果
        """
        if not self._sender:
            return SendResult(
                message_id="",
                chat_id=chat_id,
                success=False,
                error="Channel not started",
            )

        try:
            result = await self._sender.send_text(
                chat_id=chat_id,
                text=content,
                reply_in_thread=reply_in_thread,
                parent_id=parent_id,
            )
            return SendResult(
                message_id=result.message_id,
                chat_id=result.chat_id,
                success=True,
            )
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return SendResult(
                message_id="",
                chat_id=chat_id,
                success=False,
                error=str(e),
            )

    async def respond(self, ctx: "MessageContext", result: "DispatchResult") -> None:
        """飞书频道专用响应发送。

        覆盖基类默认行为：
        - error → 发送错误消息 + 移除 typing indicator
        - command_handled → 发送命令响应 + 移除 typing indicator
        - 常规消息 → thinking_parts 链式线程回复 + response+footer + 移除 typing indicator
        """
        if result.error:
            await self.send_message(
                chat_id=ctx.chat_id,
                content=f"抱歉，处理消息时出错：{result.error}",
                reply_in_thread=bool(ctx.thread_id),
                parent_id=ctx.parent_id,
            )
            remove_typing_indicator(ctx.message_id)
            return

        if result.command_handled:
            if result.command_response:
                await self.send_message(
                    chat_id=ctx.chat_id,
                    content=result.command_response,
                    reply_in_thread=bool(ctx.thread_id),
                    parent_id=ctx.parent_id,
                )
            remove_typing_indicator(ctx.message_id)
            return

        # 常规消息：分条发送 thinking + response
        last_msg_id = ctx.parent_id
        for think_content in result.thinking_parts:
            send_result = await self.send_message(
                chat_id=ctx.chat_id,
                content=think_content,
                reply_in_thread=True,
                parent_id=last_msg_id,
            )
            last_msg_id = send_result.message_id

        if result.response:
            full_response = result.response + result.footer
            await self.send_message(
                chat_id=ctx.chat_id,
                content=full_response,
                reply_in_thread=True,
                parent_id=last_msg_id,
            )
        remove_typing_indicator(ctx.message_id)

    def set_gateway(self, gateway: "GatewayServer") -> None:
        """设置 Gateway 引用（实现 ChannelPlugin 接口）"""
        self._gateway = gateway

    def get_gateway(self) -> Optional["GatewayServer"]:
        """获取 Gateway 引用

        Returns:
            Optional[GatewayServer]: 已设置的 Gateway，未设置时返回 None
        """
        return self._gateway

    def set_scheduler_loop(self, loop) -> None:
        """设置 scheduler 事件循环，用于跨线程 dispatch"""
        self._scheduler_loop = loop

    # ===== ChannelPlugin 实现 =====

    def is_enabled(self) -> bool:
        """是否启用

        Returns:
            bool: 配置中 enabled 字段
        """
        return self._config.enabled

    async def start(self) -> None:
        """启动飞书 Channel

        创建客户端、发送器、去重器与连接，按 connectionMode 选择 WS 或 Webhook。

        Returns:
            None

        Raises:
            Exception: 启动失败时会 stop 并重新抛出
        """
        if not self._config.enabled:
            logger.info("Feishu channel is disabled")
            return

        if self._running:
            logger.warning("Feishu channel already running")
            return

        logger.info("Starting Feishu channel...")

        try:
            # 创建客户端
            self._client = create_feishu_client(self._account_config)

            # 创建发送器
            self._sender = FeishuSender(self._client, self._account_config)

            # 创建去重器（使用当前 workspace）
            ws_dir = resolve_workspace_dir()
            self._dedup = FeishuDedup(ws_dir / ".aion", self._account_config.name or "default")

            # 创建事件分发器
            self._event_dispatcher = create_event_dispatcher(
                self._account_config,
                self._account_config.name or "default",
                None,
                self._dedup,
            )

            # 设置消息处理器
            if self._message_callback:
                self._event_dispatcher.set_message_handler(self._message_callback)
            else:
                # 使用内置的消息处理器
                self._event_dispatcher.set_message_handler(self._handle_message)

            # 创建连接管理器
            self._connection_manager = ConnectionManager(self._account_config)

            # 根据连接模式启动(WebSocket 模式在独立线程中运行,自动重连)
            if self._account_config.connectionMode == "websocket":
                self._connection_manager.connect_websocket(
                    self._event_dispatcher,
                    self._handle_event,
                    asyncio.get_running_loop(),
                )
            else:
                await self._connection_manager.connect_webhook(
                    self._handle_event,
                )

            self._running = True
            logger.info("Feishu channel started successfully")

        except Exception as e:
            logger.error(f"Failed to start Feishu channel: {e}")
            await self.stop()
            raise

    async def stop(self) -> None:
        """停止飞书 Channel

        断开连接并清理 ConnectionManager。

        Returns:
            None
        """
        if not self._running:
            return

        logger.info("Stopping Feishu channel...")

        if self._connection_manager:
            self._connection_manager.disconnect()
            self._connection_manager = None

        self._running = False
        logger.info("Feishu channel stopped")

    async def _handle_event(self, event_data: dict) -> Any:
        """处理飞书事件

        Args:
            event_data: 飞书事件数据

        Returns:
            处理结果
        """
        if not self._event_dispatcher:
            logger.warning("Event dispatcher not initialized")
            return None

        try:
            return await self._event_dispatcher.dispatch(event_data)
        except Exception as e:
            logger.error(f"Error handling event: {e}")
            return None

    async def _handle_message(self, ctx: FeishuMessageContext) -> Any:
        """处理飞书消息 — 统一通过 gateway dispatch

        dispatch_message 内部入队 SessionQueue，响应由 Worker 通过 self.respond() 发送。
        """
        if not self._gateway:
            logger.error("Gateway not set for FeishuChannel")
            return None

        unified_ctx = self._to_unified_context(ctx)

        # ── 多模态媒体消息：下载并构建 content blocks ──
        # media 是飞书移动端的视频消息类型，映射为 video
        block_type = "video" if ctx.content_type == "media" else ctx.content_type

        if block_type in ("image", "video", "audio", "file") and ctx.raw_content:
            from .media import download_feishu_media

            file_key: str | None = None
            if ctx.content_type == "image":
                file_key = ctx.raw_content.get("image_key")
            else:
                file_key = ctx.raw_content.get("file_key")

            # 普通文件（非 image/video/audio）LLM API 不支持，转文本占位
            if ctx.content_type == "file":
                unified_ctx.content = [{"type": ContentBlockType.TEXT, "text": "[文件]"}]
            elif file_key and self._client:
                # 移动端视频（media）需使用消息资源 API 下载
                if ctx.content_type == "media":
                    from .media import (
                        download_feishu_message_resource,
                        detect_mime_from_bytes,
                        rename_with_mime_ext,
                    )
                    from ..constants import VIDEO_MAX_BYTES

                    file_path = await download_feishu_message_resource(
                        message_id=ctx.message_id,
                        file_key=file_key,
                        resource_type="file",
                        client=self._client,
                    )
                    if file_path:
                        raw = Path(file_path).read_bytes()
                        mime, itype = detect_mime_from_bytes(raw)
                        # 超限检查
                        if len(raw) > VIDEO_MAX_BYTES:
                            max_mb = VIDEO_MAX_BYTES // (1024 * 1024)
                            await self.send_message(
                                chat_id=ctx.chat_id,
                                content=f"抱歉，视频文件超过 {max_mb}MB，已跳过。",
                                reply_in_thread=bool(ctx.thread_id),
                                parent_id=ctx.parent_id,
                            )
                            Path(file_path).unlink(missing_ok=True)
                            return None
                        fp = rename_with_mime_ext(file_path, mime)
                        unified_ctx.content = [
                            {"type": itype, "data": fp, "mimeType": mime},
                        ]
                    else:
                        await self.send_message(
                            chat_id=ctx.chat_id,
                            content=f"抱歉，{ctx.content_type} 下载失败，请稍后重试。",
                            reply_in_thread=bool(ctx.thread_id),
                            parent_id=ctx.parent_id,
                        )
                        return None
                else:
                    # 图片：统一使用消息资源 API（与富文本内联图片一致）
                    if ctx.content_type == "image":
                        from .media import download_feishu_message_resource

                        file_path = await download_feishu_message_resource(
                            message_id=ctx.message_id,
                            file_key=file_key,
                            resource_type="image",
                            client=self._client,
                        )
                    else:
                        file_path = await download_feishu_media(
                            file_key=file_key,
                            file_type=block_type,
                            client=self._client,
                            raw_content=ctx.raw_content,
                        )
                    if file_path:
                        from .media import _get_mime

                        mime = _get_mime(block_type, ctx.raw_content)
                        unified_ctx.content = [
                            {"type": block_type, "data": file_path, "mimeType": mime},
                        ]
                    else:
                        await self.send_message(
                            chat_id=ctx.chat_id,
                            content=f"抱歉，{ctx.content_type} 下载失败，请稍后重试。",
                            reply_in_thread=bool(ctx.thread_id),
                            parent_id=ctx.parent_id,
                        )
                        return None

        # ── 富文本（post）消息：按原始顺序解析元素（text + 内联图片/视频）──
        if ctx.content_type == "post" and ctx.raw_content:
            from .media import download_feishu_message_resource
            from .message import parse_post_elements

            elements = parse_post_elements(ctx.raw_content)
            if elements:
                has_media = any(e["type"] != ContentBlockType.TEXT for e in elements)
                if has_media:
                    content_blocks: list[dict] = []
                    download_ok = True
                    for el in elements:
                        if el["type"] == ContentBlockType.TEXT:
                            content_blocks.append({"type": ContentBlockType.TEXT, "text": el["text"]})
                        elif el["type"] == "media":
                            fk = el.get("file_key", "")
                            if fk and self._client:
                                try:
                                    fp = await download_feishu_message_resource(
                                        message_id=ctx.message_id,
                                        file_key=fk,
                                        resource_type="file",
                                        client=self._client,
                                    )
                                except Exception as exc:
                                    logger.error("Failed to download media: %s", exc)
                                    fp = None
                                if fp:
                                    from .media import (
                                        detect_mime_from_bytes,
                                        rename_with_mime_ext,
                                    )
                                    from ..constants import VIDEO_MAX_BYTES

                                    raw = Path(fp).read_bytes()
                                    mime, itype = detect_mime_from_bytes(raw)
                                    if (
                                        itype in (ContentBlockType.VIDEO, ContentBlockType.AUDIO)
                                        and len(raw) > VIDEO_MAX_BYTES
                                    ):
                                        # 超限文件 → 信息不完整，中断请求
                                        max_mb = VIDEO_MAX_BYTES // (1024 * 1024)
                                        await self.send_message(
                                            chat_id=ctx.chat_id,
                                            content=f"抱歉，{itype}文件超过 {max_mb}MB，已跳过。",
                                            reply_in_thread=bool(ctx.thread_id),
                                            parent_id=ctx.parent_id,
                                        )
                                        Path(fp).unlink(missing_ok=True)
                                        return None
                                    elif itype != ContentBlockType.FILE:
                                        # 用原始文件名后缀重命名（优先），否则 fallback 到 MIME 映射
                                        orig_ext = Path(el.get("file_name", "")).suffix
                                        if orig_ext:
                                            new_fp = str(Path(fp).with_suffix(orig_ext))
                                            try:
                                                Path(fp).rename(new_fp)
                                                fp = new_fp
                                            except OSError:
                                                fp = rename_with_mime_ext(fp, mime)
                                        else:
                                            fp = rename_with_mime_ext(fp, mime)
                                        content_blocks.append({"type": itype, "data": fp, "mimeType": mime})
                                    else:
                                        content_blocks.append({"type": ContentBlockType.TEXT, "text": "[文件]"})
                                        Path(fp).unlink(missing_ok=True)
                                else:
                                    logger.warning("Media download failed for %s, aborting message", el["type"])
                                    download_ok = False
                                    break
                        elif el["type"] == ContentBlockType.FILE:
                            # 普通文件 LLM API 不支持，转文本占位
                            content_blocks.append({"type": ContentBlockType.TEXT, "text": "[文件]"})
                        elif el["type"] in (ContentBlockType.IMAGE, ContentBlockType.VIDEO):
                            fk = el.get("file_key", "")
                            if fk and self._client:
                                try:
                                    rtype = (
                                        ContentBlockType.IMAGE.value
                                        if el["type"] == ContentBlockType.IMAGE
                                        else ContentBlockType.FILE.value
                                    )
                                    fp = await download_feishu_message_resource(
                                        message_id=ctx.message_id,
                                        file_key=fk,
                                        resource_type=rtype,
                                        client=self._client,
                                    )
                                except Exception as exc:
                                    logger.error("Failed to download %s: %s", el["type"], exc)
                                    fp = None
                                if fp:
                                    content_blocks.append({"type": el["type"], "data": fp, "mimeType": el["mimeType"]})
                                else:
                                    logger.warning(
                                        "Media download failed for %s, aborting message",
                                        el["type"],
                                    )
                                    download_ok = False
                                    break
                    if not download_ok:
                        await self.send_message(
                            chat_id=ctx.chat_id,
                            content="抱歉，图片/文件下载失败，请稍后重试。",
                            reply_in_thread=bool(ctx.thread_id),
                            parent_id=ctx.parent_id,
                        )
                        return None
                    unified_ctx.content = content_blocks

        from ...gateway.dispatch import dispatch_message

        scheduler_loop = getattr(self, "_scheduler_loop", None)
        if scheduler_loop and asyncio.get_running_loop() is not scheduler_loop:
            future = asyncio.run_coroutine_threadsafe(
                dispatch_message(ctx=unified_ctx, channel=self),
                scheduler_loop,
            )
            result = await asyncio.wrap_future(future)
        else:
            result = await dispatch_message(ctx=unified_ctx, channel=self)

        # 同步返回的命令（slash）需要立即发送响应
        if result and result.command_handled:
            await self.respond(unified_ctx, result)

    def _to_unified_context(self, ctx: FeishuMessageContext) -> UnifiedMessageContext:
        """将飞书 MessageContext 转换为统一格式

        Args:
            ctx: 飞书消息上下文

        Returns:
            统一的 MessageContext
        """
        metadata: dict[str, Any] = {}
        if ctx.raw_content:
            metadata["raw_content"] = ctx.raw_content

        return UnifiedMessageContext(
            channel_id="feishu",
            chat_id=ctx.chat_id,
            message_id=ctx.message_id,
            sender_id=ctx.sender_id,
            sender_name=ctx.sender_name,
            chat_type=ctx.chat_type,
            content=ctx.content,
            thread_id=ctx.thread_id,
            parent_id=ctx.parent_id,
            root_id=ctx.root_id,
            mentioned_bot=ctx.mentioned_bot,
            has_any_mention=ctx.has_any_mention,
            workspace_dir=resolve_workspace_dir(),
            metadata=metadata,
        )

    def build_session_key(self, ctx: "UnifiedMessageContext", agent_id: str) -> str:
        """构建 Feishu session key

        按 chat_type + thread_id + sender_id 组合，实现细粒度 session 绑定。
        """
        return build_feishu_session_key(
            agent_id=agent_id,
            chat_type=ctx.chat_type,  # type: ignore[arg-type]
            chat_id=ctx.chat_id,
            thread_id=ctx.thread_id,
            sender_id=ctx.sender_id,
        )

    def is_running(self) -> bool:
        """是否正在运行"""
        return self._running

    def get_status(self) -> dict:
        """获取 Channel 状态信息（含连接状态）"""
        connected = self._connection_manager is not None and self._connection_manager.is_connected
        return {
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "running": self._running,
            "connected": connected,
        }

    # ===== 兼容性方法 =====

    def set_message_callback(self, callback: Callable) -> None:
        """设置消息回调（兼容性方法）

        Args:
            callback: 接收 FeishuMessageContext 的回调

        Returns:
            None
        """
        self._message_callback = callback
        if self._event_dispatcher:
            self._event_dispatcher.set_message_handler(callback)


# 导出
__all__ = [
    "FeishuChannel",
    "FeishuConfig",
    "FeishuAccountConfig",
    "FeishuSender",
    "FeishuEventDispatcher",
    "FeishuDedup",
    "FeishuMessageContext",
    "FeishuSendResult",
    "FeishuAgentPromptAdapter",
    "FeishuCommandAdapter",
]
