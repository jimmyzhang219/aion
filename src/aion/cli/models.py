"""aion model - 模型配置管理命令

Usage:
    aion model add <name> --model <model> --api-key <key> [--base-url <url>]
    aion model list [--show-keys]
    aion model remove <name>
"""

from pathlib import Path

import click

from ..config.loader import load_config, save_config
from ..config.defaults import PROVIDER_DEFAULTS as KNOWN_PROVIDERS
from ..core.constants import DEFAULT_CONFIG_PATH, DEFAULT_PROVIDER

# 模型名前缀 → provider 名称映射（用于自动补全 baseUrl）
# 当 name 不在 PROVIDER_DEFAULTS 中、model 也不以任何 PROVIDER_DEFAULTS 键名开头时使用此映射
_MODEL_PREFIX_MAP: dict[str, str] = {
    "gpt-": "openai",
    "o1-": "openai",
    "o3-": "openai",
    "glm-": "glm",
    "moonshot": "moonshot",
    "kimi": "moonshot",
    "minimax": "minimax",
}


def _resolve_provider(name: str, model: str) -> tuple[str | None, int, int]:
    """从 provider 名称或模型名推断 baseUrl、context_window 与 max_tokens。

    Args:
        name: 配置中的 provider/模型别名。
        model: 实际模型 ID 字符串。

    Returns:
        ``(baseUrl, context_window, max_tokens)`` 元组；无法识别时 baseUrl 为 None。
    """
    info = KNOWN_PROVIDERS.get(name)
    if info:
        return info["baseUrl"], info["context_window"], info["max_tokens"]  # type: ignore[return-value]
    for provider_key, pi in KNOWN_PROVIDERS.items():
        if model.startswith(provider_key):
            return pi["baseUrl"], pi["context_window"], pi["max_tokens"]  # type: ignore[return-value]

    # 3. 模型名前缀匹配已知模式（如 gpt-4o → openai, glm-4 → glm）
    for prefix, provider_name in _MODEL_PREFIX_MAP.items():
        if model.startswith(prefix) and provider_name in KNOWN_PROVIDERS:
            pi = KNOWN_PROVIDERS[provider_name]
            return pi["baseUrl"], pi["context_window"], pi["max_tokens"]  # type: ignore[return-value]

    defaults = KNOWN_PROVIDERS[DEFAULT_PROVIDER]
    return None, defaults["context_window"], defaults["max_tokens"]  # type: ignore[return-value]


def add_model(
    config_path: str,
    name: str,
    model: str,
    api_key: str,
    base_url: str | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    stop: str | None = None,
    timeout: int | None = None,
) -> str:
    """添加模型配置，返回用户可见的消息。

    Args:
        config_path: 配置文件路径（保留参数签名，当前固定为 DEFAULT_CONFIG_PATH）。
        name: 模型配置别名（如 deepseek、openai）。
        model: 实际调用的模型 ID。
        api_key: API 密钥。
        base_url: 可选，自定义 API 端点。
        temperature: 可选，采样温度。
        top_p: 可选，nucleus sampling 参数。
        stop: 可选，停止词（逗号分隔多个）。
        timeout: 可选，请求超时秒数。

    Returns:
        成功或错误提示字符串。
    """
    cfg_path = Path(config_path)
    config = load_config(cfg_path).model_dump()
    models = config.setdefault("models", {})
    if name in models:
        return f"错误：模型 '{name}' 已存在"

    # 确定 baseUrl 和 context_window
    resolved_base_url: str | None
    if base_url:
        resolved_base_url = base_url
        resolved_context_window = KNOWN_PROVIDERS[DEFAULT_PROVIDER]["context_window"]
        resolved_max_tokens = KNOWN_PROVIDERS[DEFAULT_PROVIDER]["max_tokens"]
    else:
        resolved_base_url, resolved_context_window, resolved_max_tokens = _resolve_provider(name, model)

    if not resolved_base_url:
        return f"错误：未知 provider '{name}'，请通过 --base-url 提供 API 端点"

    entry: dict = {
        "model": model,
        "apiKey": api_key,
        "baseUrl": resolved_base_url,
        "context_window": resolved_context_window,
        "max_tokens": resolved_max_tokens,
    }
    if temperature is not None:
        entry["temperature"] = temperature
    if top_p is not None:
        entry["top_p"] = top_p
    if stop is not None:
        entry["stop"] = [s.strip() for s in stop.split(",")] if "," in stop else stop
    if timeout is not None:
        entry["timeout"] = timeout

    models[name] = entry
    save_config(config, cfg_path)
    return f"✓ 已添加模型 '{name}': {model} (context_window={resolved_context_window})"


def _mask_key(key: str) -> str:
    """脱敏 API Key 用于列表展示。

    Args:
        key: 原始 API Key。

    Returns:
        脱敏后的字符串，如 ``sk-***...1234``。
    """
    if len(key) <= 8:
        return key[:4] + "***"
    return key[:5] + "***..." + key[-4:]


