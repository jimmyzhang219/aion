"""aion restart - 重启 Gateway 服务"""

import asyncio
import time

import click

from .services import create_service_manager


def _start_gateway():
    """启动 Gateway（通过 service manager）。"""
    mgr = create_service_manager()
    asyncio.run(mgr.start())
    time.sleep(2)


@click.command("restart")
def restart():
    """重启 Gateway 服务"""
    mgr = create_service_manager()
    pids = mgr.find_pids()
    # 检查是否有服务定义文件（plist / systemd unit），而非仅检查进程
    has_service_def = (
        bool(pids)
        or (hasattr(mgr, "PLIST_PATH") and mgr.PLIST_PATH.exists())
        or (hasattr(mgr, "SERVICE_PATH") and mgr.SERVICE_PATH.exists())
    )

    if not pids and not has_service_def:
        click.echo("Gateway 未运行且无服务配置")
        click.echo("请先运行: aion start")
        return

    if pids:
        for pid in sorted(pids):
            click.echo(f"Stopping Gateway (PID: {pid})...")
        asyncio.run(mgr.stop(pids))
        click.echo("  Gateway stopped")

    _start_gateway()

    pids = mgr.find_pids()
    if pids:
        click.echo(f"✓ Gateway 已重启 (PID: {', '.join(str(p) for p in sorted(pids))})")
    else:
        click.echo("✗ Gateway 启动失败，请检查: aion run")
