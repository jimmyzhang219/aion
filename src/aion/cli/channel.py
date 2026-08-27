"""Click CLI - Channel 管理命令

配置与管理飞书/Lark 等消息 Channel 的 CLI 子命令。
"""

from typing import Optional

import click


@click.group("channel")
def channel():
    """Channel 管理命令

    管理飞书等 Channel 的配置和连接。
    """
    pass


@channel.command("add")
@click.argument("channel_type", type=click.Choice(["feishu", "lark"]))
@click.option("--app-id", "app_id", default=None, help="飞书应用 App ID")
@click.option("--app-secret", "app_secret", default=None, help="飞书应用 App Secret")
@click.option("--domain", "domain_override", default=None, help="域名覆盖（feishu/lark）")
def channel_add(channel_type: str, app_id: str, app_secret: str, domain_override: str):
    """添加飞书 Channel

    需要在飞书开放平台创建自建应用并开启机器人能力。

    \b
    示例：
        aion channel add feishu
        aion channel add feishu --app-id cli_xxx --app-secret xxx
    """
    from ..channels.feishu import FeishuAccountConfig

    domain = domain_override or ("lark" if channel_type == "lark" else "feishu")

    # 交互式输入
    if not app_id:
        app_id = click.prompt("请输入 App ID", type=str)
    if not app_secret:
        app_secret = click.prompt("请输入 App Secret", type=str, hide_input=True)

    result = FeishuAccountConfig(
        appId=app_id,
        appSecret=app_secret,
        domain=domain,  # type: ignore[arg-type]
    )  # type: ignore[call-arg]

    click.echo(f"✓ Channel 配置完成 (App ID: {app_id})")

    save_account_config(result)


@channel.command("list")
def channel_list():
    """列出已配置的 Channel

    \b
    示例：
        aion channel list
    """
    from ..config.loader import load_config

    try:
        config = load_config().model_dump()
    except FileNotFoundError:
        click.echo("配置文件不存在，请先运行 aion setup")
        return

    channel_config = config.get("channels", {}) or {}

    if not channel_config:
        click.echo("没有配置任何 Channel")
        return

    click.echo("Channel 配置:")
    click.echo("-" * 40)

    for ch_type, ch_cfg in channel_config.items():
        if isinstance(ch_cfg, dict):
            enabled = ch_cfg.get("enabled", False)
            status = "✅ 已启用" if enabled else "❌ 已禁用"
            app_id = ch_cfg.get("appId", "N/A")
            connection_mode = ch_cfg.get("connectionMode", "N/A")
            click.echo(f"  {ch_type}: {status}")
            click.echo(f"    App ID: {app_id}")
            click.echo(f"    连接模式: {connection_mode}")


@channel.command("remove")
@click.argument("channel_type", type=click.Choice(["feishu", "lark"]))
@click.option("--force", is_flag=True, help="跳过确认")
def channel_remove(channel_type: str, force: bool):
    """移除 Channel

    \b
    示例：
        aion channel remove feishu
        aion channel remove feishu --force
    """
    from ..config.loader import load_config, save_config

    try:
        data = load_config().model_dump()
    except FileNotFoundError:
        click.echo("配置文件不存在，请先运行 aion setup")
        return

    if not force:
        click.confirm(f"确定要移除 {channel_type} Channel 吗? [y/n]", default=False, show_default=False, abort=True)

    channels = data.setdefault("channels", {})
    if channel_type not in channels:
        click.echo(f"{channel_type} Channel 不存在")
        return

    del channels[channel_type]
    save_config(data)

    click.echo(f"已移除 {channel_type} Channel")


def save_account_config(result, workspace: Optional[str] = None):  # noqa: ARG001 (workspace 保留参数)
    """将 Channel 账号配置写入 ``aion.json`` 顶层 ``channels``。

    Args:
        result: ``FeishuAccountConfig`` 实例，含 appId/appSecret/domain。
        workspace: 保留参数，当前未使用。

    Returns:
        None

    TODO: 当前写死 "feishu" 键。save_account_config 应支持所有 channel 类型，
          需根据 channel_type 动态写入对应键名。同时需将 channel_type 透传进来。
    """
    from ..config.loader import load_config, save_config as _save_config

    try:
        config = load_config().model_dump()
    except FileNotFoundError:
        click.echo("配置文件不存在，请先运行 aion setup")
        return

    config.setdefault("channels", {})["feishu"] = {
        "enabled": True,
        "connectionMode": "websocket",
        "appId": result.appId,
        "appSecret": result.appSecret,
        "domain": result.domain,
    }

    _save_config(config)
