"""Subagent system prompt 构建

为派生子 agent 生成专用 system prompt 段，约束任务范围与嵌套 spawn 规则。
"""

from __future__ import annotations


def build_subagent_system_prompt(
    task: str,
    child_session_id: str,
    parent_session_id: str,
    agent_id: str | None = None,
) -> str:
    """构建子 agent 专用的 system prompt 段。

    Args:
        task: 要执行的任务描述
        child_session_id: 子 session ID（自己）
        parent_session_id: 父 session ID
        agent_id: 目标 agent ID（当前未写入 prompt 正文，保留扩展）

    Returns:
        Markdown 格式的 Subagent Context 段落
    """

    lines = [
        "## Subagent Context",
        "",
        "你是被**父 agent**派生的**子 agent**，负责完成以下任务：",
        "",
        "### 任务",
        f"{task}",
        "",
        "### 规则",
        "1. **只做你的任务**，不要做额外的事",
        "2. **完成后结果自动返回给父 agent**（无需你主动汇报）",
        "3. 这是临时会话，不要发起心跳、定时任务或后台任务",
        f"4. 父 session: {parent_session_id}",
        f"5. 你的 session: {child_session_id}",
        "",
        "### 禁止",
        "- 不要与用户直接对话（你没有用户交互通道）",
        "- **不可派生子 agent**（所有子任务自己完成）",
        "- 不要伪装成父 agent",
        "- 不要做任务范围之外的任何事",
        "",
        "### 输出格式",
        "直接输出任务结果，保持简洁。说明完成了什么、关键发现或产出。",
        "父 agent 会收到你的完整输出并合并到最终回复中。",
        "",
    ]

    return "\n".join(lines)
