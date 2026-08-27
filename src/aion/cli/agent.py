"""aion agent - Agent 管理命令

Usage:
    aion agent add <agent_id> --provider <provider> --workspace <ws>
    aion agent set-leader <agent_id> --workspace <ws>
    aion agent list --workspace <ws>
"""

import click

from ..core.constants import DEFAULT_WORKSPACES_DIR as WORKSPACES_DIR
from ._common import load_config, save_config, find_workspace_in_scopes


def get_all_workspace_names(scopes: list) -> list:
    """收集 scopes 中所有工作空间名称。

    Args:
        scopes: ``workspaces.scopes`` 列表。

    Returns:
        工作空间名称列表。
    """
    names = []
    for scope in scopes:
        names.extend(scope.keys())
    return names


def _create_agent_files(workspace_name: str, agent_id: str) -> None:
    """创建 Agent 完整目录结构与默认 Markdown 引导文件。

    目录结构::

        workspaces/{name}/agents/{agent_id}/
        ├── CONFIG.md
        ├── AGENT_BOOTSTRAP.md
        ├── memory/
        └── sessions/

    Args:
        workspace_name: 所属工作空间名称。
        agent_id: 新 Agent 的标识符。

    Returns:
        None
    """
    from ..agent.bootstrap.templates import (
        CONFIG_MD,
        AGENT_BOOTSTRAP_MD,
    )

    ws_dir = WORKSPACES_DIR / workspace_name
    agent_dir = ws_dir / "agents" / agent_id

    # 创建目录
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "memory").mkdir(parents=True, exist_ok=True)
    (agent_dir / "sessions").mkdir(parents=True, exist_ok=True)

    # 写入 Agent 专属 .md 文件
    (agent_dir / "CONFIG.md").write_text(CONFIG_MD(agent_id), encoding="utf-8")
    (agent_dir / "AGENT_BOOTSTRAP.md").write_text(AGENT_BOOTSTRAP_MD(agent_id, workspace_name), encoding="utf-8")


@click.group("agent")
def agent():
    """Agent 管理命令"""
    pass


@agent.command("add")
@click.argument("agent_id")
@click.option("--provider", default="deepseek", help="默认 LLM provider")
@click.option("--workspace", "ws_name", required=True, help="工作空间名称（必填）")
def add_agent(agent_id: str, provider: str, ws_name: str):
    """在工作空间下添加新 Agent

    \b
    示例：
        aion agent add agent2 --provider deepseek --workspace default
    """
    try:
        config = load_config()
    except FileNotFoundError:
        click.echo("配置文件不存在，请先运行 aion setup")
        return

    idx, ws = find_workspace_in_scopes(config, ws_name)
    scopes = config.get("workspaces", {}).get("scopes", [])

    if idx < 0:
        click.echo(f"工作空间不存在: {ws_name}")
        click.echo(f"可用工作空间: {', '.join(get_all_workspace_names(scopes))}")
        return

    agents = ws.setdefault("agents", {})

    if agent_id in agents:
        if agent_id in ("leader", "defaultLlm", "fallbackLlms"):
            click.echo(f"无法创建，保留关键字冲突: {agent_id}")
        else:
            click.echo(f"Agent 已存在: {agent_id}")
        return

    agents[agent_id] = {"provider": provider, "fallback": []}

    save_config(config)

    # 创建 Agent 文件结构
    _create_agent_files(ws_name, agent_id)

    click.echo(f"✓ 已添加 Agent '{agent_id}' 到工作空间 '{ws_name}'")
    click.echo(f"  provider: {provider}")
    click.echo(f"  目录: agents/{agent_id}/")


@agent.command("set-leader")
@click.argument("agent_id")
@click.option("--workspace", "ws_name", required=True, help="工作空间名称（必填）")
def set_leader(agent_id: str, ws_name: str):
    """设置 Agent 为 leader

    \b
    示例：
        aion agent set-leader agent2 --workspace default
    """
    try:
        config = load_config()
    except FileNotFoundError:
        click.echo("配置文件不存在，请先运行 aion setup")
        return

    idx, ws = find_workspace_in_scopes(config, ws_name)

    if idx < 0:
        click.echo(f"工作空间不存在: {ws_name}")
        return

    agents = ws.get("agents", {})

    if agent_id not in agents:
        click.echo(f"Agent 不存在: {agent_id}")
        click.echo(
            f"可用 Agent: {', '.join(k for k in agents.keys() if k not in ('leader', 'defaultLlm', 'fallbackLlms'))}"
        )
        return

    agents["leader"] = agent_id
    save_config(config)
    click.echo(f"✓ 已设置 '{agent_id}' 为工作空间 '{ws_name}' 的 leader")


@agent.command("list")
@click.option("--workspace", "ws_name", required=True, help="工作空间名称（必填）")
def list_agents(ws_name: str):
    """列出工作空间下的所有 Agent

    \b
    示例：
        aion agent list --workspace default
    """
    try:
        config = load_config()
    except FileNotFoundError:
        click.echo("配置文件不存在，请先运行 aion setup")
        return

    idx, ws = find_workspace_in_scopes(config, ws_name)

    if idx < 0:
        click.echo(f"工作空间不存在: {ws_name}")
        return

    agents = ws.get("agents", {})

    current_leader = agents.get("leader", "main")

    click.echo(f"工作空间: {ws_name}")
    click.echo(f"Leader: {current_leader}")
    click.echo()
    click.echo("Agents:")

    for agent_id, agent_cfg in agents.items():
        if agent_id in ("leader", "defaultLlm", "fallbackLlms"):
            continue
        marker = " *" if agent_id == current_leader else ""
        if isinstance(agent_cfg, dict):
            provider = agent_cfg.get("provider", "unknown")
            fallback = agent_cfg.get("fallback", [])
            click.echo(f"  {agent_id}{marker}  provider={provider}, fallback={fallback}")
        else:
            click.echo(f"  {agent_id}{marker}")


@agent.command("remove")
@click.argument("agent_id")
@click.option("--workspace", "ws_name", required=True, help="工作空间名称（必填）")
@click.option("--force", is_flag=True, help="跳过确认直接删除")
def remove_agent(agent_id: str, ws_name: str, force: bool):
    """删除工作空间下的 Agent

    \b
    示例：
        aion agent remove agent2 --workspace default
        aion agent remove agent2 --workspace default --force
    """
    try:
        config = load_config()
    except FileNotFoundError:
        click.echo("配置文件不存在，请先运行 aion setup")
        return

    idx, ws = find_workspace_in_scopes(config, ws_name)

    if idx < 0:
        click.echo(f"工作空间不存在: {ws_name}")
        return

    agents = ws.get("agents", {})

    if agent_id not in agents:
        click.echo(f"Agent 不存在: {agent_id}")
        return

    if agent_id in ("leader", "defaultLlm", "fallbackLlms"):
        click.echo(f"无法删除保留关键字: {agent_id}")
        return

    # 检查是否是当前 leader
    if agents.get("leader") == agent_id:
        click.echo(f"无法删除当前 leader: {agent_id}")
        click.echo("请先使用 set-leader 切换 leader")
        return

    # 确认删除
    if not force:
        click.confirm(f"确定要删除 Agent '{agent_id}' 吗？ [y/n]", default=False, show_default=False, abort=True)

    del agents[agent_id]
    save_config(config)
    click.echo(f"✓ 已删除 Agent '{agent_id}'")
