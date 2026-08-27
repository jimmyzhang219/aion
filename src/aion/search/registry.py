"""Web 搜索策略注册表与工厂。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import WebSearchProvider
from .providers.baidu import BaiduProvider
from .providers.bocha import BochaProvider

if TYPE_CHECKING:
    from ..config.schema import Config

_PROVIDERS: dict[str, type[WebSearchProvider]] = {
    "bocha": BochaProvider,
    "baidu": BaiduProvider,
}


def create_provider(config: "Config") -> WebSearchProvider | None:
    """根据配置实例化搜索 provider。

    Args:
        config: 已加载的 ``Config`` 对象。

    Returns:
        provider 实例；未配置（无 apiKey）时返回 None。

    Raises:
        ValueError: provider id 未知（配置错误，需显式暴露）。
    """
    result = config.get_search_provider()
    if result is None:
        return None
    provider_id, cfg = result
    cls = _PROVIDERS.get(provider_id)
    if cls is None:
        raise ValueError(
            f"Unknown web search provider: {provider_id}, available: {list(_PROVIDERS)}"
        )
    return cls(cfg)
