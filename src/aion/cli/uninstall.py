"""aion uninstall - 卸载 aion

清理 Gateway 进程、系统服务、符号链接、二进制及 ``~/.aion/`` 全部用户数据。
"""

import asyncio
import shutil
import subprocess
import time
from pathlib import Path

import click

from ..core.constants import AION_HOME as AION_CONFIG_DIR

PID_FILE = AION_CONFIG_DIR / "gateway.pid"  # Gateway PID 文件
SYMLINK_PATHS = [Path("/usr/local/bin/aion"), Path.home() / ".local" / "bin" / "aion"]  # 可能的符号链接路径
BIN_DIRS = [Path("/usr/local/lib/aion"), Path.home() / ".local" / "lib" / "aion"]  # 可能的二进制安装目录


def _stop_gateway():
    """通过 service manager 停止 Gateway 进程和服务。"""
    from .services import create_service_manager

    mgr = create_service_manager()
    pids = mgr.find_pids()
    if pids:
        click.echo("Stopping Gateway...")
        asyncio.run(mgr.stop(pids))

        # 额外确认
        for _ in range(30):
            if not any(mgr.find_pids()):
                click.echo("  Gateway stopped")
                break
            time.sleep(0.1)
        else:
            click.echo("  Gateway force-killed")
        PID_FILE.unlink(missing_ok=True)


def _remove_system_service():
    """移除系统服务定义文件（launchd plist / systemd unit）。"""
    from .services import create_service_manager

    mgr = create_service_manager()
    # stop() 已包含服务定义清理，这里只删除服务定义文件本身
    if hasattr(mgr, "PLIST_PATH") and mgr.PLIST_PATH.exists():
        click.echo("Removing LaunchAgent...")
        mgr.PLIST_PATH.unlink()
        click.echo("  LaunchAgent removed")
    if hasattr(mgr, "SERVICE_PATH") and mgr.SERVICE_PATH.exists():
        mgr.SERVICE_PATH.unlink()


def _remove_symlinks():
    """删除所有可能的 aion 符号链接。

    Returns:
        None
    """
    for symlink in SYMLINK_PATHS:
        if not symlink.is_symlink():
            continue
        click.echo(f"Removing symlink {symlink}...")
        try:
            symlink.unlink()
            click.echo("  Symlink removed")
        except PermissionError:
            try:
                subprocess.run(
                    ["sudo", "-n", "rm", str(symlink)],
                    check=True,
                    capture_output=True,
                )
                click.echo("  Symlink removed (via sudo)")
            except (subprocess.CalledProcessError, FileNotFoundError):
                click.echo(f"  Warning: Cannot remove {symlink} (requires sudo)")
                click.echo(f"  Run manually: sudo rm {symlink}")


def _remove_bin_dirs():
    """删除所有可能的二进制安装目录。

    Returns:
        None
    """
    for d in BIN_DIRS:
        if not d.exists():
            continue
        click.echo(f"Removing binary dir {d}...")
        try:
            shutil.rmtree(d)
            click.echo("  Removed")
        except PermissionError:
            try:
                subprocess.run(
                    ["sudo", "-n", "rm", "-rf", str(d)],
                    check=True,
                    capture_output=True,
                )
                click.echo("  Removed (via sudo)")
            except (subprocess.CalledProcessError, FileNotFoundError):
                click.echo(f"  Warning: Cannot remove {d} (requires sudo)")
                click.echo(f"  Run manually: sudo rm -rf {d}")


@click.command("uninstall")
@click.option("--yes", "-y", is_flag=True, help="跳过确认")
def uninstall(yes: bool):
    """卸载 aion（清理所有安装产物）

    \b
    清理内容：
    - 停止 Gateway 进程
    - 移除 LaunchAgent / systemd 服务
    - 删除符号链接 / 二进制 / ~/.aion/

    \b
    示例：
        aion uninstall
        aion uninstall -y
    """
    if not AION_CONFIG_DIR.exists() and not any(d.exists() for d in BIN_DIRS):
        click.echo("未检测到 aion 安装")
        return

    if not yes:
        click.echo("即将清理以下内容：")
        click.echo("  1. 停止 Gateway（若运行中）")
        click.echo("  2. 移除系统服务（LaunchAgent / systemd）")
        click.echo("  3. 删除符号链接")
        click.echo("  4. 删除二进制安装目录")
        click.echo(f"  5. 删除 {AION_CONFIG_DIR}/ （配置、全部 workspace 数据）")
        click.echo()
        if not click.confirm("确认卸载？ [y/n]", default=False, show_default=False):
            click.echo("已取消")
            return

    _stop_gateway()
    _remove_system_service()
    _remove_symlinks()
    _remove_bin_dirs()

    if AION_CONFIG_DIR.exists():
        click.echo(f"Removing {AION_CONFIG_DIR}...")
        shutil.rmtree(AION_CONFIG_DIR)

    click.echo()
    click.echo("aion 已卸载")
