"""tests/test_short_memory.py"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aion.memory.short import SessionStore


class TestSessionStore:
    def test_new_session_creates_file(self, tmp_path):
        store = SessionStore("test-session-id", tmp_path)
        assert store.file_path.exists()
        assert "test-session-id" in store.file_path.name
        assert store.file_path.suffix == ".jsonl"

    def test_reuses_existing_session(self, tmp_path):
        store1 = SessionStore("test-session-id", tmp_path)
        store2 = SessionStore("test-session-id", tmp_path)
        assert store1.file_path == store2.file_path

    def test_append_entry_writes_jsonl(self, tmp_path):
        store = SessionStore("test-session-id", tmp_path)
        store.append_entry(
            {
                "type": "message",
                "message": {"role": "user", "content": "你好", "timestamp": "2026-06-16T09:01:43.416188"},
            }
        )
        entries = store.get_all_entries()
        assert len(entries) == 1
        assert entries[0]["type"] == "message"
        assert entries[0]["message"]["content"] == "你好"

    def test_append_writes_user_assistant_batch(self, tmp_path):
        store = SessionStore("test-session-id", tmp_path)
        store.append("user message", "assistant response")
        entries = store.get_all_entries()
        assert len(entries) == 2
        assert entries[0]["message"]["role"] == "user"
        assert entries[1]["message"]["role"] == "assistant"

    def test_get_compaction_boundary(self, tmp_path):
        store = SessionStore("test-session-id", tmp_path)
        store.append_entry(
            {
                "type": "message",
                "message": {"role": "user", "content": "第一轮对话", "timestamp": "2026-06-16T09:01:00"},
            }
        )
        store.append_entry(
            {
                "type": "compaction",
                "message": {"role": "system", "content": "摘要", "timestamp": "2026-06-16T09:31:00"},
            }
        )
        store.append_entry(
            {
                "type": "message",
                "message": {"role": "user", "content": "第二轮对话", "timestamp": "2026-06-16T09:32:00"},
            }
        )
        compaction, subsequent = store.get_compaction_boundary()
        assert compaction is not None
        assert compaction["type"] == "compaction"
        assert len(subsequent) == 1
        assert subsequent[0]["content"] == "第二轮对话"

    def test_get_compaction_boundary_no_compaction(self, tmp_path):
        store = SessionStore("test-session-id", tmp_path)
        store.append_entry(
            {
                "type": "message",
                "message": {"role": "user", "content": "你好", "timestamp": "2026-06-16T09:01:00"},
            }
        )
        compaction, subsequent = store.get_compaction_boundary()
        assert compaction is None
        assert len(subsequent) == 1

    def test_start_new_replaces_file(self, tmp_path):
        store = SessionStore("test-session-id", tmp_path)
        old_path = store.file_path
        store.start_new("new-session-id")
        assert store.file_path != old_path
        assert "new-session-id" in store.file_path.name

    def test_get_messages_skips_compaction(self, tmp_path):
        store = SessionStore("test-session-id", tmp_path)
        store.append_entry(
            {
                "type": "compaction",
                "message": {"role": "system", "content": "摘要", "timestamp": "2026-06-16T09:31:00"},
            }
        )
        store.append_entry(
            {
                "type": "message",
                "message": {"role": "user", "content": "你好", "timestamp": "2026-06-16T09:32:00"},
            }
        )
        msgs = store.get_messages()
        assert len(msgs) == 1
        assert msgs[0]["content"] == "你好"
