"""Web 搜索策略模式 — 抽象基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .types import SearchRequest, SearchResultItem


class WebSearchProvider(ABC):
    """联网搜索策略抽象接口。

    各 provider 实现负责：解析自身配置、freshness 降级映射、
    发起 HTTP 请求、把响应解析为 ``list[SearchResultItem]``。
    """

    @abstractmethod
    def __init__(self, cfg: dict) -> None:
        """provider 配置入口（子类从 cfg 读取 apiKey 等）。"""

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """provider 标识（如 bocha / baidu）。"""

    @abstractmethod
    def search(self, request: SearchRequest) -> list[SearchResultItem]:
        """执行搜索，返回统一的结果列表。"""
