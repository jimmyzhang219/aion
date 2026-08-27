"""百度千帆（ai_search）联网搜索 provider。

协议：POST，X-Appbuilder-Authorization: Bearer；
请求体 messages + search_source + resource_type_filter(top_k 控制数量)；
解析 references[].{title,url,content,date}。
不支持 country/language（忽略）；freshness noLimit 省略、day 降级 week。
"""

from __future__ import annotations

import httpx

from ..base import WebSearchProvider
from ..types import SearchRequest, SearchResultItem

BAIDU_SEARCH_URL = "https://qianfan.baidubce.com/v2/ai_search/web_search"
DEFAULT_TIMEOUT = 30.0

# 统一 freshness → 百度 search_recency_filter 映射
# noLimit: 省略过滤参数；day: 降级 week（百度不支持 day）
_BAIDU_RECENCY: dict[str, str | None] = {
    "noLimit": None,
    "day": "week",
    "week": "week",
    "month": "month",
    "semiyear": "semiyear",
    "year": "year",
}


class BaiduProvider(WebSearchProvider):
    """百度千帆 Web 搜索 provider。"""

    def __init__(self, cfg: dict) -> None:
        self._api_key = cfg["apiKey"]

    @property
    def provider_id(self) -> str:
        return "baidu"

    def search(self, request: SearchRequest) -> list[SearchResultItem]:
        count = max(1, min(int(request.max_results or 8), 50))  # web top_k 上限 50
        body: dict = {
            "messages": [{"content": request.query, "role": "user"}],
            "search_source": "baidu_search_v2",
            "resource_type_filter": [{"type": "web", "top_k": count}],
        }
        recency = _BAIDU_RECENCY.get(request.freshness or "noLimit")
        if recency:
            body["search_recency_filter"] = recency
        resp = httpx.post(
            BAIDU_SEARCH_URL,
            json=body,
            headers={
                "Content-Type": "application/json",
                "X-Appbuilder-Authorization": f"Bearer {self._api_key}",
            },
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        references = data.get("references") or []
        return [
            SearchResultItem(
                title=r.get("title") or "",
                url=r.get("url") or "",
                snippet=r.get("content") or "",
                date=r.get("date") or "",
            )
            for r in references
        ]
