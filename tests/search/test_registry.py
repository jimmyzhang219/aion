"""create_provider factory 分发与配置迁移测试。"""

from __future__ import annotations

import pytest

from aion.config.schema import Config, resolve_search_provider
from aion.search import create_provider
from aion.search.providers.baidu import BaiduProvider
from aion.search.providers.bocha import BochaProvider


# ── resolve_search_provider：纯字典解析（含旧配置迁移） ──

def test_resolve_new_structure_bocha():
    cfg = {"webSearch": {"provider": "bocha", "providers": {
        "bocha": {"apiKey": "k1"}, "baidu": {"apiKey": "k2"}}}}
    pid, pcfg = resolve_search_provider(cfg)
    assert pid == "bocha"
    assert pcfg == {"apiKey": "k1"}


def test_resolve_new_structure_baidu_selected():
    cfg = {"webSearch": {"provider": "baidu", "providers": {
        "bocha": {"apiKey": "k1"}, "baidu": {"apiKey": "k2"}}}}
    pid, pcfg = resolve_search_provider(cfg)
    assert pid == "baidu"
    assert pcfg == {"apiKey": "k2"}


def test_resolve_legacy_flat_migrates_to_bocha():
    """旧平铺 apiKey 自动视作 providers.bocha（url 已固化为常量，不再透传）。"""
    cfg = {"webSearch": {"apiKey": "oldkey", "url": "oldurl"}}
    pid, pcfg = resolve_search_provider(cfg)
    assert pid == "bocha"
    assert pcfg == {"apiKey": "oldkey"}


def test_resolve_legacy_without_url():
    cfg = {"webSearch": {"apiKey": "k"}}
    pid, pcfg = resolve_search_provider(cfg)
    assert pid == "bocha"
    assert pcfg == {"apiKey": "k"}


def test_resolve_not_configured_returns_none():
    assert resolve_search_provider({"webSearch": {"apiKey": ""}}) is None
    assert resolve_search_provider({}) is None


def test_resolve_selected_provider_missing_key_returns_none():
    cfg = {"webSearch": {"provider": "baidu", "providers": {"baidu": {"apiKey": ""}}}}
    assert resolve_search_provider(cfg) is None


# ── create_provider：按 Config 实例化 ──

def test_create_provider_bocha():
    config = Config(search={"webSearch": {"provider": "bocha", "providers": {
        "bocha": {"apiKey": "k"}}}})
    provider = create_provider(config)
    assert isinstance(provider, BochaProvider)


def test_create_provider_baidu():
    config = Config(search={"webSearch": {"provider": "baidu", "providers": {
        "baidu": {"apiKey": "k"}}}})
    provider = create_provider(config)
    assert isinstance(provider, BaiduProvider)


def test_create_provider_returns_none_when_not_configured():
    config = Config(search={"webSearch": {"providers": {"bocha": {"apiKey": ""}}}})
    assert create_provider(config) is None


def test_create_provider_unknown_raises():
    config = Config(search={"webSearch": {"provider": "bing", "providers": {
        "bing": {"apiKey": "k"}}}})
    with pytest.raises(ValueError):
        create_provider(config)


def test_create_provider_legacy_config():
    """旧平铺配置经 create_provider 仍能拿到 BochaProvider。"""
    config = Config(search={"webSearch": {"apiKey": "k", "url": "u"}})
    provider = create_provider(config)
    assert isinstance(provider, BochaProvider)
