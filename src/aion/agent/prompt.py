"""System prompt 构建

== 唯一入口 ==
build_system_prompt() 是 System Prompt 的唯一构建方法。
AgentLoop 通过委托调用此函数，不再自建内联实现。

职责：拼接 Bootstrap + Skills + Startup Context + Recovery + Reasoning 各段，
返回 section 字符串列表，每段对应一条 system role 消息。
"""

from pathlib import Path
from typing import Optional

from .bootstrap import build_bootstrap_markdown_for_system_prompt
from .context import build_startup_context
from aion.skills import get_skills_loader


def build_system_prompt(
    workspace_dir: Path,
    agent_id: Optional[str] = None,
    memory_config: Optional[dict] = None,
    is_subagent: bool = False,
    subagent_system_prompt: str = "",
    is_leader: bool = True,
) -> list[str]:
    """构建 system prompt section 列表。

    子 agent（is_subagent=True）使用 minimal 模式：仅注入 Subagent Context
    与 Reasoning Format，跳过 Bootstrap/Skills/Startup/Recovery。

    返回 list[str]，每项对应一条 section。调用方（AgentLoop）将每项包装为
    {"role": "system", "content": section} 注入 context.messages。

    Args:
        workspace_dir: 工作空间根目录
        agent_id: Agent ID
        memory_config: 记忆相关配置
        is_subagent: 是否为子 agent 模式
        subagent_system_prompt: 子 agent 专用 prompt
        is_leader: 是否为 leader agent（仅 leader 加载 Agent Teams 成员列表）

    Returns:
        system prompt section 字符串列表
    """
    if memory_config is None:
        memory_config = {}

    if is_subagent and subagent_system_prompt:
        # Minimal 模式：仅 Subagent Context + Reasoning Format
        sections: list[str] = [subagent_system_prompt]
        sections.append(_build_reasoning_section())
        return [s.strip() for s in sections if s and s.strip()]

    # 完整模式：Bootstrap + Skills + Startup + Recovery + Reasoning
    bootstrap = build_bootstrap_markdown_for_system_prompt(
        workspace_dir=workspace_dir,
        agent_id=agent_id,
        max_chars_per_file=memory_config.get("bootstrap_max_chars", 20_000),
        total_max_chars=memory_config.get("bootstrap_total_max_chars", 150_000),
    )

    skills_loader = get_skills_loader(workspace_dir)
    skills_prompt = skills_loader.build_prompt() or ""

    startup_context = build_startup_context(
        workspace_dir=workspace_dir,
        agent_id=agent_id,
        memory_config=memory_config,
    )

    reasoning_section = _build_reasoning_section()

    recovery_section = (
        "### 大文件读取与截断恢复\n\n"
        "工具结果可能因超出上下文预算而被截断。若看到 `[... N more characters truncated]` "
        "或 `[Read output capped at X. Use offset=Y to continue.]`，说明内容已被缩减。\n\n"
        "- 要读取更大的文件，使用 `read` 工具的 `offset` 和 `limit` 参数分段读取\n"
        "- 不要使用 `exec` 的 `cat` 或其他 shell 命令读取大文件全文，应使用 `read` 工具分段读取\n"
    )

    tool_call_rule = _build_tool_call_rule_section()
    # bootstrap_cleanup = _build_bootstrap_cleanup_instruction()

    # Agent Teams（仅 leader 加载 member agent 列表）
    agent_teams_section = ""
    if is_leader:
        agent_teams_section = _build_agent_teams_section(
            workspace_dir=workspace_dir,
            agent_id=agent_id or "",
        )

    sections = [
        bootstrap,
        skills_prompt,
        startup_context,
        recovery_section,
        tool_call_rule,
        agent_teams_section,
        reasoning_section,
    ]

    return [s.strip() for s in sections if s and s.strip()]


def _build_agent_teams_section(workspace_dir: Path, agent_id: str) -> str:
    """构建 Agent Teams section，列出当前 workspace 中的 member agent。

    从 aion.json 读取 workspace agents 配置，过滤出当前 agent 以外的成员。
    """
    from aion.config.loader import load_config

    try:
        config = load_config()
        ws_name = workspace_dir.name
        ws_config = config.get_workspace(ws_name)
        if not ws_config:
            return ""

        members: list[tuple[str, str]] = []
        for name, cfg in ws_config.agents.items():
            if name == "leader":
                continue
            if not isinstance(cfg, dict):
                continue
            if name == agent_id:
                continue
            desc = cfg.get("description", "") or ""
            members.append((name, desc))

        if not members:
            return ""

        lines = [
            "## Agent Teams",
            "",
            "你所在的团队有以下成员可供调用：",
            "",
        ]
        for name, desc in members:
            if desc:
                lines.append(f"- `{name}`: {desc}")
            else:
                lines.append(f"- `{name}`")
        lines.extend(
            [
                "",
                "你可以通过 `sessions_send` 工具将任务分配给团队成员。",
                "团队成员会独立执行任务并返回结果。",
            ]
        )
        return "\n".join(lines)
    except Exception:
        return ""


def _build_tool_call_rule_section() -> str:
    """构建工具调用约束与知识边界段。"""
    return (
        "### 工具调用约束\n\n"
        "任何需要调用工具的操作（如读写文件、执行命令、搜索等）必须通过模型 tool call 响应参数实际执行，"
        "调用工具的意图必须与模型 tool call 里的工具一致。"
        "禁止仅思考不执行或仅用自然语言描述意图代替真正的工具调用。\n"
        "先输出 tool call，确认执行成功，再回复结果。\n"
        "\n"
        "你的训练知识存在截止日期且可能不完整。"
        "在提供事实性信息前，优先使用可用工具获取或验证。\n"
        "工具执行结果具有最高优先级——当工具结果与你的训练知识不一致时，以工具结果为准。\n"
    )


# def _build_bootstrap_cleanup_instruction() -> str:
#     """构建引导文件处理指令段，约束 LLM 不向用户提及内部配置文件名。"""
#     return (
#         "### 引导文件处理\n\n"
#         "系统在 Project Context 段展示了一系列引导配置文件。这些文件是系统内部配置，"
#         "除非用户主动询问，否则禁止向用户提及它们的文件名或路径。\n\n"
#         "若引导仪式文件（workspace_bootstrap.md / agent_bootstrap.md）已完成使命，"
#         "使用 delete 工具静默清理即可，无需询问用户，无需提及。\n"
#     )


def _build_reasoning_section() -> str:
    """构建思维链(CoT)引导 section。

    Returns:
        Reasoning Format Markdown 段
    """
    return (
        "## Reasoning Format\n"
        "ALL internal reasoning MUST be inside <think>...</think>.\n"
        "Do not output any analysis outside <think>.\n"
        "Format every reply as <think>...</think> then <final>...</final>, with no other text.\n"
        "Only the final user-visible reply may appear inside <final>.\n"
        "Only text inside <final> is shown to the user; everything else is discarded and never seen by the user.\n"
        "Example:\n"
        "<think>Short internal reasoning.</think>\n"
        "<final>Hey there! What would you like to do next?</final>\n"
    )
