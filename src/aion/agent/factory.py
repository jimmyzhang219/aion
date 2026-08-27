"""AgentLoop 工厂

从配置中读取 workspace、leader agent、LLM、MCP、memory 等设置，
创建或返回缓存的 AgentLoop 实例。
"""

from typing import Optional

from ..config.defaults import PROVIDER_DEFAULTS

from ..config.loader import load_config, resolve_workspace_dir
from ..llm.factory import create_llm
from ..log import get_trace_logger
from .loop import AgentLoop

logger = get_trace_logger(__name__)


def get_or_create_agent_loop(
    session_id: str,
    workspace_name: Optional[str] = None,
    session_loops: Optional[dict] = None,
) -> AgentLoop:
    """获取或创建指定 Session 的 AgentLoop。

    每个 Session 有独立的 AgentLoop，复用历史上下文。
    首次创建时会从配置中读取 LLM、工作空间、记忆等配置。

    Args:
        session_id: Session ID
        workspace_name: 可选，指定工作空间名称
        session_loops: session -> AgentLoop 缓存字典。为 None 时仅创建不缓存。

    Returns:
        AgentLoop 实例

    Raises:
        ValueError: 当没有配置工作空间或 LLM 时抛出
    """
    loops = session_loops

    if loops is not None and (existing := loops.get(session_id)) is not None:
        logger.debug(f"reusing existing AgentLoop session={session_id}")
        return existing

    config = load_config()
    if workspace_name:
        ws_config = config.get_workspace(workspace_name)
    else:
        ws_config = config.get_current_workspace()
    if not ws_config:
        raise ValueError("No current workspace configured")

    leader_id = ws_config.get_leader()
    leader_cfg = ws_config.get_agent_config(leader_id)
    if not leader_cfg:
        raise ValueError(f"Leader agent '{leader_id}' not configured in workspace.")
    provider_name = leader_cfg.get("provider")
    if not provider_name:
        raise ValueError(f"Agent '{leader_id}' has no provider configured.")
    llm_cfg = config.get_model_config(provider_name)
    if not llm_cfg:
        raise ValueError(f"LLM config '{provider_name}' not found")

    llm = create_llm(provider_name, llm_cfg)
    workspace_dir = resolve_workspace_dir(workspace_name, config)
    memory_cfg = config.get_memory_config().model_dump()
    mcp_servers = [
        {"name": name, "command": cfg.command, "args": cfg.args, "url": cfg.url, "transport": cfg.transport}
        for name, cfg in ws_config.mcp_servers.items()
    ]

    defaults = PROVIDER_DEFAULTS.get(provider_name.lower(), {})
    context_window = llm_cfg.get("context_window") or defaults.get("context_window", 200000)

    max_tool_rounds = ws_config.max_tool_rounds

    agent = AgentLoop(
        llm,
        session_id=session_id,
        workspace_dir=workspace_dir,
        agent_id=leader_id,
        memory_config=memory_cfg,
        context_window_config={
            "context_window": context_window,
        },
        mcp_servers=mcp_servers,
        max_tool_rounds=max_tool_rounds,
    )

    if loops is not None:
        loops[session_id] = agent
    logger.info(f"[dispatch] created new AgentLoop session={session_id} workspace={workspace_dir.name}")
    return agent


def create_agent_loop(
    session_id: str,
    workspace_name: str,
) -> "AgentLoop":
    """创建一个全新的 AgentLoop，不缓存。

    与 get_or_create_agent_loop 的区别：不查缓存、不存缓存。
    每次调用创建全新的 AgentLoop 实例。
    """
    from ..config.loader import load_config, resolve_workspace_dir
    from ..llm.factory import create_llm
    from ..config.defaults import PROVIDER_DEFAULTS

    config = load_config()
    ws_config = config.get_workspace(workspace_name)
    if not ws_config:
        raise ValueError(f"No workspace configured: {workspace_name}")
    leader_id = ws_config.get_leader()
    leader_cfg = ws_config.get_agent_config(leader_id)
    if not leader_cfg:
        raise ValueError(f"Leader agent '{leader_id}' not configured in workspace.")
    provider_name = leader_cfg.get("provider")
    if not provider_name:
        raise ValueError(f"Agent '{leader_id}' has no provider configured.")
    llm_cfg = config.get_model_config(provider_name)
    if not llm_cfg:
        raise ValueError(f"LLM config '{provider_name}' not found")

    llm = create_llm(provider_name, llm_cfg)
    workspace_dir = resolve_workspace_dir(workspace_name, config)
    memory_cfg = config.get_memory_config().model_dump()
    mcp_servers = [
        {"name": name, "command": cfg.command, "args": cfg.args, "url": cfg.url, "transport": cfg.transport}
        for name, cfg in ws_config.mcp_servers.items()
    ]
    defaults = PROVIDER_DEFAULTS.get(provider_name.lower(), {})
    context_window = llm_cfg.get("context_window") or defaults.get("context_window", 200000)
    max_tool_rounds = ws_config.max_tool_rounds

    agent = AgentLoop(
        llm,
        session_id=session_id,
        workspace_dir=workspace_dir,
        agent_id=leader_id,
        memory_config=memory_cfg,
        context_window_config={"context_window": context_window},
        mcp_servers=mcp_servers,
        max_tool_rounds=max_tool_rounds,
    )
    logger.info(f"[dispatch] created new AgentLoop session={session_id} workspace={workspace_dir.name}")
    return agent
