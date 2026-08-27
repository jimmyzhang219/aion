"""SubagentSession 单元测试"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aion.agent.subagent.session import SubagentSession


def test_session_creates_file():
    """创建 SubagentSession 应生成带 -subagent.jsonl 后缀的文件"""
    with tempfile.TemporaryDirectory() as tmp:
        session = SubagentSession("sub-abc123", "main", Path(tmp))
        assert session.file_path.name.endswith("-subagent.jsonl")
        assert session.file_path.exists()
        assert "sub-abc123" in session.file_path.name


def test_append_messages():
    """append_messages 应追加写入 JSONL 行"""
    with tempfile.TemporaryDirectory() as tmp:
        session = SubagentSession("sub-xyz", "main", Path(tmp))
        session.append_messages(
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hello"},
            ]
        )
        lines = session.file_path.read_text().strip().split("\n")
        assert len(lines) == 2
        entry1 = json.loads(lines[0])
        assert entry1["type"] == "message"
        assert entry1["message"]["role"] == "system"


def test_mark_deleted():
    """mark_deleted 应将文件重命名为 .jsonl.delete"""
    with tempfile.TemporaryDirectory() as tmp:
        session = SubagentSession("sub-abc", "main", Path(tmp))
        original = session.file_path
        session.mark_deleted()
        assert not original.exists()
        assert (original.with_name(original.name + ".delete")).exists()


def test_mark_deleted_idempotent():
    """多次 mark_deleted 不应报错"""
    with tempfile.TemporaryDirectory() as tmp:
        session = SubagentSession("sub-abc", "main", Path(tmp))
        session.mark_deleted()
        session.mark_deleted()  # 不应抛异常


def test_append_unicode():
    """append_messages 应正确处理非 ASCII 内容"""
    with tempfile.TemporaryDirectory() as tmp:
        session = SubagentSession("sub-unicode", "main", Path(tmp))
        session.append_messages([{"role": "user", "content": "你好世界"}])
        raw = session.file_path.read_text("utf-8")
        assert "你好世界" in raw


def test_append_twice():
    """两次 append_messages 应追加（4 行而非覆盖）"""
    with tempfile.TemporaryDirectory() as tmp:
        session = SubagentSession("sub-twice", "main", Path(tmp))
        session.append_messages([{"role": "user", "content": "first"}])
        session.append_messages([{"role": "user", "content": "second"}])
        lines = session.file_path.read_text().strip().split("\n")
        assert len(lines) == 2
