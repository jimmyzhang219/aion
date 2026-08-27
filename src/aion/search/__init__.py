"""Web 搜索策略模式：抽象基类、数据实体、provider 注册表。"""

from __future__ import annotations

from .base import WebSearchProvider
from .registry import create_provider
from .types import SearchRequest, SearchResultItem

__all__ = [
    "WebSearchProvider",
    "create_provider",
    "SearchRequest",
    "SearchResultItem",
]
