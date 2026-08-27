"""ContextVar 上下文 — set/get/reset"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_context_var_default_is_none():
    from aion.tools._context import get_agent_loop

    assert get_agent_loop() is None


def test_context_var_set_get():
    from aion.tools._context import get_agent_loop, set_agent_loop, reset_agent_loop

    token = set_agent_loop("test")
    assert get_agent_loop() == "test"
    reset_agent_loop(token)
    assert get_agent_loop() is None


def test_context_var_isolation():
    """不同上下文的 set/reset 互不干扰"""
    import contextvars
    from aion.tools._context import get_agent_loop, set_agent_loop, reset_agent_loop

    cv = contextvars.copy_context()
    token = set_agent_loop("ctx1")
    assert get_agent_loop() == "ctx1"
    reset_agent_loop(token)
    assert get_agent_loop() is None

    # 另一个上下文不受影响
    assert cv.run(get_agent_loop) is None
