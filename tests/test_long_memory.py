"""tests/test_long_memory.py"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aion.memory.long import LongTermStore


class TestLongTermStore:
    def test_creates_in_agent_memory_dir(self, tmp_path):
        store = LongTermStore(tmp_path, agent_id="main")
        expected = tmp_path / "agents" / "main" / "memory" / "MEMORY.md"
        assert store.file_path == expected

    def test_initial_creates_file(self, tmp_path):
        store = LongTermStore(tmp_path, agent_id="main")
        assert store.file_path.exists()
        assert "# 永久记忆" in store.file_path.read_text()

    def test_overwrite_replaces_content(self, tmp_path):
        store = LongTermStore(tmp_path, agent_id="main")
        store.overwrite("## 用户偏好\n- 喜欢简洁架构\n- 资深TypeScript开发者")
        content = store.read_all()
        assert "用户偏好" in content
        assert "资深TypeScript开发者" in content

    def test_overwrite_triggers_callback(self, tmp_path):
        callback_called = False

        def on_write(path, content):
            nonlocal callback_called
            callback_called = True

        store = LongTermStore(tmp_path, agent_id="main", on_write=on_write)
        store.overwrite("新内容")
        assert callback_called
