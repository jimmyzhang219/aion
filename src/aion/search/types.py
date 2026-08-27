"""Web 搜索策略模式 — 请求与响应数据实体。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SearchRequest:
    """统一搜索请求。

    Attributes:
        query: 搜索关键词。
        max_results: 期望结果条数（1–25）。
        freshness: 时间过滤，统一枚举 noLimit/day/week/month/semiyear/year。
        country: 国家代码（如 CN），博查支持、百度忽略。
        language: 语言代码（如 zh），博查支持、百度忽略。
    """

    query: str
    max_results: int = 8
    freshness: str = "noLimit"
    country: str = ""
    language: str = ""


@dataclass
class SearchResultItem:
    """单条搜索结果。

    Attributes:
        title: 标题。
        url: 网址。
        snippet: 摘要片段。
        date: 发布时间（百度有，博查无），可选。
    """

    title: str
    url: str
    snippet: str
    date: str = ""
