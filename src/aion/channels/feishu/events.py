"""飞书事件处理

设计文档: docs/design/feishu-channel.md 第 5 节
"""

import asyncio
import json
import logging
from typing import Optional, Callable, Any, Dict
from dataclasses import dataclass

import lark_oapi as lark

from .config import FeishuAccountConfig
from .dedup import FeishuDedup
from .message import parse_feishu_message_context

logger = logging.getLogger(__name__)


@dataclass
class FeishuHandlerContext:
    """飞书事件处理器上下文

    聚合事件分发所需的配置与辅助组件。
    """

    config: FeishuAccountConfig  # 账号配置
    account_id: str  # 账号 ID
    bot_open_id: Optional[str] = None  # 机器人 open_id
    dedup: Optional[FeishuDedup] = None  # 消息去重器


class EventRegistry:
    """飞书事件注册表

    管理事件类型和对应处理器的映射。
    """

    def __init__(self):
        """初始化空的事件处理器映射"""
        self._handlers: Dict[str, Callable] = {}  # event_type -> handler

    def register(self, event_type: str, handler: Callable) -> None:
        """注册事件处理器

        Args:
            event_type: 飞书事件类型，如 im.message.receive_v1
            handler: 处理该事件的 callable

        Returns:
            None
        """
        self._handlers[event_type] = handler

    def unregister(self, event_type: str) -> None:
        """注销事件处理器

        Args:
            event_type: 飞书事件类型

        Returns:
            None
        """
        self._handlers.pop(event_type, None)

    def get_handler(self, event_type: str) -> Optional[Callable]:
        """获取事件处理器

        Args:
            event_type: 飞书事件类型

        Returns:
            Optional[Callable]: 已注册的处理器，不存在时返回 None
        """
        return self._handlers.get(event_type)

    def list_handlers(self) -> list[str]:
        """列出所有已注册的事件类型

        Returns:
            list[str]: 事件类型列表
        """
        return list(self._handlers.keys())


