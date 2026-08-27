"""Channels 模块

Channel 抽象接口和具体实现。
Channel 是 aion 与外部消息系统（如飞书、微信）通信的桥梁。

主要组件：
- ChannelPlugin: Channel 插件抽象基类
- ChannelAgentPromptAdapter: Agent Prompt 适配器
- ChannelCommandAdapter: 命令处理适配器
- ChannelRegistry: Channel 注册表
- MessageContext: 统一消息上下文

具体 Channel 实现：
- FeishuChannel: 飞书 Channel

设计理念：
- 每个 Channel 是独立的连接，负责协议转换
- Gateway 只做路由，不知道 Channel 内部实现
- Channel 通过 ChannelPlugin 接口提供格式提示和命令处理
- 消息统一转换为 MessageContext 后传给 Agent
"""

from .adapters import (
    ChannelPlugin,
    ChannelAgentPromptAdapter,
    ChannelCommandAdapter,
    CommandResult,
    SendResult,
    CommandSource,
)
from .registry import (
    ChannelRegistry,
    get_channel_registry,
    register_channel,
    get_channel,
    list_channels,
)
from .types import (
    MessageContext,
)

__all__ = [
    # 核心接口
    "ChannelPlugin",
    "ChannelAgentPromptAdapter",
    "ChannelCommandAdapter",
    "CommandResult",
    "SendResult",
    "CommandSource",
    # 注册表
    "ChannelRegistry",
    "get_channel_registry",
    "register_channel",
    "get_channel",
    "list_channels",
    # 类型
    "MessageContext",
]
