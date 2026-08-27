"""博查（Bocha）联网搜索 provider。

协议：POST，Authorization: Bearer，扁平请求体 {query,count,freshness,summary,...}；
解析 data.webPages.value[].{name,url,snippet}。
freshness 不支持 semiyear，降级为 month；country/language 直接透传。
端点固定（BOCHA_SEARCH_URL），不再可配。
"""

from __future__ import annotations

import httpx

from ..base import WebSearchProvider
from ..types import SearchRequest, SearchResultItem

BOCHA_SEARCH_URL = "https://api.bocha.cn/v1/web-search"
DEFAULT_TIMEOUT = 30.0


class BochaProvider(WebSearchProvider):
    """博查 Web 搜索 provider。"""

    def __init__(self, cfg: dict) -> None:
        self._api_key = cfg["apiKey"]

    @property
    def provider_id(self) -> str:
        return "bocha"

    def _map_freshness(self, freshness: str) -> str:
        """博查不支持 semiyear，降级为 month；其余原样透传。"""
        return "month" if freshness == "semiyear" else freshness

    def search(self, request: SearchRequest) -> list[SearchResultItem]:
        count = max(1, min(int(request.max_results or 8), 25))
        body: dict = {
            "query": request.query,
            "count": count,
            "freshness": self._map_freshness(request.freshness or "noLimit"),
            "summary": True,
        }
        if request.country:
            body["country"] = request.country
        if request.language:
            body["language"] = request.language
        resp = httpx.post(
            BOCHA_SEARCH_URL,
            json=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        pages = (data.get("data") or {}).get("webPages") or {}
        value = pages.get("value") or []
        return [
            SearchResultItem(
                title=p.get("name") or "",
                url=p.get("url") or "",
                snippet=p.get("snippet") or "",
            )
            for p in value
        ]
