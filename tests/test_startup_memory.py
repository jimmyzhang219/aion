"""启动时每日记忆预读与时间锚点注入测试

验证 build_daily_memory_startup_prelude 的格式与开关、
cron 风格时间行去重，以及用户轮次时间锚点注入行为。
"""

import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


def test_untrusted_daily_block_format(tmp_path: Path) -> None:
    """当日记忆文件应被包装为不可信引用块并包含原文

    Args:
        tmp_path: pytest 临时目录（Path）

    Returns:
        None
    """
    from aion.agent.startup_memory import build_daily_memory_startup_prelude

    now_ms = int(time.time() * 1000)
    utc_day = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
    mem = tmp_path / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / f"{utc_day}.md").write_text("note alpha", encoding="utf-8")

    # 启动预读相关配置项
    cfg = {
        "startup_context_enabled": True,
        "daily_memory_days": 1,
        "max_file_bytes": 8192,
        "max_file_chars": 900,
        "max_total_chars": 5000,
    }
    out = build_daily_memory_startup_prelude(tmp_path, cfg, now_ms=now_ms)
    assert out is not None
    assert "[Startup context loaded by runtime]" in out
    assert "[Untrusted daily memory:" in out
    assert "BEGIN_QUOTED_NOTES" in out and "END_QUOTED_NOTES" in out
    assert "```text" in out
    assert "note alpha" in out


def test_startup_context_disabled() -> None:
    """startup_context_enabled 为 False 时不生成预读块

    Returns:
        None
    """
    from aion.agent.startup_memory import build_daily_memory_startup_prelude

    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        (p / "memory").mkdir()
        (p / "memory" / "2050-01-01.md").write_text("x", encoding="utf-8")
        r = build_daily_memory_startup_prelude(
            p,
            {"startup_context_enabled": False},
            now_ms=2524651200000,
        )
        assert r is None


def test_build_current_time_line(tmp_path: Path) -> None:
    """build_current_time_line 应返回合法的时间行格式

    Args:
        tmp_path: pytest 临时目录（Path）

    Returns:
        None
    """
    from aion.agent.startup_memory import build_current_time_line

    line = build_current_time_line(tmp_path, agent_id=None, now_ms=1719500000000)
    assert line.startswith("Current time:")
    assert "UTC" in line


def test_append_cron_skips_duplicate(tmp_path: Path) -> None:
    """已有 Current time 行时不重复追加 cron 时间行

    Args:
        tmp_path: pytest 临时目录（Path）

    Returns:
        None
    """
    from aion.agent.startup_memory import append_cron_style_current_time_line

    s = append_cron_style_current_time_line(
        "hello\nCurrent time: x",
        tmp_path,
        agent_id=None,
    )
    assert s == "hello\nCurrent time: x"


def test_build_current_time_line_anchor(tmp_path: Path) -> None:
    """build_current_time_line 应返回 Current time: 格式的时间行

    Args:
        tmp_path: pytest 临时目录（Path）

    Returns:
        None
    """
    from aion.agent.startup_memory import build_current_time_line

    line = build_current_time_line(tmp_path, agent_id=None, now_ms=int(time.time() * 1000))
    assert line.startswith("Current time:")


def test_time_anchor_is_first_message(tmp_path: Path) -> None:
    """Current time 应作为第一条 system 消息出现

    Args:
        tmp_path: pytest 临时目录（Path）

    Returns:
        None
    """
    from aion.agent.context import Context
    from aion.agent.context_manager import ContextManager

    ctx_mgr = ContextManager.__new__(ContextManager)
    ctx_mgr.context = Context()
    # 模拟已有 system prompt
    ctx_mgr.context.messages = [
        {"role": "system", "content": "[Bootstrap content]"},
        {"role": "system", "content": "[Skills]"},
    ]
    ctx_mgr._workspace_dir = tmp_path
    ctx_mgr._agent_id = ""

    ctx_mgr.set_time_anchor(now_ms=1719500000000)

    assert ctx_mgr.context.messages[0]["role"] == "system"
    assert ctx_mgr.context.messages[0]["content"].startswith("Current time:")
    assert ctx_mgr.context.messages[1]["content"] == "[Bootstrap content]"
