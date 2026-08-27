"""统一消息上下文类型定义

定义跨 Channel 的统一消息格式。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    pass


@dataclass
class MessageContext:
    """统一消息上下文

    所有 Channel 的入站消息都会转换为该 dataclass，
    供 Gateway 与 Agent 以平台无关的方式处理。
    """

    # Channel 信息
    channel_id: str  # Channel ID（如 "feishu", "wechat"）
    chat_id: str  # 聊天 ID
    message_id: str  # 消息 ID
    sender_id: str  # 发送者 ID
    sender_name: Optional[str] = None  # 发送者名称

    # 聊天类型
    chat_type: str = "p2p"  # p2p=单聊, group=群聊, private=内部群

    # 消息内容
    # 纯文本消息为 ``str``；多模态消息为 ``list[dict]``，示例：
    #   [
    #       {"type": "text", "text": "分析一下"},
    #       {"type": "image", "data": "/tmp/xx.jpg", "mimeType": "image/jpeg"},
    #       {"type": "video", "data": "/tmp/yy.mp4", "mimeType": "video/mp4"},
    #   ]
    content: str | list[dict] = ""  # 消息内容

    # 线程信息
    thread_id: Optional[str] = None  # 话题 ID
    parent_id: Optional[str] = None  # 父消息 ID
    root_id: Optional[str] = None  # 根消息 ID

    # @ 状态
    mentioned_bot: bool = False  # 是否 @ 了机器人
    has_any_mention: bool = False  # 是否有任何 @

    # 工作空间
    workspace_dir: Optional[Path] = None  # 本条消息所属工作空间绝对路径

    # 元数据
    metadata: dict = field(default_factory=dict)  # Channel 特定元数据

    @property
    def is_group(self) -> bool:
        """是否为群聊

        Returns:
            bool: chat_type 为 group 时返回 True
        """
        return self.chat_type == "group"

    @property
    def is_p2p(self) -> bool:
        """是否为私聊

        Returns:
            bool: chat_type 为 p2p 时返回 True
        """
        return self.chat_type == "p2p"

    @property
    def has_thread(self) -> bool:
        """是否有话题（线程）

        Returns:
            bool: thread_id 非空时返回 True
        """
        return bool(self.thread_id)

    def to_dict(self) -> dict:
        """转换为字典

        Returns:
            dict: 包含所有字段的可序列化字典
        """
        return {
            "channel_id": self.channel_id,
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "chat_type": self.chat_type,
            "content": self.content,
            "thread_id": self.thread_id,
            "parent_id": self.parent_id,
            "root_id": self.root_id,
            "mentioned_bot": self.mentioned_bot,
            "has_any_mention": self.has_any_mention,
            "metadata": self.metadata,
        }


@dataclass
class DispatchResult:
    """dispatch_message 的统一返回结构"""

    command_handled: bool = False
    command_response: Optional[str] = None
    thinking_parts: list[str] = field(default_factory=list)
    response: str = ""
    footer: str = ""
    session_id: str = ""
    traceid: str = ""
    error: Optional[str] = None
