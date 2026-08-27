"""Typing Indicator 管理模块

管理飞书消息的「正在输入」反应（Typing reaction）状态。
使用内存存储 reaction_id，支持 TTL 自动过期，避免重复添加或泄漏。
"""

import threading
import time
from typing import Optional, Any


class TypingIndicatorStore:
    """线程安全的 Typing Indicator 存储，带 TTL 清理

    每条消息对应一个 reaction 记录，超过 TTL 后自动视为无效。
    """

    TTL_SECONDS = 60  # 60 秒后自动清理过期条目

    def __init__(self):
        """初始化空存储与线程锁"""
        self._lock = threading.Lock()
        # message_id -> {reaction_id, chat_id, added_at}
        self._store: dict[str, dict[str, Any]] = {}

    def add(self, message_id: str, reaction_id: str, chat_id: str) -> None:
        """记录一条 Typing reaction

        Args:
            message_id: 飞书消息 ID
            reaction_id: 反应 ID，用于后续删除
            chat_id: 聊天 ID

        Returns:
            None
        """
        with self._lock:
            self._store[message_id] = {
                "reaction_id": reaction_id,
                "chat_id": chat_id,
                "added_at": time.time(),
            }

    def get(self, message_id: str) -> Optional[dict[str, Any]]:
        """获取消息的 Typing 记录

        若记录已超过 TTL，会先删除再返回 None。

        Args:
            message_id: 飞书消息 ID

        Returns:
            Optional[dict]: 包含 reaction_id、chat_id、added_at 的字典；不存在或已过期时返回 None
        """
        with self._lock:
            entry = self._store.get(message_id)
            if entry and time.time() - entry["added_at"] > self.TTL_SECONDS:
                del self._store[message_id]
                return None
            return entry

    def remove(self, message_id: str) -> Optional[dict[str, Any]]:
        """移除并返回 Typing 记录

        Args:
            message_id: 飞书消息 ID

        Returns:
            Optional[dict]: 被移除的记录；不存在时返回 None
        """
        with self._lock:
            return self._store.pop(message_id, None)


# 全局单例，供 client.add/remove_typing_indicator 共享
_typing_store = TypingIndicatorStore()


def get_typing_store() -> TypingIndicatorStore:
    """获取全局 Typing Indicator 存储单例

    Returns:
        TypingIndicatorStore: 线程安全的存储实例
    """
    return _typing_store
