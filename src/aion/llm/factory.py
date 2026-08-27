"""LLM 工厂模块"""

from __future__ import annotations

from typing import Any

from langchain_openai.chat_models.base import BaseChatOpenAI

from .providers import ChatDeepSeekV4, ReasoningOpenAI
from .providers.maas import ChatMaaS
from ..config.defaults import PROVIDER_DEFAULTS


def _get_llm_class(model: str, provider_name: str) -> type[BaseChatOpenAI]:
    """根据模型名称/provider 名称返回对应的 LLM 实现类。

    规则：
    - ``model`` 以 ``deepseek-v4`` 开头 → ``ChatDeepSeekV4``（处理 reasoning_content 序列化）
    - ``provider_name`` 为 ``alicloud`` → ``ChatMaaS``（百炼 thinking 参数翻译）
    - 其余所有模型 → ``ReasoningOpenAI``（捕获 reasoning_content，支持 thinking 展示）
    """
    if model.startswith("deepseek-v4"):
        return ChatDeepSeekV4
    if provider_name.lower() == "alicloud":
        return ChatMaaS

    return ReasoningOpenAI


def create_llm(provider_name: str, provider_config: dict[str, Any]) -> BaseChatOpenAI:
    """根据配置创建 LLM 实例。

    支持任意 OpenAI 兼容 API 的模型。provider 名称参与分发（``alicloud`` → ChatMaaS），
    其余实现类由 ``provider_config["model"]`` 决定。

    Args:
        provider_name: provider 配置别名（用于 PROVIDER_DEFAULTS 查找与 alicloud 分发）
        provider_config: provider 配置字典，必须包含 ``model`` 和 ``apiKey``

    Returns:
        BaseChatOpenAI 实例（即 BaseChatModel 子类）
    """
    model_name: str = provider_config["model"]
    cls = _get_llm_class(model_name, provider_name)

    # 解析 base_url：优先 provider_config，其次 PROVIDER_DEFAULTS
    base_url: str | None = provider_config.get("baseUrl")
    if not base_url:
        defaults = PROVIDER_DEFAULTS.get(provider_name.lower(), {})
        base_url = defaults.get("baseUrl", "")

    # 非 LLM 参数（agent 内部使用，不传给 LLM 构造函数）
    known_keys = {"model", "apiKey", "baseUrl", "context_window", "balance"}
    extra_kwargs: dict[str, Any] = {k: v for k, v in provider_config.items() if k not in known_keys and v is not None}

    return cls(
        model=model_name,
        api_key=provider_config["apiKey"],
        base_url=base_url,
        **extra_kwargs,
    )
