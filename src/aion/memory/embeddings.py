"""Embedding 模型工厂：根据 aion.json 配置创建对应的 LangChain Embeddings 实例。

支持 Provider:
- openai: langchain_openai.OpenAIEmbeddings（兼容智谱等国内厂商）
- ollama: langchain_ollama.OllamaEmbeddings（BGE 等本地模型）

所有失败路径返回 None，不抛异常，调用方自然降级为关键词搜索。
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def create_embeddings(config: dict[str, Any] | None) -> Any | None:
    """根据 memory.embedding 配置创建 Embeddings 实例。

    Args:
        config: ``memory.embedding`` 配置字典，含 provider + 各 provider 子段。

    Returns:
        LangChain ``Embeddings`` 实例；配置缺失/缺依赖/缺 key 时返回 None。
    """
    if not config:
        return None

    provider = config.get("provider")
    if not provider:
        return None

    provider_cfg = config.get(provider, {})
    if not provider_cfg:
        logger.warning("[Embeddings] provider=%s 配置段为空", provider)
        return None

    try:
        return _build(provider, provider_cfg)
    except Exception as e:
        logger.warning("[Embeddings] 创建 %s 嵌入失败: %s", provider, e)
        return None


def _build(provider: str, cfg: dict[str, Any]) -> Any | None:
    match provider:
        case "openai":
            return _openai(cfg)
        case "ollama":
            return _ollama(cfg)
        case _:
            logger.warning("[Embeddings] 未知 provider: %s", provider)
            return None


def _openai(cfg: dict[str, Any]) -> Any | None:
    try:
        from langchain_openai import OpenAIEmbeddings
    except ImportError:
        logger.debug("[Embeddings] langchain_openai 未安装")
        return None
    api_key = cfg.get("api_key") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.debug("[Embeddings] OpenAI API key 未配置")
        return None
    return OpenAIEmbeddings(
        model=cfg.get("model", "text-embedding-3-small"),
        openai_api_key=api_key,  # type: ignore[call-arg]
    )


def _ollama(cfg: dict[str, Any]) -> Any | None:
    try:
        from langchain_ollama import OllamaEmbeddings
    except ImportError:
        logger.warning("[Embeddings] langchain_ollama 未安装，pip install langchain-ollama")
        return None
    try:
        import httpx

        transport = httpx.HTTPTransport()
    except ImportError:
        transport = None
    kwargs = dict(
        model=cfg.get("model", "bge-m3"),
        base_url=cfg.get("base_url", "http://localhost:11434"),
    )
    if transport is not None:
        kwargs["sync_client_kwargs"] = {"transport": transport}
    return OllamaEmbeddings(**kwargs)
