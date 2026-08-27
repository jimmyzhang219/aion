"""WebSocket ChannelPlugin — Worker 通过 respond() 主动推送响应。"""

import json
from typing import Optional

from .adapters import ChannelPlugin, ChannelAgentPromptAdapter, ChannelCommandAdapter, SendResult


class WebSocketChannel(ChannelPlugin):
    """WebSocket Channel — 连接长期持有，Worker 主动 push 响应。"""

    channel_id = "ws"
    channel_name = "WebSocket"

    def __init__(self, websocket):
        self._ws = websocket
        self._chat_id = "ws-cli"

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        try:
            await self._ws.close()
        except Exception:
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
        """Worker respond() 通过此方法发送响应到 WebSocket。"""
        try:
            await self._ws.send(json.dumps({"type": "message", "content": content}))
            return SendResult(message_id="ws", chat_id=chat_id)
        except Exception as e:
            return SendResult(message_id="ws", chat_id=chat_id, success=False, error=str(e))
