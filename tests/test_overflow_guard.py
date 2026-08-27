"""prune_context 统一裁剪 pipeline 单元测试"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aion.session.context_trimmer import (
    prune_context,
    _soft_trim_tool_results,
    _hard_clear_old_tool_results,
)
from aion.llm.tokenizer import count_message_tokens, count_tokens


def test_estimate_tokens_empty():
    assert count_message_tokens([]) == 0


def test_estimate_tokens_simple():
    msgs = [{"role": "user", "content": "hello world"}]
    assert count_message_tokens(msgs) == count_tokens("hello world")


def test_prune_context_below_threshold():
    msgs = [{"role": "user", "content": "hello"}]
    result = prune_context(msgs, context_window_tokens=200000)
    assert result == msgs


def test_prune_context_soft_trims_oversized_tool():
    big_content = "A" * 200000
    msgs = [
        {"role": "user", "content": "run"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "read"}]},
        {"role": "tool", "content": big_content, "tool_call_id": "c1"},
    ]
    result = prune_context(msgs, context_window_tokens=10000)
    tool_msg = [m for m in result if m["role"] == "tool"][0]
    assert len(tool_msg["content"]) < len(big_content)
    assert "middle omitted" in tool_msg["content"]


def test_prune_context_non_destructive():
    big_content = "A" * 200000
    msgs = [
        {"role": "user", "content": "run"},
        {"role": "tool", "content": big_content, "tool_call_id": "c1"},
    ]
    original = msgs[1]["content"]
    prune_context(msgs, context_window_tokens=10000)
    assert msgs[1]["content"] == original


def test_soft_trim_tool_results_preserves_non_tool():
    msgs = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    result = _soft_trim_tool_results(msgs)
    assert result == msgs


def test_cleanup_orphaned_removes_unmatched_tools():
    """有 assistant tool_calls 时，清除无对应 tool_call 的孤立 tool 消息。"""
    from aion.session.context_trimmer import cleanup_orphaned_tool_calls

    messages = [
        {"role": "system", "content": "prompt"},
        {"role": "user", "content": "run search and read"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "name": "search"}]},
        {"role": "tool", "content": "search result", "tool_call_id": "call_1"},
        # 孤立 tool 消息 — 没有对应的 tool_call
        {"role": "tool", "content": "orphaned result", "tool_call_id": "call_orphan"},
    ]
    result = cleanup_orphaned_tool_calls(messages)
    tool_msgs = [m for m in result if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "call_1"


def test_cleanup_orphaned_keeps_paired_tools():
    """有对应 tool_call 的 tool 消息应被保留。"""
    from aion.session.context_trimmer import cleanup_orphaned_tool_calls

    messages = [
        {"role": "system", "content": "prompt"},
        {"role": "user", "content": "search"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "name": "search"}]},
        {"role": "tool", "content": "result", "tool_call_id": "call_1"},
    ]
    result = cleanup_orphaned_tool_calls(messages)
    tool_msgs = [m for m in result if m["role"] == "tool"]
    assert len(tool_msgs) == 1


def test_cleanup_orphaned_all_tools_removed_when_no_assistant_toolcalls():
    """所有 assistant tool_calls 被移除后，孤立 tool 消息应全部清除（bug fix）。"""
    from aion.session.context_trimmer import cleanup_orphaned_tool_calls

    # 模拟 compaction 后的消息：只有 tool 结果，没有 assistant(tool_calls)
    messages = [
        {"role": "system", "content": "prompt"},
        {"role": "assistant", "content": "[Compaction summary...]", "is_compaction": True},
        {"role": "tool", "content": "result 1", "tool_call_id": "call_1"},
        {"role": "tool", "content": "result 2", "tool_call_id": "call_2"},
    ]
    result = cleanup_orphaned_tool_calls(messages)
    assert all(m["role"] != "tool" for m in result)


def test_cleanup_orphaned_removes_stale_tool_calls():
    """assistant 消息中无对应 tool 结果的 tool_calls 应被移除。"""
    from aion.session.context_trimmer import cleanup_orphaned_tool_calls

    messages = [
        {"role": "system", "content": "prompt"},
        {"role": "user", "content": "search"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "name": "search"}]},
        # 没有对应的 tool 结果消息
    ]
    result = cleanup_orphaned_tool_calls(messages)
    for m in result:
        if m["role"] == "assistant":
            assert "tool_calls" not in m or not m["tool_calls"]


def test_cleanup_orphaned_noop_clean():
    """没有 tool 消息时，清理应为空操作。"""
    from aion.session.context_trimmer import cleanup_orphaned_tool_calls

    messages = [
        {"role": "system", "content": "prompt"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    result = cleanup_orphaned_tool_calls(messages)
    assert result == messages


def test_cleanup_orphaned_preserves_compaction_marker_no_toolcalls():
    """compaction_marker (assistant, is_compaction, 无 tool_calls) 不应被修改。"""
    from aion.session.context_trimmer import cleanup_orphaned_tool_calls

    messages = [
        {"role": "system", "content": "prompt"},
        {"role": "assistant", "content": "[Compaction]", "is_compaction": True},
        {"role": "user", "content": "tell me more"},
    ]
    result = cleanup_orphaned_tool_calls(messages)
    assert len(result) == 3
    assert any(m.get("is_compaction") for m in result)


def test_hard_clear_protects_recent():
    msgs = [
        {"role": "system", "content": "prompt"},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "tool", "content": "old result", "tool_call_id": "c1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
        {"role": "tool", "content": "recent result", "tool_call_id": "c2"},
    ]
    result = _hard_clear_old_tool_results(msgs, keep_recent_assistants=1)
    assert result[3]["content"] == "[Old tool result content cleared]"
    assert result[6]["content"] == "recent result"
