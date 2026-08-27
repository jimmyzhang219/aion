"""HTTP ChannelPlugin 实现

供 HTTP/CLI 路径使用的轻量 ChannelPlugin 实现。
"""

from typing import Optional

from .adapters import (
    ChannelPlugin,
    ChannelAgentPromptAdapter,
    ChannelCommandAdapter,
    SendResult,
)


class HttpChannel(ChannelPlugin):
    """供 HTTP/CLI 路径使用的轻量 ChannelPlugin 实现"""

    channel_id = "http"
    channel_name = "HTTP"

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    def is_running(self) -> bool:
        return True

    def get_agent_prompt_adapter(self) -> ChannelAgentPromptAdapter:
        return ChannelAgentPromptAdapter()

    def get_command_adapter(self) -> ChannelCommandAdapter:
        return ChannelCommandAdapter()

    async def send_message(
        self,
        chat_id: str,
        content: str,
        reply_in_thread: bool = False,
        parent_id: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        return SendResult(message_id="http", chat_id=chat_id)