class FeishuEventDispatcher:
    """飞书事件分发器

    接收飞书事件，根据事件类型分发给对应的处理器。
    """

    def __init__(self, ctx: FeishuHandlerContext):
        """初始化事件分发器

        Args:
            ctx: 包含配置、账号、bot_open_id、去重器等上下文
        """
        self.ctx = ctx
        self.registry = EventRegistry()
        self._message_handler: Optional[Callable] = None  # 主消息处理器
        self._setup_default_handlers()

    def _setup_default_handlers(self) -> None:
        """注册内置事件类型与默认处理器

        Returns:
            None
        """
        # 消息事件
        self.registry.register("im.message.receive_v1", self._handle_message_event)

    def set_message_handler(self, handler: Callable) -> None:
        """设置消息处理器（主处理器）

        Args:
            handler: 接收 FeishuMessageContext 的回调

        Returns:
            None
        """
        self._message_handler = handler

    def do_without_validation(self, payload: bytes) -> Any:
        """处理 WebSocket 事件（实现 lark-oapi EventDispatcherHandler 接口）

        Args:
            payload: 原始事件数据（bytes）

        Returns:
            处理结果
        """
        try:
            from lark_oapi.event.context import EventContext
            from lark_oapi.core.json import JSON
            from lark_oapi.core.const import UTF_8

            pl = payload.decode(UTF_8)
            # logger.debug(f"do_without_validation received: {pl[:200]}")

            context = JSON.unmarshal(pl, EventContext)

            # 解析事件类型
            event_type = ""
            if hasattr(context, "header") and context.header and hasattr(context.header, "event_type"):
                event_type = context.header.event_type
            elif hasattr(context, "event") and context.event:
                event_type = context.event.get("type", "")

            # logger.info(f"do_without_validation: event_type={event_type}, schema={getattr(context, 'schema', '?')}")

            # 构建事件数据
            event_data = {
                "event_type": event_type,
                "payload": context,
                "schema": getattr(context, "schema", "p2"),
            }

            # 在独立线程中运行 async dispatch
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, self.dispatch(event_data))
                return future.result(timeout=30)
        except Exception as e:
            logger.error(f"Error in do_without_validation: {e}")
            import traceback

            traceback.print_exc()
            return None

    async def dispatch(self, event_data: Dict) -> Any:
        """分发事件

        Args:
            event_data: 飞书事件数据

        Returns:
            处理结果
        """
        event_type = event_data.get("event_type", "")

        if not event_type:
            # 顶层无 event_type 时，从嵌套 payload 中兜底解析
            payload = event_data.get("payload", event_data)
            event_type = payload.get("event_type", "")

        # logger.debug(f"[FeishuEventDispatcher.dispatch] event_type={event_type}")

        handler = self.registry.get_handler(event_type)
        if not handler:
            logger.debug(f"No handler for event type: {event_type}")
            return None

        try:
            # logger.debug(f"[FeishuEventDispatcher.dispatch] calling handler for {event_type}")
            result = await handler(event_data)
            # logger.debug(f"[FeishuEventDispatcher.dispatch] handler result: {result}")
            return result
        except Exception as e:
            logger.error(f"Error handling event {event_type}: {e}")
            raise

    async def _handle_message_event(self, event_data: Dict) -> Any:
        """处理消息事件 im.message.receive_v1

        Args:
            event_data: 飞书事件数据

        Returns:
            Any: 消息处理器的返回值；去重/忽略时返回 None
        """
        if not self._message_handler:
            # logger.warning("No message handler configured")
            return None

        # 解析消息
        # 如果 event_data 包含 payload（JSON字符串），需要先解析
        raw_event = event_data.get("payload", event_data)
        if isinstance(raw_event, str):
            raw_event = json.loads(raw_event)

        # 如果是 lark-oapi 对象，序列化为 dict
        if hasattr(raw_event, "__dict__"):
            raw_event = json.loads(lark.JSON.marshal(raw_event))

        # 提取 event 字段（v2.0 格式）
        event_obj = raw_event.get("event", raw_event)

        ctx = parse_feishu_message_context(event_obj, self.ctx.bot_open_id)

        # 去重检查
        if self.ctx.dedup:
            if await self.ctx.dedup.is_duplicate(ctx.message_id):
                # logger.debug(f"Duplicate message: {ctx.message_id}")
                return None
            await self.ctx.dedup.mark_processed(ctx.message_id)

        # 检查是否需要 @bot（群聊默认要求 @，否则静默丢弃：无 typing、无回复）
        if self.ctx.config.requireMention and ctx.chat_type == "group":
            if not ctx.mentioned_bot:
                logger.info(
                    "飞书群消息已忽略（requireMention=true 且未 @ 机器人）: "
                    "chat_id=%s message_id=%s content_preview=%r",
                    ctx.chat_id,
                    ctx.message_id,
                    (ctx.content or "")[:80],
                )
                return None

        # dedup 检查通过，代表 aion 正式接收此消息，发送 Typing Indicator
        if ctx.message_id and ctx.chat_id:
            from .client import add_typing_indicator

            add_typing_indicator(ctx.message_id, ctx.chat_id)

        # 调用消息处理器
        return await self._message_handler(ctx)


def create_event_dispatcher(
    config: FeishuAccountConfig,
    account_id: str = "default",
    bot_open_id: Optional[str] = None,
    dedup: Optional[FeishuDedup] = None,
) -> FeishuEventDispatcher:
    """创建飞书事件分发器

    Args:
        config: 飞书账号配置
        account_id: 账号 ID
        bot_open_id: 机器人 open_id
        dedup: 去重器

    Returns:
        FeishuEventDispatcher 实例
    """
    ctx = FeishuHandlerContext(
        config=config,
        account_id=account_id,
        bot_open_id=bot_open_id,
        dedup=dedup,
    )

    return FeishuEventDispatcher(ctx)
