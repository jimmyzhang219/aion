"""飞书 Session 绑定

设计文档: docs/design/feishu-channel.md 第 7 节
"""

from typing import Optional

from .types import FeishuChatType


def build_feishu_session_key(
    agent_id: str,
    chat_type: FeishuChatType,
    chat_id: str,
    thread_id: Optional[str] = None,
    sender_id: Optional[str] = None,
) -> str:
    """构建飞书 Session Key

    Session Key 格式: agent:<agent_id>:feishu:<peer_kind>:<peer_id>

    Args:
        agent_id: Agent ID（如 "main"）
        chat_type: 聊天类型（p2p/group/private）
        chat_id: 聊天 ID
        thread_id: 线程 ID（话题）
        sender_id: 发送者 ID（用于 group_topic_sender 模式）

    Returns:
        Session Key 字符串
    """
    if chat_type == "p2p":
        # 私聊：按用户绑定
        return f"agent:{agent_id}:feishu:p2p:{chat_id}"

    elif chat_type == "group":
        if thread_id:
            # 群聊 + 话题：按话题绑定
            if sender_id:
                # 群聊 + 话题 + 发送者：最细粒度
                return f"agent:{agent_id}:feishu:group_topic_sender:{chat_id}:{thread_id}:{sender_id}"
            return f"agent:{agent_id}:feishu:group_topic:{chat_id}:{thread_id}"
        # 纯群聊：按群绑定
        return f"agent:{agent_id}:feishu:group:{chat_id}"

    elif chat_type == "private":
        # 内部群/私群，类似于 p2p
        return f"agent:{agent_id}:feishu:p2p:{chat_id}"

    else:
        # 未知类型，默认按 chat_id
        return f"agent:{agent_id}:feishu:unknown:{chat_id}"
