"""aion setup - 引导式初始化/升级配置"""

import json
from pathlib import Path

import click

from ._common import (
    DEFAULT_CONFIG_PATH,
    create_workspace,
    ensure_aion_base,
    load_existing_config,
    merge_configs,
    write_config,
)


def _interactive_llm_setup(config: dict) -> None:
    """引导式配置 LLM 模型，写入 config 的 models 段并更新 workspace provider。"""
    from ..core.constants import DEFAULT_MODEL_NAME
    from ..cli.models import _resolve_provider

    provider = click.prompt(
        "  Provider 名称（如 deepseek / openai / alicloud / minimax / moonshot / glm）", default="deepseek"
    ).strip()
    model_name = click.prompt("  Model 名称", default=DEFAULT_MODEL_NAME).strip()
    model_api_key = click.prompt("  API Key", hide_input=True).strip()
    if not model_api_key:
        click.echo("  ✗ API Key 为空，跳过配置")
        return

    resolved_url, _, resolved_max_tokens = _resolve_provider(provider, model_name)
    entry: dict = {
        "model": model_name,
        "apiKey": model_api_key,
        "max_tokens": resolved_max_tokens,
    }
    if resolved_url:
        entry["baseUrl"] = resolved_url
    else:
        custom_url = click.prompt("  Base URL").strip()
        if custom_url:
            entry["baseUrl"] = custom_url

    # 从 PROVIDER_DEFAULTS 注入 request_timeout 和 reasoning_effort
    from ..config.defaults import PROVIDER_DEFAULTS

    pd_defaults = PROVIDER_DEFAULTS.get(provider.lower(), {})
    if "request_timeout" in pd_defaults:
        entry["request_timeout"] = pd_defaults["request_timeout"]

    config.setdefault("models", {})[provider] = entry
    click.echo(f"  ✓ 已配置模型: {provider}")

    ws_scopes = config.setdefault("workspaces", {}).setdefault("scopes", [])
    for scope in ws_scopes:
        for _ws_name, ws_cfg in scope.items():
            agents = ws_cfg.setdefault("agents", {})
            if not agents.get("main", {}).get("provider"):
                agents.setdefault("main", {})["provider"] = provider
                agents.setdefault("main", {}).setdefault("fallback", [])
            break
        break


def _interactive_embedding_setup(config: dict) -> None:
    """引导配置 Embedding 模型。"""
    if not click.confirm("是否配置 Embedding 模型？ [y/n]", default=True, show_default=False):
        return

    click.echo("  Embedding Provider:")
    click.echo("    1) Ollama 本地模型（BGE 等，默认）")
    click.echo("    2) OpenAI / 兼容 API（智谱等国内厂商）")
    choice = click.prompt("  请选择", default="1")

    provider_map = {"1": "ollama", "2": "openai"}
    provider = provider_map.get(choice, "ollama")

    # 确保顶层 memory 配置段存在，写入完整默认值
    memory = config.setdefault("memory", {})
    memory.setdefault("enabled", True)
    memory.setdefault("startup_context_enabled", True)
    memory.setdefault("daily_memory_days", 2)
    memory.setdefault("max_file_bytes", 16384)
    memory.setdefault("max_file_chars", 1200)
    memory.setdefault("max_total_chars", 2800)
    memory.setdefault("bootstrap_max_chars", 20000)
    memory.setdefault("bootstrap_total_max_chars", 150000)
    memory.setdefault("memory_search", True)
    memory.setdefault("memory_get", True)
    memory.setdefault("context_injection", "always")
    embedding = memory.setdefault("embedding", {})

    embedding["provider"] = provider

    if provider == "openai":
        if provider not in embedding:
            embedding[provider] = {}
        embedding[provider]["model"] = "text-embedding-3-small"
        key = click.prompt(
            "  API Key（留空使用 OPENAI_API_KEY 环境变量）",
            default="",
            hide_input=True,
        ).strip()
        if key:
            embedding[provider]["api_key"] = key
        url = click.prompt(
            "  Base URL（回车默认 OpenAI，智谱填 https://open.bigmodel.cn/api/paas/v4）",
            default="",
        ).strip()
        if url:
            embedding[provider]["base_url"] = url

    elif provider == "ollama":
        if provider not in embedding:
            embedding[provider] = {}
        url = click.prompt("  Ollama 地址", default="http://localhost:11434").strip()
        model = click.prompt("  模型", default="bge-m3").strip()
        embedding[provider]["base_url"] = url
        embedding[provider]["model"] = model

    click.echo(f"  ✓ 已配置 Embedding: {provider}")


