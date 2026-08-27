"""MCP CLI 命令

MCP 服务器在工作空间下配置。
"""

import click

from ._common import load_config, save_config, get_current_workspace, find_workspace_in_scopes


@click.group()
def mcp():
    """MCP 服务器管理子命令组（add/list/remove）。"""
    pass


@mcp.command("add")
@click.argument("name")
@click.argument("command_args", nargs=-1, required=False)
@click.option("--url", help="HTTP 模式 MCP 服务器 URL")
@click.option("--workspace", "ws_name", default=None, help="工作空间名称（默认当前）")
def add(name: str, command_args: tuple, url: str | None, ws_name: str | None):
    """添加 MCP 服务器到工作空间

    \b
    示例：
        aion mcp add time-mcp -- npx @mcpcentral/mcp-time
        aion mcp add filesystem -- npx -y @modelcontextprotocol/server-filesystem /tmp
        aion mcp add myserver --url http://localhost:8080
    """
    try:
        config = load_config()
    except FileNotFoundError:
        click.echo("配置文件不存在，请先运行 aion setup")
        return

    if ws_name is None:
        ws_name = get_current_workspace(config)

    idx, ws = find_workspace_in_scopes(config, ws_name)

    if idx < 0:
        click.echo(f"工作空间不存在: {ws_name}")
        return

    servers = ws.setdefault("mcpServers", {})

    # 检查是否已存在
    if name in servers:
        click.echo(f"MCP 服务器已存在: {name}")
        return

    if url:
        # HTTP 模式
        servers[name] = {"url": url, "transport": "streamable-http"}
    elif command_args:
        # stdio 模式
        server: dict = {"command": command_args[0]}
        if len(command_args) > 1:
            server["args"] = list(command_args[1:])
        servers[name] = server
    else:
        click.echo("错误: 请提供 MCP 服务器命令（用 -- 分隔）或 --url", err=True)
        return
    save_config(config)
    click.echo(f"✓ 已添加 MCP 服务器: {name} -> {ws_name}")


@mcp.command("list")
@click.option("--workspace", "ws_name", help="工作空间名称（默认当前）")
def list_mcp(ws_name: str):
    """列出工作空间的 MCP 服务器

    \b
    示例：
        aion mcp list
        aion mcp list --workspace default
    """
    try:
        config = load_config()
    except FileNotFoundError:
        click.echo("配置文件不存在")
        return

    if ws_name is None:
        ws_name = get_current_workspace(config)

    idx, ws = find_workspace_in_scopes(config, ws_name)

    if idx < 0:
        click.echo(f"工作空间不存在: {ws_name}")
        return

    servers = ws.get("mcpServers", {})

    click.echo(f"工作空间: {ws_name}")
    if not servers:
        click.echo("  暂无 MCP 服务器")
        return

    for name, cfg in servers.items():
        addr = cfg.get("url") or f"{cfg.get('command', '')} {' '.join(cfg.get('args', []))}".strip()
        click.echo(f"  - {name}  ({addr})")


@mcp.command("remove")
@click.argument("name")
@click.option("--workspace", "ws_name", default=None, help="工作空间名称（默认当前）")
@click.option("--force", is_flag=True, help="跳过确认直接删除")
def remove(name: str, ws_name: str, force: bool):
    """移除 MCP 服务器

    \b
    示例：
        aion mcp remove filesystem --workspace default
        aion mcp remove filesystem --force
    """
    try:
        config = load_config()
    except FileNotFoundError:
        click.echo("配置文件不存在")
        return

    if ws_name is None:
        ws_name = get_current_workspace(config)

    idx, ws = find_workspace_in_scopes(config, ws_name)

    if idx < 0:
        click.echo(f"工作空间不存在: {ws_name}")
        return

    servers = ws.get("mcpServers", {})

    if name not in servers:
        click.echo(f"未找到 MCP 服务器: {name}")
        return

    if not force:
        click.confirm(f"确定要删除 MCP 服务器 '{name}' 吗？ [y/n]", default=False, show_default=False, abort=True)

    ws["mcpServers"].pop(name, None)
    save_config(config)
    click.echo(f"✓ 已移除 MCP 服务器: {name}")
