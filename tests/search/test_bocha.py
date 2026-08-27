"""BochaProvider 请求构造、响应解析、freshness 映射测试。"""

from __future__ import annotations

import aion.search.providers.bocha as bocha_mod
from aion.search.providers.bocha import BochaProvider
from aion.search.types import SearchRequest


class _FakeResponse:
    """模拟 httpx.Response。"""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def _patch_post(monkeypatch, payload, recorder):
    """把 bocha 模块的 httpx.post 替换为记录调用的假函数。"""

    def fake_post(url, json=None, headers=None, timeout=None):
        recorder["url"] = url
        recorder["json"] = json
        recorder["headers"] = headers
        return _FakeResponse(payload)

    monkeypatch.setattr(bocha_mod.httpx, "post", fake_post)


def test_parses_webpages_value(monkeypatch):
    """解析 data.webPages.value[].{name,url,snippet}。"""
    rec: dict = {}
    _patch_post(
        monkeypatch,
        {"data": {"webPages": {"value": [
            {"name": "T1", "url": "http://a", "snippet": "S1"},
            {"name": "T2", "url": "http://b", "snippet": "S2"},
        ]}}},
        rec,
    )
    provider = BochaProvider({"apiKey": "k"})
    items = provider.search(SearchRequest("hello", max_results=5))

    assert len(items) == 2
    assert items[0].title == "T1"
    assert items[0].url == "http://a"
    assert items[0].snippet == "S1"
    assert items[0].date == ""


def test_request_body_and_auth(monkeypatch):
    """请求体含 query/count/freshness/summary，Authorization 带 Bearer，发往固定端点。"""
    rec: dict = {}
    _patch_post(monkeypatch, {"data": {"webPages": {"value": []}}}, rec)
    provider = BochaProvider({"apiKey": "secret"})
    provider.search(SearchRequest("q", max_results=3, country="CN", language="zh"))

    assert rec["url"] == "https://api.bocha.cn/v1/web-search"
    assert rec["headers"]["Authorization"] == "Bearer secret"
    body = rec["json"]
    assert body["query"] == "q"
    assert body["count"] == 3
    assert body["freshness"] == "noLimit"
    assert body["summary"] is True
    assert body["country"] == "CN"
    assert body["language"] == "zh"


def test_uses_fixed_url(monkeypatch):
    """始终发往固定博查端点（URL 不再可配）。"""
    rec: dict = {}
    _patch_post(monkeypatch, {"data": {"webPages": {"value": []}}}, rec)
    BochaProvider({"apiKey": "k"}).search(SearchRequest("q"))
    assert rec["url"] == "https://api.bocha.cn/v1/web-search"


def test_freshness_semiyear_degrades_to_month(monkeypatch):
    """博查不支持 semiyear，降级为 month。"""
    rec: dict = {}
    _patch_post(monkeypatch, {"data": {"webPages": {"value": []}}}, rec)
    BochaProvider({"apiKey": "k"}).search(SearchRequest("q", freshness="semiyear"))
    assert rec["json"]["freshness"] == "month"


def test_empty_value_returns_empty_list(monkeypatch):
    """无 value 时返回空列表（上层格式化为「无结果」）。"""
    rec: dict = {}
    _patch_post(monkeypatch, {"data": {"webPages": {"value": []}}}, rec)
    items = BochaProvider({"apiKey": "k"}).search(SearchRequest("q"))
    assert items == []


def test_provider_id():
    assert BochaProvider({"apiKey": "k"}).provider_id == "bocha"
