#!/usr/bin/env python3
"""汇聚点直连测试脚本

绕过飞书 Channel，直接用 Mock ChannelPlugin 调用 GatewayServer.handle_channel_message，
用于本地验证 Gateway 消息处理与会话路由，无需启动 HTTP 或飞书连接。

用法：
  cd /path/to/aion
  source .venv/bin/activate
  python scripts/test_gateway.py "明天北京天气"
  python scripts/test_gateway.py "2026.05.08廊坊天气" --session my-test
"""
import asyncio
import sys
from pathlib import Path

# 将项目 src 加入导入路径，便于直接 import aion
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aion.channels.adapters import (
    ChannelPlugin,
    ChannelAgentPromptAdapter,
    ChannelCommandAdapter,
    SendResult,
)
from aion.channels.types import MessageContext


class MockChannel(ChannelPlugin):
    """Mock Channel：不连接任何外部服务，send_message 直接打印到 stdout"""

    @property
    def channel_id(self) -> str:
        """渠道标识，固定为 mock
        
        Returns:
            str: 渠道标识字符串
        """
        return "mock"

    @property
    def channel_name(self) -> str:
        """渠道显示名称
        
        Returns:
            str: 渠道显示名称
        """
        return "MockChannel"

    async def start(self) -> None:
        """启动钩子（无操作）
        
        Returns:
            None
        """
        pass

    async def stop(self) -> None:
        """停止钩子（无操作）
        
        Returns:
            None
        """
        pass

    def is_running(self) -> bool:
        """Mock 渠道始终视为运行中
        
        Returns:
            bool: 是否运行中
        """
        return True

    def get_agent_prompt_adapter(self) -> ChannelAgentPromptAdapter:
        """返回默认 Agent Prompt 适配器

        Returns:
            ChannelAgentPromptAdapter: 默认适配器实例
        """
        return ChannelAgentPromptAdapter()

    def get_command_adapter(self) -> ChannelCommandAdapter:
        """返回默认命令适配器

        Returns:
            ChannelCommandAdapter: 默认适配器实例
        """
        return ChannelCommandAdapter()

    async def send_message(
        self,
        chat_id: str,
        content: str,
        reply_in_thread: bool = False,
        parent_id: str | None = None,
    ) -> SendResult:
        """将出站消息打印到 stdout 并返回模拟 message_id
        
        Args:
            chat_id: 会话/chat 标识
            content: 消息正文内容
            reply_in_thread: 是否在线程内回复
            parent_id: 父消息 ID（可选）
        
        Returns:
            SendResult: 含 mock message_id 的发送结果
        """
        print(f"\n{'='*60}")
        print(f"[MockChannel.send_message] chat_id={chat_id}")
        print(f"[MockChannel.send_message] content=\n{content}")
        print(f"{'='*60}\n")
        return SendResult(message_id=f"mock-msg-{id(content)}", chat_id=chat_id)

    def get_status(self) -> dict:
        """返回渠道运行状态摘要
        
        Returns:
            dict: 状态摘要字典
        """
        return {"running": True, "channel_id": "mock"}


async def main() -> None:
    """解析命令行、构造 MessageContext 并驱动 Gateway 处理一条消息

    从 sys.argv 读取用户查询与可选 --session；创建 GatewayServer 与 MockChannel，
    调用 handle_channel_message 完成端到端本地验证。

    Returns:
        None
    """
    from aion.log import set_traceid, reset_traceid, configure_logging
    from aion.gateway.server import GatewayServer

    configure_logging(verbose=True)

    # 解析参数：第一条非选项参数为查询文本
    query = sys.argv[1] if len(sys.argv) > 1 else "今天北京天气"
    session_id = None
    for i, arg in enumerate(sys.argv):
        if arg == "--session" and i + 1 < len(sys.argv):
            session_id = sys.argv[i + 1]

    print(f"查询: {query}")
    print(f"Session: {session_id or '自动'}")
    print("-" * 60)

    gateway = GatewayServer()

    # 每条测试消息使用唯一 id，并截取前 16 字符作为 traceid
    msg_id = f"test-{id(query)}"
    _trace_token = set_traceid(msg_id[:16])

    # 模拟飞书 p2p 入站消息的上下文
    ctx = MessageContext(
        channel_id="mock",
        chat_id="test-chat-001",
        message_id=msg_id,
        sender_id="test-user",
        sender_name="Tester",
        chat_type="p2p",
        content=query,
        content_type="text",
    )

    # 指定 --session 时用 session 作为 message_id 以复用同一会话
    if session_id:
        ctx.message_id = session_id
        _trace_token = set_traceid(session_id[:16])

    channel = MockChannel()
    await gateway.handle_channel_message(channel, ctx)
    reset_traceid(_trace_token)


if __name__ == "__main__":
    asyncio.run(main())
