"""aion workspace - 工作空间管理（list/add/remove/use）"""

import json
import shutil
import click

from ..config.loader import load_config, save_config
from ._common import (
    DEFAULT_CONFIG_PATH,
    WORKSPACES_DIR,
    create_workspace,
    load_existing_config,
    write_config,
)


def switch_workspace(workspace_name: str) -> bool:
    """切换当前工作空间，返回是否成功。"""
    try:
        config = load_config().model_dump()
    except FileNotFoundError:
        click.echo(f"配置文件不存在: {DEFAULT_CONFIG_PATH}")
        click.echo("请先运行: aion setup")
        return False

    workspaces = config.get("workspaces", {})
    scopes = workspaces.get("scopes", [])

    ws_exists = any(workspace_name in scope for scope in scopes)
    if not ws_exists:
        names = []
        for scope in scopes:
            names.extend(scope.keys())
        click.echo(f"工作空间不存在: {workspace_name}")
        click.echo(f"可用工作空间: {', '.join(names)}")
        return False

    workspaces["current"] = workspace_name
    save_config(config)
    return True


@click.group("workspace")
def workspace():
    """工作空间管理命令（list/add/remove/use）。"""
    pass


@workspace.command("list")
def ws_list():
    """列出所有工作空间

    \b
    示例：
        aion workspace list
    """
    existing = load_existing_config()
    if not existing:
        click.echo("配置文件不存在，请先运行 aion setup")
        return

    current = existing.get("workspaces", {}).get("current", "")
    scopes = existing.get("workspaces", {}).get("scopes", [])

    if not scopes:
        click.echo("暂无工作空间")
        return

    click.echo(f"{'工作空间名':<20} {'当前':<6}")
    click.echo("-" * 26)
    for scope in scopes:
        for ws_name in scope:
            marker = " ✓" if ws_name == current else ""
            click.echo(f"{ws_name:<20}{marker}")


@workspace.command("add")
@click.argument("workspace_name")
def ws_add(workspace_name: str):
    """添加新工作空间

    \b
    示例：
        aion workspace add work
    """
    existing = load_existing_config()
    if not existing:
        click.echo("配置文件不存在，请先运行 aion setup")
        return

    existing_names = set()
    for scope in existing.get("workspaces", {}).get("scopes", []):
        existing_names.update(scope.keys())

    if workspace_name in existing_names:
        click.echo(f"工作空间 '{workspace_name}' 已存在")
        return

    from ..config.defaults import get_default_config

    default_config = json.loads(get_default_config())
    default_ws_template = default_config["workspaces"]["scopes"][0].get("default")
    if not default_ws_template:
        click.echo("错误：无法创建工作空间")
        return

    ws_scope = existing.get("workspaces", {})
    leader_id = "main"
    for scope in ws_scope.get("scopes", []):
        for _ws_name, ws_config in scope.items():
            leader_id = ws_config.get("agents", {}).get("leader", "main")
            break
        break

    new_ws = json.loads(json.dumps(default_ws_template))

    # 为新工作空间选择 LLM provider
    models = existing.get("models", {})
    provider_keys = list(models.keys())
    chosen_provider = ""
    if not provider_keys:
        click.secho("⚠ 未配置任何 LLM，请先运行 aion model add 添加 LLM 配置", fg="yellow")
    elif len(provider_keys) == 1:
        chosen_provider = provider_keys[0]
    else:
        chosen_provider = click.prompt(
            "为新工作空间选择 LLM provider",
            type=click.Choice(provider_keys),
            show_choices=True,
        )
    if chosen_provider:
        new_ws.setdefault("agents", {}).setdefault("main", {})["provider"] = chosen_provider

    existing.setdefault("workspaces", {}).setdefault("scopes", []).append({workspace_name: new_ws})
    write_config(DEFAULT_CONFIG_PATH, existing)

    create_workspace(workspace_name, agent_id=leader_id)
    click.echo(f"✓ 已添加工作空间: {workspace_name}")


@workspace.command("remove")
@click.argument("workspace_name")
@click.option("--force", "-f", is_flag=True, help="跳过确认直接删除")
def ws_remove(workspace_name: str, force: bool):
    """删除工作空间

    \b
    示例：
        aion workspace remove work
        aion workspace remove work --force
    """
    if not DEFAULT_CONFIG_PATH.exists():
        click.echo("配置文件不存在，请先运行 aion setup")
        return

    existing = load_existing_config()
    if not existing:
        click.echo("配置文件不存在或格式错误")
        return

    scopes = existing.get("workspaces", {}).get("scopes", [])
    ws_found, ws_idx = False, -1
    for i, scope in enumerate(scopes):
        if workspace_name in scope:
            ws_found, ws_idx = True, i
            break

    if not ws_found:
        click.echo(f"工作空间 '{workspace_name}' 不存在")
        return

    current_ws = existing.get("workspaces", {}).get("current", "")
    if workspace_name == current_ws:
        click.echo(f"无法删除当前工作空间 '{workspace_name}'，请先切换到其他工作空间")
        return

    if len(scopes) <= 1:
        click.echo("无法删除最后一个工作空间")
        return

    if not force:
        click.echo(f"警告：将删除工作空间 '{workspace_name}' 及其所有数据（包括 memory、sessions 等）")
        if not click.confirm("确认删除？ [y/n]", default=False, show_default=False):
            click.echo("已取消")
            return

    del scopes[ws_idx]
    existing["workspaces"]["scopes"] = scopes
    write_config(DEFAULT_CONFIG_PATH, existing)

    ws_dir = WORKSPACES_DIR / workspace_name
    if ws_dir.exists():
        shutil.rmtree(ws_dir)
        click.echo(f"✓ 已删除工作空间目录: {ws_dir}")

    click.echo(f"✓ 已删除工作空间: {workspace_name}")


@workspace.command("use")
@click.argument("workspace_name")
def ws_use(workspace_name: str):
    """切换当前工作空间

    \b
    示例：
        aion workspace use work
        aion workspace use default
    """
    if switch_workspace(workspace_name):
        click.echo(f"✓ 已切换到工作空间: {workspace_name}")
