"""飞书 Channel 主入口

设计文档: docs/design/feishu-channel.md

导出：
- FeishuChannel: 飞书 Channel 主类（实现 ChannelPlugin 接口）
- FeishuConfig: 飞书配置
- 其他支持类型
"""

from pathlib import Path
from typing import Optional

from .channel import FeishuChannel
from .config import FeishuConfig, FeishuAccountConfig
from .prompt_adapter import FeishuAgentPromptAdapter
from .commands import FeishuCommandAdapter

# 保持向后兼容的导出（旧代码可能直接引用这些组件）
from .types import FeishuMessageContext, FeishuSendResult
from .sender import FeishuSender
from .events import FeishuEventDispatcher
from .dedup import FeishuDedup
from .auth import FeishuAuth, AuthResult, BeginResult


async def create_channel(
    config: dict,
    workspace_dir: Optional[Path] = None,
) -> FeishuChannel:
    """根据配置创建 FeishuChannel 实例

    供 Gateway 通过 importlib 动态加载 channel。

    Args:
        config: aion.json 中 channels.feishu 的配置字典
        workspace_dir: 已废弃，FeishuChannel 改为从 aion.json 动态读取当前 workspace

    Returns:
        已配置但未启动的 FeishuChannel 实例
    """
    channel_config = FeishuConfig(
        enabled=True,
        appId=config["appId"],
        appSecret=config["appSecret"],
        domain=config.get("domain", "feishu"),
        connectionMode=config.get("connectionMode", "websocket"),
    )  # type: ignore[call-arg]
    return FeishuChannel(channel_config)


__all__ = [
    # 主类
    "FeishuChannel",
    # 配置
    "FeishuConfig",
    "FeishuAccountConfig",
    # 工厂函数
    "create_channel",
    # 适配器
    "FeishuAgentPromptAdapter",
    "FeishuCommandAdapter",
    # 类型
    "FeishuMessageContext",
    "FeishuSendResult",
    # 组件
    "FeishuSender",
    "FeishuEventDispatcher",
    "FeishuDedup",
    "FeishuAuth",
    "AuthResult",
    "BeginResult",
]
