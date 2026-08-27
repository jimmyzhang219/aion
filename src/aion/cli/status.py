"""aion status - 查看服务及健康状态"""

import json
import time
import urllib.request

import click

from .services import create_service_manager

from ._common import load_config, scan_skills

from ..config.loader import load_config as load_model_config
from ..config.schema import resolve_search_provider
from ..core.constants import DEFAULT_WORKSPACES_DIR


def _resolve_port(port: int | None) -> int:
    """从 config 读取 port，兜底 19527。

    Args:
        port: 用户显式传入的端口；为 None 时从 aion.json 读取。

    Returns:
        解析后的端口号。
    """
    if port is not None:
        return port
    try:
        cfg = load_model_config()
        return cfg.gateway.port
    except Exception:
        return 19527


def _query_gateway_status(port: int, timeout: float = 2.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


# _parse_skill_frontmatter 和 _get_skills_info 已移至 _common.py
# 保留此注释以标识删除；当前使用 from ._common import parse_skill_frontmatter, scan_skills


def _get_current_workspace_mcp(config: dict) -> dict:
    """Get MCP server dict for the current workspace from config."""
    workspaces = config.get("workspaces", {})
    current = workspaces.get("current", "")
    scopes = workspaces.get("scopes", [])
    for scope in scopes:
        ws = scope.get(current)
        if ws is not None:
            return ws.get("mcpServers", {})
    return {}


@click.command("status")
@click.option("--port", type=int, default=None, help="Gateway 端口（默认从 aion.json 读取）")
def status(port: int | None):
    """查看 aion 服务及健康状态

    示例：

        aion status

    Returns:
        None（状态信息通过 click.echo 输出到终端）。
    """
    port = _resolve_port(port)

    # 进程状态（多源查找）
    pids = create_service_manager().find_pids()
    runtime = None
    if pids:
        pid_str = ", ".join(str(p) for p in sorted(pids))
        process_status = click.style("RUNNING", fg="green", bold=True)
        click.echo(f"Gateway 进程:  {process_status} (PID: {pid_str})")

        # 运行时状态 — 获取启动时间
        runtime = _query_gateway_status(port)
        if runtime and runtime.get("start_time"):
            start_ts = runtime["start_time"]
            start_local = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start_ts))
            elapsed = time.time() - start_ts
            hours, remainder = divmod(int(elapsed), 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours:
                uptime_str = f"{hours}小时{minutes}分{seconds}秒"
            else:
                uptime_str = f"{minutes}分{seconds}秒"
            click.echo(f"  启动时间:     {start_local}  (已运行 {uptime_str})")
    else:
        process_status = click.style("NOT RUNNING", fg="red", bold=True)
        click.echo(f"Gateway 进程:  {process_status}")

    # 运行时状态（从 Gateway HTTP 端点获取）
    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}", timeout=2.0)
        click.echo(f"  HTTP 服务:    {click.style('healthy', fg='green')} 端口 {port}")
        resp.close()
    except Exception:
        if pids:
            click.echo(f"  HTTP 服务:    {click.style('unreachable', fg='red')} 端口 {port}")
        else:
            click.echo(f"  HTTP 服务:    {click.style('not running', fg='yellow')}")

    # Channel 运行时连接状态（复用前面的 runtime 结果）
    channels_runtime = {}
    session_queues = {}
    if runtime:
        channels_runtime = runtime.get("channels", {})
        session_queues = runtime.get("session_queues", {})
        for ch_name, ch_info in channels_runtime.items():
            running = ch_info.get("running", False)
            connected = ch_info.get("connected", False)
            status_value = ch_info.get("status")
            if status_value == "failed":
                ch_label = click.style("FAILED", fg="red", bold=True)
                error_msg = ch_info.get("error", "")
                click.echo(f"  {ch_name}:     {ch_label}  ({error_msg})")
            elif connected:
                ch_label = click.style("CONNECTED", fg="green", bold=True)
            elif running:
                ch_label = click.style("connecting...", fg="yellow")
            else:
                ch_label = click.style("stopped", fg="red")
            click.echo(f"  {ch_name}:     {ch_label} (WebSocket)")

        # 会话队列状态
        if session_queues:
            click.echo()
            click.echo("--- 会话队列 ---")
            for sid, info in session_queues.items():
                status_label = (
                    click.style("处理中", fg="green") if info.get("processing") else click.style("等待中", fg="yellow")
                )
                qs = info.get("queue_size", 0)
                queue_label = f" (队列: {qs})" if qs > 0 else ""
                click.echo(f"  {sid}  {status_label}{queue_label}")
        else:
            click.echo(f"  会话队列:    {click.style('无活跃会话', fg='white')}")

    # 配置状态
    try:
        config = load_config()
    except FileNotFoundError:
        click.echo()
        click.echo(f"配置:          {click.style('MISSING', fg='red')}  (请运行 aion setup)")
        return

    click.echo()
    click.echo("--- 配置状态 ---")

    # Models
    models = config.get("models", {})
    if not models:
        click.echo(f"LLM 模型:      {click.style('未配置', fg='yellow')}")
    else:
        for name, cfg in sorted(models.items()):
            key = cfg.get("apiKey", "")
            key_display = (key[:8] + "***..." + key[-4:]) if len(key) > 12 else ("***" if key else "empty")
            model_name = cfg.get("model", "?")
            base_url = cfg.get("baseUrl", "N/A")
            click.echo(
                f"LLM 模型:      {click.style(name, fg='green')}  model={model_name}  endpoint={base_url}  key={key_display}"
            )

    # Channels
    channels = config.get("channels", {})
    if not channels:
        click.echo(f"Channel:       {click.style('未配置', fg='yellow')}")
    else:
        for ch_name, ch_cfg in channels.items():
            enabled = ch_cfg.get("enabled", False)
            app_id = ch_cfg.get("appId", "N/A")
            if enabled:
                ch_config_status = click.style("已启用", fg="green")
            else:
                ch_config_status = click.style("已禁用", fg="yellow")
            # 从运行时数据获取实际状态
            runtime_info = channels_runtime.get(ch_name, {})
            if not enabled:
                ch_runtime_status = click.style("—", fg="white")
            elif runtime_info.get("status") == "failed":
                ch_runtime_status = click.style(f"启动失败 ✗: {runtime_info.get('error', '')}", fg="red")
            elif runtime_info.get("connected"):
                ch_runtime_status = click.style("运行中 ✓", fg="green")
            elif runtime_info.get("running"):
                ch_runtime_status = click.style("启动中…", fg="yellow")
            elif channels_runtime:
                ch_runtime_status = click.style("已停止", fg="yellow")
            else:
                ch_runtime_status = click.style("—", fg="white")
            click.echo(f"Channel:       {ch_name}  {ch_config_status}  {ch_runtime_status}  appId={app_id}")

    # Web 搜索
    resolved = resolve_search_provider(config.get("search", {}))
    if resolved:
        provider_id, provider_cfg = resolved
        search_key = provider_cfg.get("apiKey", "")
        key_display = (
            (search_key[:8] + "***..." + search_key[-4:]) if len(search_key) > 12 else "***"
        )
        click.echo(
            f"Web 搜索:      {click.style('已启用', fg='green')}  provider={provider_id}  key={key_display}"
        )
    else:
        click.echo(f"Web 搜索:      {click.style('未配置', fg='yellow')}")

    # Workspaces
    workspaces = config.get("workspaces", {})
    current = workspaces.get("current", "N/A")
    scopes = workspaces.get("scopes", [])
    ws_names = []
    for scope in scopes:
        ws_names.extend(scope.keys())
    click.echo("工作空间:")
    for name in ws_names:
        marker = " ← 当前" if name == current else ""
        click.echo(f"  {name}{marker}")

    # Embedding 配置（全局顶层 memory）
    memory_cfg = config.get("memory", {})
    embedding_cfg = memory_cfg.get("embedding", {}) if memory_cfg else {}
    if embedding_cfg and embedding_cfg.get("provider"):
        provider = embedding_cfg["provider"]
        provider_cfg = embedding_cfg.get(provider, {})
        model = provider_cfg.get("model", "N/A")
        key = provider_cfg.get("api_key", "")
        key_display = (key[:8] + "***..." + key[-4:]) if len(key) > 12 else ("***" if key else "未配置")
        endpoint = provider_cfg.get("base_url", "N/A")
        click.echo(
            f"Embeddings:    {click.style(provider, fg='green')}  model={model}  endpoint={endpoint}  key={key_display}"
        )
    else:
        click.echo(f"Embeddings:    {click.style('未配置', fg='yellow')}  (降级为关键词搜索)")

    # Skills（当前工作空间已安装的技能）
    click.echo("Skills:")
    if current != "N/A" and current:
        ws_dir = DEFAULT_WORKSPACES_DIR / current
        skills = scan_skills(ws_dir)
        if skills:
            for s in skills:
                desc = f" — {s['description']}" if s["description"] else ""
                click.echo(f"  {click.style(s['name'], fg='cyan')}{desc}")
        else:
            click.echo(f"  {click.style('无', fg='yellow')}  (workspace/skills/ 下无 SKILL.md)")

    # MCP 服务器（当前工作空间已注册的 MCP）
    click.echo("MCP:")
    mcp_servers = _get_current_workspace_mcp(config) if current != "N/A" and current else {}
    if mcp_servers:
        for name, cfg in mcp_servers.items():
            addr = cfg.get("url") or f"{cfg.get('command', '')} {' '.join(cfg.get('args', []))}".strip()
            click.echo(f"  {click.style(name, fg='cyan')}  {addr}")
    else:
        click.echo(f"  {click.style('无', fg='yellow')}  (mcpServers 未配置)")

    # Log level
    log_level = config.get("log_level", "info")
    click.echo(f"日志级别:      {log_level}")
