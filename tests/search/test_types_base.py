"""search 包数据实体与抽象基类契约测试。"""

from __future__ import annotations

import pytest

from aion.search.base import WebSearchProvider
from aion.search.types import SearchRequest, SearchResultItem


def test_search_request_defaults():
    """SearchRequest 仅传 query 时其余字段取默认值。"""
    r = SearchRequest("hello")
    assert r.query == "hello"
    assert r.max_results == 8
    assert r.freshness == "noLimit"
    assert r.country == ""
    assert r.language == ""


def test_search_result_item_optional_date():
    """SearchResultItem date 默认空，可指定。"""
    item = SearchResultItem(title="T", url="http://a", snippet="S")
    assert item.date == ""
    item2 = SearchResultItem(title="T", url="http://a", snippet="S", date="2026-08-10")
    assert item2.date == "2026-08-10"


def test_provider_is_abstract():
    """WebSearchProvider 不可直接实例化。"""
    with pytest.raises(TypeError):
        WebSearchProvider()  # type: ignore[abstract]


def test_provider_subclass_must_implement():
    """缺抽象方法的子类不可实例化。"""

    class _Incomplete(WebSearchProvider):  # type: ignore[abstract]
        pass

    with pytest.raises(TypeError):
        _Incomplete()
