"""Channel 适配器接口定义

定义 Channel 与 Agent/Gateway 交互的接口。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING
from enum import Enum

if TYPE_CHECKING:
    from .types import DispatchResult, MessageContext
    from ..gateway.server import GatewayServer


class CommandSource(Enum):
    """命令来源枚举

    标识用户触发命令的方式，供 Gateway 区分处理策略。
    """

    SLASH = "slash"  # / 开头的命令
    MENTION = "mention"  # @ 机器人
    KEYWORD = "keyword"  # 关键词触发
    OTHER = "other"


@dataclass
class CommandResult:
    """命令执行结果

    Channel 命令适配器处理 slash 命令后返回的统一结构。
    """

    handled: bool  # 命令是否被识别并处理
    response: Optional[str] = None  # 处理后的响应文本（可选）
    continue_to_agent: bool = False  # 是否继续传给 Agent


@dataclass
class SendResult:
    """消息发送结果

    Channel 向外部平台发送消息后的统一返回结构。
    """

    message_id: str  # 平台返回的消息 ID
    chat_id: str  # 目标聊天 ID
    success: bool = True  # 是否发送成功
    error: Optional[str] = None  # 失败时的错误信息


class ChannelAgentPromptAdapter:
    """Channel Agent Prompt 适配器

    提供 Channel 特定的格式提示，用于构建 LLM System Prompt。
    """

    def get_inbound_formatting_hints(self) -> dict:
        """返回入站格式提示

        Returns:
            dict: 包含 text_markup 和 rules
                - text_markup: 文本格式类型（如 "markdown", "plain_text"）
                - rules: 格式规则列表
        """
        return {"text_markup": "markdown", "rules": []}

    def get_message_tool_hints(self) -> list[str]:
        """返回消息工具提示

        Returns:
            list[str]: 工具提示列表
        """
        return []

    def get_message_tool_capabilities(self) -> list[str]:
        """返回消息工具能力

        Returns:
            list[str]: 工具能力列表
        """
        return []

    def get_reaction_guidance(self) -> dict:
        """返回反应指导

        Returns:
            dict: 包含 level (minimal/extensive) 和可选的 channel_label
        """
        return {"level": "minimal"}


class ChannelCommandAdapter(ABC):
    """Channel 命令处理适配器

    处理 Channel 特定的命令解析和执行。
    """

    def is_command(self, message: str, context: "MessageContext") -> bool:
        """判断消息是否为命令

        Args:
            message: 消息内容
            context: 消息上下文

        Returns:
            bool: 是否为命令
        """
        return message.startswith("/")

    def parse_command(self, message: str) -> tuple[str, str]:
        """解析命令

        Args:
            message: 消息内容（已确认是命令）

        Returns:
            tuple[str, str]: (command_name, args)
                - command_name: 命令名称（如 "new", "switch"）
                - args: 命令参数
        """
        parts = message.lstrip("/").split(None, 1)
        return parts[0].lower(), parts[1] if len(parts) > 1 else ""

    def get_supported_commands(self) -> list[dict]:
        """返回支持的命令列表

        Returns:
            list[dict]: 命令列表，每项包含 name, description, usage
        """
        return []


class ChannelPlugin(ABC):
    """Channel 插件抽象基类

    所有 Channel（如飞书、微信等）都需要实现此接口。
    Gateway 通过此接口统一管理所有 Channel。
    """

    _gateway: Optional["GatewayServer"] = None

    @property
    @abstractmethod
    def channel_id(self) -> str:
        """Channel 唯一标识符（如 "feishu", "wechat"）

        Returns:
            str: 小写 Channel ID
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def channel_name(self) -> str:
        """Channel 显示名称（如 "Feishu", "WeChat"）

        Returns:
            str: 人类可读名称
        """
        raise NotImplementedError

    @property
    def capabilities(self) -> dict:
        """Channel 支持的能力

        Returns:
            dict: 能力列表（chat_types, reactions, threads 等）
        """
        return {
            "chat_types": ["p2p", "group"],
            "reactions": True,
            "threads": True,
        }

    @abstractmethod
    async def start(self) -> None:
        """启动 Channel

        启动后 Channel 应在后台运行，消息通过 callback 回调。

        Returns:
            None
        """
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        """停止 Channel

        Returns:
            None
        """
        raise NotImplementedError

    @abstractmethod
    def is_running(self) -> bool:
        """Channel 是否正在运行

        Returns:
            bool: 运行中为 True
        """
        raise NotImplementedError

    @abstractmethod
    def get_agent_prompt_adapter(self) -> ChannelAgentPromptAdapter:
        """获取 Agent Prompt 适配器

        Returns:
            ChannelAgentPromptAdapter: 格式提示适配器
        """
        raise NotImplementedError

    @abstractmethod
    def get_command_adapter(self) -> ChannelCommandAdapter:
        """获取命令处理适配器

        Returns:
            ChannelCommandAdapter: 命令处理适配器
        """
        raise NotImplementedError

    @abstractmethod
    async def send_message(
        self, chat_id: str, content: str, reply_in_thread: bool = False, parent_id: Optional[str] = None, **kwargs
    ) -> SendResult:
        """发送消息

        Args:
            chat_id: 聊天 ID
            content: 消息内容
            reply_in_thread: 是否在话题中回复
            parent_id: 父消息 ID
            **kwargs: 其他 Channel 特定参数

        Returns:
            SendResult: 发送结果
        """
        raise NotImplementedError

    async def respond(self, ctx: "MessageContext", result: "DispatchResult") -> None:
        """处理完消息后，Worker 调用此方法发送响应。

        默认实现：
        1. 如果有 error → 发送错误消息
        2. 如果是命令响应 → 发送 command_response
        3. 否则发送 response（含 footer）

        Args:
            ctx: 原始消息上下文
            result: Worker 处理结果
        """
        if result.error:
            await self.send_message(
                chat_id=ctx.chat_id,
                content=f"抱歉，处理消息时出错：{result.error}",
                reply_in_thread=bool(ctx.thread_id),
                parent_id=ctx.parent_id,
            )
        elif result.command_handled and result.command_response:
            await self.send_message(
                chat_id=ctx.chat_id,
                content=result.command_response,
                reply_in_thread=bool(ctx.thread_id),
                parent_id=ctx.parent_id,
            )
        elif result.response:
            await self.send_message(
                chat_id=ctx.chat_id,
                content=result.response + (result.footer or ""),
                reply_in_thread=bool(ctx.thread_id),
                parent_id=ctx.parent_id,
            )

    def get_status(self) -> dict:
        """获取 Channel 状态信息

        Returns:
            dict: 状态信息
        """
        return {
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "running": self.is_running(),
        }

    def build_footer(
        self,
        workspace_name: str,
        model_name: str,
        tokens: int = 0,
        balance: Optional[str] = None,
        traceid: str = "",
    ) -> str:
        """构建消息 footer

        各 Channel 可重写以定制格式（如飞书用 Markdown 分隔线，其他平台可能不同）。
        返回空字符串表示不需要 footer。

        Args:
            workspace_name: 当前工作空间名
            model_name: 模型名
            tokens: Token 消耗
            balance: 余额信息（可选）
            traceid: 链路追踪 ID

        Returns:
            footer 字符串
        """
        parts = [f"Space: {workspace_name}", f"Model: {model_name}"]
        if tokens > 0:
            parts.append(f"Tokens: {tokens}")
        if balance:
            parts.append(f"Balance: {balance}")
        if traceid:
            parts.append(f"TraceID: {traceid}")
        return "\n\n---\n" + " | ".join(parts)

    def set_gateway(self, gateway: "GatewayServer") -> None:
        """设置关联的 Gateway

        Args:
            gateway: GatewayServer 实例

        Returns:
            None
        """
        self._gateway = gateway

    def get_gateway(self) -> Optional["GatewayServer"]:
        """获取关联的 Gateway

        Returns:
            GatewayServer: Gateway 实例
        """
        return getattr(self, "_gateway", None)

    def build_session_key(self, ctx: "MessageContext", agent_id: str) -> str:
        """构建 Session Key

        默认按 agent_id + channel_id + chat_id 组合。
        Channel 可重写以实现更细粒度的 session 绑定（如按 thread/sender）。

        Args:
            ctx: 统一消息上下文
            agent_id: Agent ID

        Returns:
            session_key 字符串
        """
        return f"agent:{agent_id}:{self.channel_id}:{ctx.chat_id}"


# 导出
__all__ = [
    "ChannelPlugin",
    "ChannelAgentPromptAdapter",
    "ChannelCommandAdapter",
    "CommandResult",
    "SendResult",
    "CommandSource",
]
