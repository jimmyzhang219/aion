"""Context trimming for token window overflow — unified prune_context pipeline + orphaned tool call repair.

Estimates message token usage before LLM invocation and progressively trims messages
when approaching the context window limit.

Three-layer defense:
  Layer 1: soft_trim (keep head 1500 + tail 1500 chars, mark hard-truncated for single-line overage)
  Layer 2: hard_clear (replace old tool results with placeholders)
  Layer 3: drop_oldest (drop oldest non-protected messages one by one)

Also provides cleanup_orphaned_tool_calls() to fix broken tool_call/tool pairs after pruning.
"""

from ..llm.tokenizer import count_message_tokens

SAFETY_RATIO = 0.9


def prune_context(
    messages: list[dict],
    context_window_tokens: int,
    *,
    keep_system: bool = True,
    keep_recent_assistants: int = 3,
    safety_ratio: float = SAFETY_RATIO,
) -> list[dict]:
    """统一上下文裁剪入口。

    估算 token → 超阈值时渐进裁剪 tool 结果 → 仍超则裁剪旧消息。
    返回新列表，不修改原 messages。
    """
    threshold = int(context_window_tokens * safety_ratio)
    estimated = count_message_tokens(messages)

    if estimated <= threshold:
        return list(messages)

    result = list(messages)

    result = _soft_trim_tool_results(result)
    if count_message_tokens(result) <= threshold:
        return result

    result = _hard_clear_old_tool_results(result, keep_recent_assistants)
    if count_message_tokens(result) <= threshold:
        return result

    result = _drop_oldest_messages(result, keep_system, keep_recent_assistants, threshold)
    return result


def _soft_trim_tool_results(messages: list[dict]) -> list[dict]:
    """对 tool 消息做 soft-trim：保留 head 1500 + tail 1500 chars。

    若 head 段不含换行符，说明首行超过 1500 字符 → 硬截断，
    标记为 hard-truncated 并提示模型使用 read 工具读取源文件。
    """
    result = []
    for msg in messages:
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > 3000:
                msg_copy = dict(msg)
                head = content[:1500]
                tail = content[-1500:]
                if "\n" not in head:
                    marker = (
                        "\n... [middle omitted - hard-truncated (line > 1500 chars). "
                        "Use read tool on source file to recover, offset cannot help.] ...\n"
                    )
                else:
                    marker = "\n... [middle omitted] ...\n"
                msg_copy["content"] = head + marker + tail
                result.append(msg_copy)
                continue
        result.append(msg)
    return result


def _hard_clear_old_tool_results(
    messages: list[dict],
    keep_recent_assistants: int,
) -> list[dict]:
    """将旧的 tool 结果替换为占位符，保护最近 N 个 assistant turn 的 tool 结果。"""
    assistant_count = 0
    cutoff_index = 0
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "assistant":
            assistant_count += 1
            if assistant_count >= keep_recent_assistants:
                cutoff_index = i
                break

    result = []
    for i, msg in enumerate(messages):
        if i < cutoff_index and msg.get("role") == "tool":
            msg_copy = dict(msg)
            msg_copy["content"] = "[Old tool result content cleared]"
            result.append(msg_copy)
        else:
            result.append(msg)
    return result


def _drop_oldest_messages(
    messages: list[dict],
    keep_system: bool,
    keep_recent_assistants: int,
    threshold: int,
) -> list[dict]:
    """从旧到新逐条移除非保护消息，直到估算 token 数降到阈值以下。"""
    protected_start = 0
    if keep_system and messages and messages[0].get("role") == "system":
        protected_start = 1

    assistant_count = 0
    protected_end = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "assistant":
            assistant_count += 1
            if assistant_count >= keep_recent_assistants:
                protected_end = i
                break

    if protected_start >= protected_end:
        return messages

    for drop_idx in range(protected_start, protected_end):
        candidate = messages[:drop_idx] + messages[drop_idx + 1 :]
        if count_message_tokens(candidate) <= threshold:
            return candidate

    return messages


def cleanup_orphaned_tool_calls(messages: list[dict]) -> list[dict]:
    """清理因裁剪导致的孤立 tool_calls 与 tool 结果消息。

    Pruning 可能破坏 assistant tool_calls 与 tool 消息的配对。本函数：
    1. 收集所有 assistant 消息中有效的 tool_call id
    2. 移除没有对应 tool_call 的孤立 tool 消息
    3. 移除没有对应 tool 结果的孤立 tool_calls 条目

    Args:
        messages: 待清理的消息列表（会被原地修改）

    Returns:
        清理后的同一 messages 列表引用
    """
    valid_call_ids: set[str] = set()
    for m in messages:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls", []):
                cid = str(tc.get("id", "") or tc.get("name", ""))
                if cid:
                    valid_call_ids.add(cid)

    tool_ids: set[str] = set()
    for m in messages:
        if m.get("role") == "tool":
            tcid = m.get("tool_call_id", "")
            if tcid:
                tool_ids.add(str(tcid))

    if not tool_ids and not valid_call_ids:
        # 没有 tool 消息也没有 tool_calls 需要清理
        return messages

    if valid_call_ids:
        # 有 assistant tool_calls → 只移除没有对应 tool_call 的 tool 消息
        messages[:] = [
            m
            for m in messages
            if not (m.get("role") == "tool" and str(m.get("tool_call_id", "")) not in valid_call_ids)
        ]
    else:
        # 没有 assistant tool_calls（全被 compaction/pruning 移除了）→ 所有 tool 消息都是孤立的
        messages[:] = [m for m in messages if m.get("role") != "tool"]

    tool_ids = {str(m["tool_call_id"]) for m in messages if m.get("role") == "tool" and m.get("tool_call_id")}

    for m in messages:
        if m.get("role") != "assistant":
            continue
        tcs = m.get("tool_calls")
        if not tcs:
            continue
        if not tool_ids:
            del m["tool_calls"]
            continue
        surviving = [tc for tc in tcs if str(tc.get("id", "") or tc.get("name", "")) in tool_ids]
        if surviving:
            m["tool_calls"] = surviving
        else:
            del m["tool_calls"]

    return messages
