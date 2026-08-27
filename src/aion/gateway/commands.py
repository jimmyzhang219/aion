"""Slash 命令处理器

处理 /new、/switch、/workspaces、/status、/help 等用户命令。
所有命令函数接收所需参数，不依赖模块级状态（除 _session_loops 作为参数传入）。

命令处理流程：
1. dispatch_message 接收用户消息
2. 检测到 / 开头 → 调用 handle_slash_command
3. 匹配命令名 → 执行对应处理函数
4. 返回 DispatchResult 或 None（未知命令 → 交给 Agent）
"""

from typing import Optional, TYPE_CHECKING, cast, Literal

from ..channels.types import DispatchResult
from ..config.loader import resolve_workspace_dir, save_config
from ..log import get_trace_logger
from ..session.binder import SessionBinder

if TYPE_CHECKING:
    from aion.config.schema import Config
    from aion.channels.types import MessageContext
    from aion.channels.adapters import ChannelPlugin

logger = get_trace_logger(__name__)


async def handle_slash_command(
    ctx,
    channel,
    config,
    workspace_name: str,
    agent_id: str,
    session_loops=None,  # type: ignore  # 已废弃，保留签名兼容
    session_queues=None,
) -> Optional[DispatchResult]:
    """处理 slash command，返回 DispatchResult 或 None（表示非命令）。

    Args:
        ctx: MessageContext
        channel: ChannelPlugin 实例
        config: 应用配置
        workspace_name: 工作空间名称
        agent_id: Agent ID
        session_loops: 已废弃，保留签名兼容

    Returns:
        DispatchResult 或 None（未知命令）
    """
    content = ctx.content.lstrip("/")
    parts = content.split(None, 1)
    command = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    if command == "new":
        return await _cmd_new(ctx, channel, config, workspace_name, agent_id, session_loops)
    elif command == "switch":
        return await _cmd_switch(ctx, args, channel, config, workspace_name, agent_id, session_loops)
    elif command == "workspaces":
        return _cmd_workspaces(config)
    elif command == "status":
        return _cmd_status(ctx, channel, config, workspace_name, agent_id, session_loops, session_queues)
    elif command == "mode":
        return await _cmd_mode(args, config, session_loops, ctx, channel, workspace_name, agent_id)
    elif command == "help":
        return _cmd_help()
    else:
        return None  # 未知命令，交给 Agent


async def _cmd_new(ctx, channel, config, workspace_name, agent_id, session_loops=None):  # noqa: ARG001 (config 未使用，保留签名统一)
    session_key = channel.build_session_key(ctx, agent_id)
    workspace_dir = resolve_workspace_dir(workspace_name)
    binder = SessionBinder(workspace_dir)
    new_session_id = binder.refresh_binding(session_key)

    return DispatchResult(
        command_handled=True,
        command_response="好的，开始新对话！",
        session_id=new_session_id,
    )


async def _cmd_switch(ctx, args, channel, config, workspace_name, agent_id, session_loops=None):
    target_ws = args.strip()
    if not target_ws:
        return DispatchResult(
            command_handled=True,
            command_response="用法：/switch <工作空间名>",
        )

    scopes = config.workspaces.scopes
    found = any(target_ws in s for s in scopes)
    if not found:
        names = [list(s.keys())[0] for s in scopes]
        return DispatchResult(
            command_handled=True,
            command_response=f"工作空间不存在：{target_ws}\n可用：{', '.join(names)}",
        )

    config.workspaces.current = target_ws
    config_dict = config.model_dump()
    save_config(config_dict)

    return DispatchResult(
        command_handled=True,
        command_response=f"✓ 已切换到工作空间：{target_ws}",
    )


def _cmd_workspaces(config):
    scopes = config.workspaces.scopes
    current_ws = config.workspaces.current
    lines = ["**可用工作空间：**"]
    for scope in scopes:
        for ws_name in scope.keys():
            marker = " (当前)" if ws_name == current_ws else ""
            lines.append(f"  • {ws_name}{marker}")
    return DispatchResult(command_handled=True, command_response="\n".join(lines))


def _cmd_status(ctx, channel, config, workspace_name, agent_id, session_loops=None, session_queues=None):
    """显示系统状态及所有 session 队列情况。"""
    from aion import __version__ as version

    current_ws = config.workspaces.current

    lines = [
        "**状态信息：**",
        f"版本：{version}",
        f"工作空间：{current_ws}",
        "",
    ]

    # ── 会话队列状态 ──
    queue_status = session_queues.get_all_status() if session_queues else {}
    if queue_status:
        lines.append("**会话队列：**")
        for sid, info in queue_status.items():
            parts = [f"  `{sid}`"]
            if info["processing"]:
                parts.append("→ 处理中")
            else:
                parts.append("→ 等待中")
            qs = info["queue_size"]
            if qs > 0:
                parts.append(f"(队列: {qs})")
            lines.append(" ".join(parts))
    else:
        lines.append("**会话队列：**（无活跃会话）")

    return DispatchResult(
        command_handled=True,
        command_response="\n".join(lines),
    )


async def _cmd_mode(
    args: str,
    config: "Config",
    session_loops=None,  # 已废弃
    ctx: Optional["MessageContext"] = None,
    channel: Optional["ChannelPlugin"] = None,
    workspace_name: str = "",
    agent_id: str = "main",
) -> DispatchResult:
    """处理 /mode 命令：显示或切换执行模式。"""
    ws_config = config.get_workspace(config.workspaces.current)
    if not ws_config:
        return DispatchResult(
            command_handled=True,
            command_response="无法获取当前工作空间配置。",
        )

    if not args:
        return DispatchResult(
            command_handled=True,
            command_response=f"当前模式：**{ws_config.execution_mode}**\n可用：`react`、`plan`\n切换：`/mode react` 或 `/mode plan`",
        )

    mode = args.strip().lower()
    if mode not in ("react", "plan"):
        return DispatchResult(
            command_handled=True,
            command_response=f"不支持的模式：`{mode}`\n可用模式：`react`、`plan`",
        )

    # AgentLoop 不再缓存，无需清理中断状态
    # （中断状态在消息处理后自然结束）
    ws_config.execution_mode = cast(Literal["react", "plan"], mode)
    config_dict = config.model_dump()
    save_config(config_dict)
    return DispatchResult(
        command_handled=True,
        command_response=f"✓ 已切换到 **{mode}** 模式",
    )


def _cmd_help():
    return DispatchResult(
        command_handled=True,
        command_response=(
            "**可用命令：**\n"
            "/new — 开始新会话（清空上下文）\n"
            "/workspaces — 显示所有工作空间\n"
            "/status — 显示当前状态信息\n"
            "/switch <工作空间> — 切换工作空间\n"
            "/mode [react|plan] — 显示/切换执行模式\n"
            "/help — 显示此帮助"
        ),
    )