def format_models_list(models: dict, show_keys: bool = False) -> str:
    """将模型配置格式化为终端表格字符串。

    Args:
        models: ``aion.json`` 中 models 段字典。
        show_keys: 为 True 时显示完整 API Key，否则脱敏。

    Returns:
        多行表格文本。
    """
    if not models:
        return "(暂无模型配置)"

    # 检查是否有任何模型配置了额外参数
    has_extra_params = any(
        any(k in cfg for k in ("temperature", "top_p", "stop", "timeout")) for cfg in models.values()
    )

    header = f"{'名称':<15} {'模型':<25} {'端点':<35} {'context_window':<16}"
    if has_extra_params:
        header += f" {'额外参数'}"
    header += f" {'API Key' if show_keys else 'API Key(脱敏)'}"
    sep_len = 135 if has_extra_params else 115
    lines = [header, "-" * sep_len]
    for name, cfg in sorted(models.items()):
        model_name = cfg.get("model", "?")
        base_url = cfg.get("baseUrl", "?")
        cw = str(cfg.get("context_window", "?"))
        key = cfg.get("apiKey", "")
        key_display = key if show_keys else _mask_key(key)
        line = f"{name:<15} {model_name:<25} {base_url:<35} {cw:<16}"
        if has_extra_params:
            extra_parts = []
            for k in ("temperature", "top_p", "stop", "timeout"):
                v = cfg.get(k)
                if v is not None:
                    extra_parts.append(f"{k}={v}")
            line += f" {', '.join(extra_parts):<20}" if extra_parts else f" {'-':<20}"
        line += f" {key_display}"
        lines.append(line)
    return "\n".join(lines)


def remove_model(config_path: str, name: str) -> str:
    """删除指定名称的模型配置。

    Args:
        config_path: 配置文件路径（保留参数签名，当前固定为 DEFAULT_CONFIG_PATH）。
        name: 待删除的模型别名。

    Returns:
        成功或错误提示字符串。
    """
    cfg_path = Path(config_path)
    config = load_config(cfg_path).model_dump()
    models = config.get("models", {})
    if name not in models:
        return f"错误：模型 '{name}' 不存在"

    del models[name]
    save_config(config, cfg_path)
    return f"✓ 已删除模型 '{name}'"


# ----- Click 命令定义 -----


@click.group("model")
def model():
    """管理 LLM 模型配置"""
    pass


@model.command("add")
@click.argument("name")
@click.option("--model", required=True, help="模型名称，如 deepseek-v4-flash、gpt-4o")
@click.option("--api-key", required=True, help="API 密钥")
@click.option("--base-url", help="API 端点（已知 provider 可省略）")
@click.option("--temperature", type=float, help="温度参数 (0.0-2.0)")
@click.option("--top-p", type=float, help="nucleus sampling 参数 (0.0-1.0)")
@click.option("--stop", help="停止词，多个用逗号分隔")
@click.option("--timeout", type=int, help="API 调用超时秒数")
def models_add(
    name: str,
    model: str,
    api_key: str,
    base_url: str | None,
    temperature: float | None,
    top_p: float | None,
    stop: str | None,
    timeout: int | None,
):
    """添加模型配置

    已知 provider (deepseek/openai/alicloud/minimax/moonshot/glm) 自动填充 baseUrl 和 context_window。

    \b
    示例：
        aion model add deepseek --model deepseek-v4-flash --api-key sk-xxx
        aion model add alicloud --model glm-5.1 --api-key sk-xxx
        aion model add myllm --model gpt-4o --api-key sk-xxx --base-url https://api.openai.com
    """
    try:
        msg = add_model(
            str(DEFAULT_CONFIG_PATH),
            name,
            model,
            api_key,
            base_url,
            temperature=temperature,
            top_p=top_p,
            stop=stop,
            timeout=timeout,
        )
    except FileNotFoundError:
        click.echo(f"配置文件不存在: {DEFAULT_CONFIG_PATH}")
        click.echo("请先运行: aion setup")
        return
    click.echo(msg)


@model.command("list")
@click.option("--show-keys", is_flag=True, help="显示完整的 API Key")
def models_list(show_keys: bool):
    """列出已配置的模型

    \b
    示例：
        aion model list
        aion model list --show-keys
    """
    try:
        config = load_config()
    except FileNotFoundError:
        click.echo(f"配置文件不存在: {DEFAULT_CONFIG_PATH}")
        click.echo("请先运行: aion setup")
        return
    models = getattr(config, "models", {})
    click.echo(format_models_list(models, show_keys=show_keys))


@model.command("remove")
@click.argument("name")
@click.confirmation_option(prompt="确定要删除该模型吗？ [y/n]", show_default=False)
def models_remove(name: str):
    """删除模型配置

    \b
    示例：
        aion model remove deepseek
    """
    try:
        msg = remove_model(str(DEFAULT_CONFIG_PATH), name)
    except FileNotFoundError:
        click.echo(f"配置文件不存在: {DEFAULT_CONFIG_PATH}")
        click.echo("请先运行: aion setup")
        return
    click.echo(msg)