@click.command("setup")
@click.option("--force", "-f", is_flag=True, help="强制重新生成配置")
@click.option("--minimal", "-m", is_flag=True, help="生成最小化配置")
@click.option("--api-key", "-k", "api_key", help="API Key")
@click.option("--config", "-c", "config_path", type=click.Path(), help="配置文件路径")
def setup(
    force: bool,
    minimal: bool,
    api_key: str | None,
    config_path: str | None,
):
    """引导式初始化/升级 aion 配置

    首次运行将引导配置默认 LLM 模型和飞书 Channel；
    再次运行将合并缺失字段并检查 API Key。

    \b
    示例：
        aion setup
        aion setup --force
        aion setup --api-key sk-xxx
        aion setup -c /path/to/aion.json
    """
    from ..config.defaults import get_default_config, generate_minimal_config

    cfg_path = Path(config_path or DEFAULT_CONFIG_PATH)

    ensure_aion_base()

    existing = load_existing_config(cfg_path)

    if minimal:
        if not api_key:
            click.echo("错误: minimal 模式需要提供 --api-key")
            return
        new_config = json.loads(generate_minimal_config(api_key=api_key))
    else:
        new_config = json.loads(get_default_config())

    if existing:
        if force:
            click.echo(f"配置文件已存在: {cfg_path}")
            click.echo("强制模式：用默认配置覆盖")
            config = new_config
            ws_scope = new_config.get("workspaces", {})
            for scope in ws_scope.get("scopes", []):
                for ws_name, ws_config in scope.items():
                    leader_id = ws_config.get("agents", {}).get("leader", "main")
                    create_workspace(ws_name, agent_id=leader_id)
                    break
            click.echo("✓ 已重建工作空间目录和 .md 文件")

            click.echo()
            click.echo("--- 引导配置 ---")

            gateway_port = click.prompt("  Gateway HTTP 端口", default=19527, type=int)
            config["gateway"] = {"port": gateway_port}

            if click.confirm("是否配置默认 LLM 模型？ [y/n]", default=True, show_default=False):
                _interactive_llm_setup(config)

            _interactive_embedding_setup(config)

            if click.confirm("是否配置飞书 Channel？ [y/n]", default=True, show_default=False):
                feishu_app_id = click.prompt("  App ID").strip()
                feishu_app_secret = click.prompt("  App Secret", hide_input=True).strip()
                if feishu_app_id and feishu_app_secret:
                    config.setdefault("channels", {})["feishu"] = {
                        "enabled": True,
                        "connectionMode": "websocket",
                        "appId": feishu_app_id,
                        "appSecret": feishu_app_secret,
                        "domain": "feishu",
                    }
                    click.echo("  ✓ 已配置飞书 Channel")

            if click.confirm("是否配置 Web 搜索？ [y/n]", default=True, show_default=False):
                choice = click.prompt("  搜索 provider (bocha/baidu)", default="bocha").strip().lower()
                web_search_key = click.prompt("  API Key", hide_input=True).strip()
                if web_search_key and choice in ("bocha", "baidu"):
                    prov_cfg = {"apiKey": web_search_key}
                    config.setdefault("search", {})["webSearch"] = {
                        "provider": choice,
                        "providers": {choice: prov_cfg},
                    }
                    click.echo(f"  ✓ 已配置 Web 搜索 ({choice})")
                else:
                    click.echo("  ✗ API Key 为空或 provider 非法，跳过（web_search 不会启用）")

            click.echo("--- 引导配置完成 ---")
        else:
            click.echo(f"配置文件已存在: {cfg_path}")
            click.echo("合并缺失的字段...")
            config = merge_configs(existing, new_config)

            models_config = config.get("models", {})
            if not models_config:
                if click.confirm("尚未配置 LLM 模型，是否现在配置？ [y/n]", default=True, show_default=False):
                    _interactive_llm_setup(config)
            else:
                placeholder_found = False
                for model_name, model_cfg in list(models_config.items()):
                    if isinstance(model_cfg, dict) and model_cfg.get("apiKey", "") in ("YOUR_API_KEY_HERE", ""):
                        placeholder_found = True
                        if api_key:
                            model_cfg["apiKey"] = api_key
                            click.echo(f"✓ 已更新 {model_name} API Key")
                        elif click.confirm(
                            f"{model_name} API Key 未配置，是否现在设置？ [y/n]", default=True, show_default=False
                        ):
                            new_key = click.prompt("  API Key", hide_input=True).strip()
                            if new_key:
                                model_cfg["apiKey"] = new_key
                                click.echo(f"  ✓ 已更新 {model_name} API Key")
                        break
                if not placeholder_found:
                    click.echo("✓ 模型配置已存在")
    else:
        config = new_config

        create_workspace("default", agent_id="main")

        click.echo()
        click.echo("--- 引导配置 ---")

        gateway_port = click.prompt("  Gateway HTTP 端口", default=19527, type=int)
        config["gateway"] = {"port": gateway_port}

        if click.confirm("是否配置默认 LLM 模型？ [y/n]", default=True, show_default=False):
            _interactive_llm_setup(config)

        _interactive_embedding_setup(config)
        if click.confirm("是否配置飞书 Channel？ [y/n]", default=True, show_default=False):
            feishu_app_id = click.prompt("  App ID").strip()
            feishu_app_secret = click.prompt("  App Secret", hide_input=True).strip()
            if feishu_app_id and feishu_app_secret:
                config.setdefault("channels", {})["feishu"] = {
                    "enabled": True,
                    "connectionMode": "websocket",
                    "appId": feishu_app_id,
                    "appSecret": feishu_app_secret,
                    "domain": "feishu",
                }
                click.echo("  ✓ 已配置飞书 Channel")

        if click.confirm("是否配置 Web 搜索？ [y/n]", default=True, show_default=False):
            choice = click.prompt("  搜索 provider (bocha/baidu)", default="bocha").strip().lower()
            web_search_key = click.prompt("  API Key", hide_input=True).strip()
            if web_search_key and choice in ("bocha", "baidu"):
                prov_cfg = {"apiKey": web_search_key}
                config.setdefault("search", {})["webSearch"] = {
                    "provider": choice,
                    "providers": {choice: prov_cfg},
                }
                click.echo(f"  ✓ 已配置 Web 搜索 ({choice})")
            else:
                click.echo("  ✗ API Key 为空或 provider 非法，跳过（web_search 不会启用）")

        click.echo("--- 引导配置完成 ---")

    write_config(cfg_path, config)
    click.echo(f"✓ 配置文件已保存: {cfg_path}")

    click.echo()
    click.echo("配置结构：")
    click.echo("  models           系统级模型配置（所有工作空间共享）")
    click.echo("  search           Web 搜索配置")
    click.echo("    webSearch      provider + providers.{bocha|baidu}.apiKey")
    click.echo("  gateway          Gateway 服务配置")
    click.echo("    port           HTTP 监听端口")
    click.echo("  memory           全局记忆/Embedding 配置")
    click.echo("    embedding      Embedding Provider（ollama / openai）")
    click.echo("  workspaces       工作空间配置")
    click.echo("    scopes[]       工作空间数组")
    click.echo("    current        当前工作空间名字（字符串）")
    click.echo("    [].agents      Agent 配置")
    click.echo("    [].compaction  压缩配置")
    click.echo("    [].pruning     裁剪配置")
    click.echo("    [].mcpServers MCP 服务器（dict 格式）")
    click.echo("  channels         配置消息渠道")
    click.echo("    [name]        Channel 名称（如 feishu）")
    click.echo("    [].appId      飞书 App ID")
    click.echo("    [].appSecret  飞书 App Secret")
    click.echo("  log_level        全局日志级别（默认 info，可选 debug/warn/error）")
    click.echo()
    click.echo(f"查看/编辑配置: nano {cfg_path}")

    # --force 模式：配置完成后自动重启 Gateway
    if force and existing:
        click.echo()
        click.echo("--- 重启服务 ---")
        from .restart import restart as restart_cmd

        cb = getattr(restart_cmd, "callback", None)
        if cb is not None:
            cb()
