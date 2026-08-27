"""M5 会话管理（Transcript / SessionLister）单元测试

测试 JSONL 转录文件的追加与持久化、空会话读取，
以及 SessionLister 的目录布局与最近会话列表。
"""

import re
from pathlib import Path
import sys

# 将项目 src 加入导入路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aion.memory.short import SessionStore as Transcript
from aion.session.manager import SessionLister


class TestTranscript:
    """Transcript JSONL 读写测试"""

    def test_create_transcript(self, tmp_path):
        """新建转录应绑定 session_id，文件名包含时间戳前缀"""
        transcript = Transcript("test-session", tmp_path)
        assert transcript.session_id == "test-session"
        # 文件名应包含时间戳前缀 + session_id
        assert re.match(
            r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_test-session\.jsonl$",
            transcript.file_path.name,
        )

    def test_append_user_message(self, tmp_path):
        """append 应按顺序写入 user/assistant 消息"""
        transcript = Transcript("test", tmp_path)
        transcript.append("Hello!", "Hi there!")

        messages = transcript.get_messages()
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello!"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "Hi there!"

    def test_persistence(self, tmp_path):
        """同一 Transcript 实例应能读回写入的消息"""
        transcript = Transcript("persist-test", tmp_path)
        transcript.append("Message 1", "Response 1")
        messages = transcript.get_messages()
        assert len(messages) == 2
        assert messages[0]["content"] == "Message 1"

    def test_get_messages_empty(self, tmp_path):
        """无写入时 get_messages 应返回空列表"""
        transcript = Transcript("empty-session", tmp_path)
        messages = transcript.get_messages()
        assert messages == []

    def test_clear_preserves_file(self, tmp_path):
        """clear() 不应删除 .jsonl 文件"""
        transcript = Transcript("clear-test", tmp_path)
        transcript.append_entry(
            {
                "type": "message",
                "message": {"role": "user", "content": "Hello", "timestamp": "2026-06-16T09:01:00"},
            }
        )
        original_path = transcript.file_path
        assert original_path.exists()
        transcript.clear()
        assert original_path.exists()

    def test_clear_empty_no_error(self, tmp_path):
        """对空 transcript 调用 clear() 不应报错"""
        transcript = Transcript("empty-clear", tmp_path)
        transcript.clear()

    def test_start_new(self, tmp_path):
        """start_new() 应创建新文件，旧文件保留不删"""
        transcript = Transcript("session-old", tmp_path)
        transcript.append_entry(
            {
                "type": "message",
                "message": {"role": "user", "content": "Old message", "timestamp": "2026-06-16T09:01:00"},
            }
        )
        old_path = transcript.file_path
        assert old_path.exists()

        transcript.start_new("session-new")
        new_path = transcript.file_path
        # 新文件名应不同
        assert new_path != old_path
        assert "session-new" in new_path.name
        # 旧文件应保留
        assert old_path.exists()
        # 新文件应可写入
        transcript.append_entry(
            {
                "type": "message",
                "message": {"role": "user", "content": "New message", "timestamp": "2026-06-16T09:02:00"},
            }
        )
        messages = transcript.get_messages()
        assert len(messages) == 1
        assert messages[0]["content"] == "New message"


class TestSessionLister:
    """SessionLister 会话目录与默认会话测试"""

    def test_create_manager(self, tmp_path):
        """创建管理器时应建立 sessions 子目录

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        manager = SessionLister(tmp_path)
        assert manager.sessions_dir == tmp_path / "agents" / "main" / "sessions"
        assert manager.sessions_dir.is_dir()

    def test_list_recent_empty(self, tmp_path):
        """无会话文件时 list_recent 应返回空列表

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        manager = SessionLister(tmp_path)
        sessions = manager.list_recent(limit=10)
        assert sessions == []

    def test_list_recent_with_sessions(self, tmp_path):
        """存在多个 jsonl 时应列出至少两条最近会话

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        sess_dir = tmp_path / "agents" / "main" / "sessions"
        sess_dir.mkdir(parents=True, exist_ok=True)
        t1 = Transcript("session-1", sess_dir)
        t1.append("First session", "Response 1")

        t2 = Transcript("session-2", sess_dir)
        t2.append("Second session", "Response 2")

        manager = SessionLister(tmp_path)
        sessions = manager.list_recent(limit=10)
        assert len(sessions) >= 2
