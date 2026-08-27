"""aion CLI 入口"""

import asyncio
import sys

import click

from ..log import configure_logging
from .status import _resolve_port


@click.group(invoke_without_command=True, context_settings=dict(help_option_names=["-h", "--help"]))
@click.option("--version", is_flag=True, help="显示版本信息并退出")
@click.pass_context
def main(ctx, version):
    """个人多 Agent AI 助手系统 CLI 根命令组。

    飞书消息斜杠命令（在聊天中直接输入）：
        /new            开始新会话（清空上下文）
        /switch <名称>  切换工作空间
        /workspaces     列出所有工作空间
        /status         查看当前状态
        /help           显示斜杠命令帮助
    """
    if version:
        from aion import __version__

        click.echo(f"aion {__version__}")
        return
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@main.command("version")
def show_version():
    """显示构建版本信息"""
    try:
        from aion._build_info import BUILD_GIT_HASH, BUILD_TIME

        click.echo(f"Build: {BUILD_GIT_HASH} @ {BUILD_TIME}")
    except ImportError:
        click.echo("Build: development mode (no build info)")


@main.command()
def run():
    """前台启动 Gateway 服务。

    在当前终端阻塞运行 HTTP Server 与飞书 Channel，按 Ctrl+C 退出。

    示例：
        aion run
    """
    from ..core.path_setup import ensure_system_path

    ensure_system_path()

    from ..log import configure_logging

    configure_logging(verbose=True)
    from ..gateway.server import GatewayServer
    from ..channels.http import HttpChannel

    gateway = GatewayServer(http_channel=HttpChannel())
    gateway.run()


@main.command()
def start():
    """后台启动 Gateway 服务（macOS LaunchAgent / Linux systemd / PID 文件）。"""
    from .services import create_service_manager
    from ..core.path_setup import ensure_system_path

    ensure_system_path()

    mgr = create_service_manager()
    ok = asyncio.run(mgr.start())
    if ok:
        click.echo("✓ Gateway 已启动")
    else:
        click.echo("启动失败，请检查: aion run")


@main.command()
def stop():
    """停止后台运行的 Gateway 服务。"""
    from .services import create_service_manager
    from aion.cli.services.launchd import LaunchdManager

    mgr = create_service_manager()
    pids = mgr.find_pids()

    if not pids:
        # 可能有残留的 LaunchAgent 定义但进程已死
        if isinstance(mgr, LaunchdManager):
            labels = mgr._find_launchagent_labels()
            if labels:
                for lb in labels:
                    mgr._bootout_launchagent(lb)
                if mgr.PLIST_PATH.exists():
                    mgr.PLIST_PATH.unlink()
                click.echo("✓ 已清理 LaunchAgent 服务定义")
            else:
                click.echo("Gateway 未运行")
        else:
            click.echo("Gateway 未运行")
        return

    for pid in sorted(pids):
        click.echo(f"Stopping Gateway (PID: {pid})...")

    ok = asyncio.run(mgr.stop(pids))
    click.echo("✓ Gateway 已停止" if ok else "⚠ Gateway 停止可能未完全成功")


@main.command()
@click.argument("message", required=False, default=None)
@click.option("--host", default="127.0.0.1", help="Gateway 地址")
@click.option("--port", type=int, default=None, help="Gateway 端口（默认从 aion.json 读取）")
@click.option("--session", "session_id", default="default", help="Session ID（默认 default，可复用）")
@click.option("--timeout", "timeout_sec", type=int, default=None, help="超时秒数（默认无超时，agent-browser 建议 300）")
@click.option("--list-sessions", is_flag=True, help="列出最近的历史 Session")
def chat(message: str, host: str, port: int | None, session_id: str, timeout_sec: int | None, list_sessions: bool):
    """向 Gateway 发送聊天消息或列出历史 Session。

    \b
    示例：
        aion chat "你好"
        aion chat --timeout 300 "用agent-browser打开百度"
        aion chat --list-sessions
        echo "你好" | aion chat
    """
    configure_logging()
    import json

    port = _resolve_port(port)

    # 管道模式：无命令行参数时从 stdin 读取消息
    if message is None and not sys.stdin.isatty():
        message = sys.stdin.read().strip()

    if list_sessions:
        import urllib.request

        url = f"http://{host}:{port}"
        payload = json.dumps({"action": "list_sessions"}).encode()
        req = urllib.request.Request(f"{url}/sessions", data=payload, method="GET")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                sessions = data.get("sessions", [])
                if not sessions:
                    click.echo("暂无历史 Session")
                    return
                click.echo(f"{'Session ID':<30} {'消息数':<8} {'最后更新':<25} {'首条消息'}")
                click.echo("-" * 80)
                for s in sessions:
                    ts = s.get("last_updated", "N/A")[:19] if s.get("last_updated") else "N/A"
                    first = s.get("first_message", "")[:30]
                    click.echo(f"{s['session_id']:<30} {s['message_count']:<8} {ts:<25} {first}")
        except Exception as e:
            click.echo(f"查询失败: {e}")
        return

    if not message:
        click.echo("请提供消息内容（直接传参或管道输入）")
        return

    # 通过 WebSocket 发送消息并等待响应推送
    try:
        import asyncio
        import websockets

        ws_port = port + 1
        timeout = timeout_sec if timeout_sec is not None else 300

        async def _ws_chat():
            async with websockets.connect(f"ws://{host}:{ws_port}") as ws:
                await ws.send(json.dumps({"message": message, "session_id": session_id}))
                response = await asyncio.wait_for(ws.recv(), timeout=timeout)
                data = json.loads(response)
                if data.get("type") == "error":
                    click.echo(f"错误: {data.get('content', '')}")
                elif data.get("type") == "message":
                    click.echo(data.get("content", ""))
                else:
                    click.echo(response)

        asyncio.run(_ws_chat())
    except ImportError:
        click.echo("需要 websockets 库: pip install websockets")
    except ConnectionRefusedError:
        click.echo("连接 Gateway 失败 — 请先运行: aion run")
    except asyncio.TimeoutError:
        click.echo("等待响应超时")


from .agent import agent
from .channel import channel
from .logs import logs
from .mcp import mcp
from .models import model
from .restart import restart
from .setup import setup
from .skill import skill
from .status import status
from .workspace import workspace


# 注册子命令（来自独立模块的 @click.command）
main.add_command(status)
main.add_command(agent)
main.add_command(channel)
main.add_command(logs)
main.add_command(mcp)
main.add_command(model)
main.add_command(restart)
main.add_command(setup)
main.add_command(skill)
main.add_command(workspace)


if __name__ == "__main__":
    main()
