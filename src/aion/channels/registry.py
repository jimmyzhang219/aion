"""Channel 注册表

管理所有已注册的 Channel 插件。
Gateway 通过此注册表统一管理所有 Channel。
"""

import logging
from typing import Optional

from .adapters import ChannelPlugin

logger = logging.getLogger(__name__)


class ChannelRegistry:
    """Channel 注册表

    管理所有已注册的 Channel 插件，提供单例模式访问。
    """

    _instance: Optional["ChannelRegistry"] = None  # 单例实例

    def __init__(self):
        """初始化注册表实例"""
        self._channels: dict[str, ChannelPlugin] = {}  # channel_id -> 插件实例
        self._failed: dict[str, str] = {}  # channel_id -> error_msg
        self._running = False  # 是否已调用 start_all

    @classmethod
    def get_instance(cls) -> "ChannelRegistry":
        """获取单例实例

        Returns:
            ChannelRegistry: 全局注册表
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例实例（用于测试）

        Returns:
            None
        """
        cls._instance = None

    def register(self, channel: ChannelPlugin) -> None:
        """注册 Channel

        Args:
            channel: Channel 插件实例

        Raises:
            ValueError: 如果 Channel ID 已注册
        """
        channel_id = channel.channel_id
        if channel_id in self._channels:
            raise ValueError(f"Channel '{channel_id}' already registered")
        self._channels[channel_id] = channel
        self._failed.pop(channel_id, None)  # 清除同名失败记录
        logger.info(f"Channel registered: {channel_id}")

    def unregister(self, channel_id: str) -> None:
        """取消注册 Channel

        Args:
            channel_id: Channel ID
        """
        if channel_id in self._channels:
            del self._channels[channel_id]
            logger.info(f"Channel unregistered: {channel_id}")

    def get(self, channel_id: str) -> Optional[ChannelPlugin]:
        """获取 Channel

        Args:
            channel_id: Channel ID

        Returns:
            Channel 插件实例，如果不存在返回 None
        """
        return self._channels.get(channel_id)

    def list_channels(self) -> list[str]:
        """列出所有已注册的 Channel ID

        Returns:
            list[str]: 已注册的 channel_id 列表
        """
        return list(self._channels.keys())

    def get_all_channels(self) -> dict[str, ChannelPlugin]:
        """获取所有 Channel

        Returns:
            dict[str, ChannelPlugin]: channel_id 到插件实例的副本
        """
        return dict(self._channels)

    def register_failure(self, channel_id: str, error_msg: str) -> None:
        """记录 Channel 启动失败

        Args:
            channel_id: Channel ID
            error_msg: 失败原因
        """
        self._failed[channel_id] = error_msg
        logger.error(f"Channel '{channel_id}' failed to start: {error_msg}")

    def get_failed_channels(self) -> dict[str, str]:
        """获取所有启动失败的 Channel

        Returns:
            dict[str, str]: channel_id 到错误信息的副本
        """
        return dict(self._failed)

    async def start_all(self) -> None:
        """启动所有 Channel

        遍历已注册插件并调用 start()，已运行的 Channel 会跳过。

        Returns:
            None
        """
        self._running = True
        for channel_id, channel in self._channels.items():
            if channel.is_running():
                logger.info(f"Channel {channel_id} already running")
                continue
            try:
                await channel.start()
                logger.info(f"Channel {channel_id} started")
            except Exception as e:
                self.register_failure(channel_id, str(e))

    async def stop_all(self) -> None:
        """停止所有 Channel

        遍历正在运行的 Channel 并调用 stop()。

        Returns:
            None
        """
        self._running = False
        for channel_id, channel in list(self._channels.items()):
            if not channel.is_running():
                continue
            try:
                await channel.stop()
                logger.info(f"Channel {channel_id} stopped")
            except Exception as e:
                logger.warning(f"Error stopping channel {channel_id}: {e}")

    @property
    def is_running(self) -> bool:
        """是否正在运行

        Returns:
            bool: start_all 成功后为 True
        """
        return self._running


# 便捷函数（模块级快捷入口）
def get_channel_registry() -> ChannelRegistry:
    """获取 Channel 注册表单例

    Returns:
        ChannelRegistry: 全局注册表实例
    """
    return ChannelRegistry.get_instance()


def register_channel(channel: ChannelPlugin) -> None:
    """注册 Channel

    Args:
        channel: 要注册的 Channel 插件实例

    Returns:
        None
    """
    get_channel_registry().register(channel)


def get_channel(channel_id: str) -> Optional[ChannelPlugin]:
    """获取 Channel

    Args:
        channel_id: Channel ID

    Returns:
        Optional[ChannelPlugin]: 插件实例，不存在时返回 None
    """
    return get_channel_registry().get(channel_id)


def list_channels() -> list[str]:
    """列出所有 Channel

    Returns:
        list[str]: 已注册的 channel_id 列表
    """
    return get_channel_registry().list_channels()
