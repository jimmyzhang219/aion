"""BaiduProvider 请求构造、响应解析、freshness 降级测试。"""

from __future__ import annotations

import aion.search.providers.baidu as baidu_mod
from aion.search.providers.baidu import BaiduProvider
from aion.search.types import SearchRequest


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def _patch_post(monkeypatch, payload, recorder):
    def fake_post(url, json=None, headers=None, timeout=None):
        recorder["url"] = url
        recorder["json"] = json
        recorder["headers"] = headers
        return _FakeResponse(payload)

    monkeypatch.setattr(baidu_mod.httpx, "post", fake_post)


def test_parses_references(monkeypatch):
    """解析 references[].{title,url,content,date}。"""
    rec: dict = {}
    _patch_post(
        monkeypatch,
        {"request_id": "x", "references": [
            {"title": "T1", "url": "http://a", "content": "C1", "date": "2026-08-10", "type": "web"},
        ]},
        rec,
    )
    items = BaiduProvider({"apiKey": "k"}).search(SearchRequest("hello", max_results=5))

    assert len(items) == 1
    assert items[0].title == "T1"
    assert items[0].url == "http://a"
    assert items[0].snippet == "C1"  # content → snippet
    assert items[0].date == "2026-08-10"


def test_request_body_and_auth(monkeypatch):
    """请求体用 messages + search_source + resource_type_filter；X-Appbuilder-Authorization。"""
    rec: dict = {}
    _patch_post(monkeypatch, {"references": []}, rec)
    BaiduProvider({"apiKey": "secret"}).search(SearchRequest("q", max_results=7, country="CN"))

    assert rec["url"] == "https://qianfan.baidubce.com/v2/ai_search/web_search"
    assert rec["headers"]["X-Appbuilder-Authorization"] == "Bearer secret"
    body = rec["json"]
    assert body["messages"] == [{"content": "q", "role": "user"}]
    assert body["search_source"] == "baidu_search_v2"
    assert body["resource_type_filter"] == [{"type": "web", "top_k": 7}]
    # country 百度不支持，不应出现
    assert "country" not in body


def test_freshness_no_limit_omits_filter(monkeypatch):
    """noLimit → 省略 search_recency_filter。"""
    rec: dict = {}
    _patch_post(monkeypatch, {"references": []}, rec)
    BaiduProvider({"apiKey": "k"}).search(SearchRequest("q", freshness="noLimit"))
    assert "search_recency_filter" not in rec["json"]


def test_freshness_day_degrades_to_week(monkeypatch):
    """百度不支持 day，降级 week。"""
    rec: dict = {}
    _patch_post(monkeypatch, {"references": []}, rec)
    BaiduProvider({"apiKey": "k"}).search(SearchRequest("q", freshness="day"))
    assert rec["json"]["search_recency_filter"] == "week"


def test_freshness_passed_through(monkeypatch):
    """week/month/semiyear/year 原样传递。"""
    for f in ("week", "month", "semiyear", "year"):
        rec: dict = {}
        _patch_post(monkeypatch, {"references": []}, rec)
        BaiduProvider({"apiKey": "k"}).search(SearchRequest("q", freshness=f))
        assert rec["json"]["search_recency_filter"] == f


def test_empty_references_returns_empty_list(monkeypatch):
    rec: dict = {}
    _patch_post(monkeypatch, {"references": []}, rec)
    items = BaiduProvider({"apiKey": "k"}).search(SearchRequest("q"))
    assert items == []


def test_provider_id():
    assert BaiduProvider({"apiKey": "k"}).provider_id == "baidu"
