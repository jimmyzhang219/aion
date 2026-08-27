"""tests/test_mid_memory.py"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aion.memory.mid import DailyFileStore


class TestDailyFileStore:
    def test_creates_in_agent_memory_dir(self, tmp_path):
        store = DailyFileStore(tmp_path, agent_id="main")
        assert store.memory_dir == tmp_path / "agents" / "main" / "memory"

    def test_append_creates_todays_file(self, tmp_path):
        from datetime import datetime

        date_str = datetime.now().strftime("%Y-%m-%d")
        store = DailyFileStore(tmp_path, agent_id="main")
        store.append("## 今日摘要\n\n用户喜欢TypeScript")
        expected = tmp_path / "agents" / "main" / "memory" / f"{date_str}.md"
        assert expected.exists()
        content = expected.read_text()
        assert "用户喜欢TypeScript" in content

    def test_overwrite_replaces_content(self, tmp_path):
        from datetime import datetime

        date_str = datetime.now().strftime("%Y-%m-%d")
        store = DailyFileStore(tmp_path, agent_id="main")
        store.append("旧内容")
        store.overwrite("新内容")
        content = (tmp_path / "agents" / "main" / "memory" / f"{date_str}.md").read_text()
        assert content == "新内容\n"

    def test_append_triggers_on_write(self, tmp_path):
        from unittest.mock import MagicMock

        on_write = MagicMock()
        store = DailyFileStore(tmp_path, agent_id="main", on_write=on_write)
        store.append("Test memory")
        on_write.assert_called_once()
