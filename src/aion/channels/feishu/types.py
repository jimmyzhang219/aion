"""飞书 Channel 类型定义

设计文档: docs/design/feishu-channel.md 第 5 节
"""

from dataclasses import dataclass
from typing import Optional, List, Literal


# === 枚举类型 ===
FeishuChatType = Literal["p2p", "group", "private"]  # 聊天类型


@dataclass
class MentionTarget:
    """提及目标（@ 的用户信息）

    对应飞书消息 mentions 数组中的单个元素，用于 @ 检测与提及转发。
    """

    open_id: Optional[str] = None  # 用户 open_id
    user_id: Optional[str] = None  # 用户 user_id
    union_id: Optional[str] = None  # 用户 union_id
    name: Optional[str] = None  # 显示名
    key: Optional[str] = None  # 飞书 @ 占位 key


@dataclass
class FeishuMessageContext:
    """飞书消息上下文

    从飞书 im.message.receive_v1 事件解析得到的结构化消息。
    """

    # === 基础信息 ===
    chat_id: str  # 聊天 ID
    message_id: str  # 消息 ID
    sender_id: str  # 发送者 ID（user_id 或 open_id）
    sender_open_id: str  # 发送者 open_id
    sender_name: Optional[str] = None  # 发送者昵称

    # === 聊天类型 ===
    chat_type: FeishuChatType = "p2p"  # p2p=单聊, group=群聊, private=内部群

    # === @ 状态 ===
    mentioned_bot: bool = False  # 是否 @ 了机器人
    has_any_mention: bool = False  # 是否包含任意 @

    # === 消息内容 ===
    content: str = ""  # 解析后的纯文本
    content_type: str = "text"  # 原始消息类型
    raw_content: dict | None = None  # 原始消息内容 JSON（含 image_key / file_key 等）

    # === 线程信息 ===
    root_id: Optional[str] = None  # 根消息 ID
    parent_id: Optional[str] = None  # 父消息 ID
    thread_id: Optional[str] = None  # 话题 ID

    # === 提及列表 ===
    mentions: Optional[List[MentionTarget]] = None  # 消息中所有 @ 目标

    # === 提及转发 ===
    mention_targets: Optional[List[MentionTarget]] = None  # @ bot + 其他用户时的转发目标


@dataclass
class FeishuSendResult:
    """飞书消息发送结果

    FeishuSender 调用 IM API 成功后返回的平台侧标识。
    """

    message_id: str  # 飞书返回的消息 ID
    chat_id: str  # 接收方 chat_id 或 open_id
