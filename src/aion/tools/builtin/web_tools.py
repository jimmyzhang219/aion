"""web_fetch / web_search 工具模块

HTTP 页面抓取（web_fetch）与联网搜索（web_search）。
搜索按配置实例化 provider 执行（见 ``aion.search`` 策略模式）。

@tool 版本只暴露 LLM 需要的参数。
"""

from __future__ import annotations

import httpx
from langchain_core.tools import tool

from ...config.loader import load_config as _load_config
from ...log import get_logger
from ...search import SearchRequest, SearchResultItem, create_provider

logger = get_logger(__name__)

# web_fetch 默认最大返回字符数
DEFAULT_FETCH_CHARS = 80_000
# HTTP 请求默认超时（秒）
DEFAULT_TIMEOUT = 30.0


def web_fetch_impl(
    url: str,
    max_chars: int = DEFAULT_FETCH_CHARS,
    timeout: float = DEFAULT_TIMEOUT,
    extract_mode: str = "text",
) -> str:
    """抓取 URL 内容并提取可读文本的底层实现

    HTML 页面会尝试用 BeautifulSoup 去 script/style 后取纯文本；
    非 HTML 直接返回 body。超长内容保留首尾各半。
    extract_mode="markdown" 时尝试输出更紧凑的格式。

    Args:
        url: 目标 URL
        max_chars: 最大返回字符数（1000–500000）
        timeout: HTTP 超时秒数
        extract_mode: 提取模式，"text"（纯文本）或 "markdown"（紧凑格式）

    Returns:
        页面文本或错误说明
    """
    if not (url or "").strip():
        logger.warning("[web_fetch] url 为空")
        return "错误：url 不能为空"
    max_c = max(1000, min(int(max_chars or DEFAULT_FETCH_CHARS), 500_000))
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": "aion-agent/1.0 (web_fetch)"},
        ) as client:
            r = client.get(url.strip())
            r.raise_for_status()
            ct = (r.headers.get("content-type") or "").lower()
            body = r.text
    except Exception as e:
        logger.warning("[web_fetch] 请求失败: %s", e)
        return f"错误：请求失败 {e}"

    if "html" in ct:
        from bs4 import BeautifulSoup

        try:
            soup = BeautifulSoup(body, "lxml")  # lxml 是 C 解析器，更快
        except Exception:
            soup = BeautifulSoup(body, "html.parser")  # 纯 Python fallback
        for tag in soup.find_all(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text("\n", strip=True)
    else:
        text = body

    # 超长正文保留首尾各半，避免丢失页脚/错误信息
    if len(text) > max_c:
        half = max_c // 2
        text = text[:half] + "\n\n...[truncated]...\n\n" + text[-half:]
    return text


def _format_results(items: list[SearchResultItem], provider_id: str) -> str:
    """把统一结果列表格式化为 LLM 可读字符串。

    Args:
        items: provider 返回的统一结果列表。
        provider_id: provider 标识，用于空结果时的来源标注。

    Returns:
        格式化字符串；空结果返回 ``[<provider_id>: 无搜索结果]``。
    """
    if not items:
        return f"[{provider_id}: 无搜索结果]"
    lines = []
    for i, item in enumerate(items, start=1):
        lines.append(f"{i}. {item.title}\n   {item.url}\n   {item.snippet}")
    return "\n".join(lines)


def web_search_impl(
    query: str,
    max_results: int = 8,
    freshness: str = "noLimit",
    country: str = "",
    language: str = "",
) -> str:
    """联网搜索的底层实现（按配置实例化 provider 执行搜索）。

    Args:
        query: 搜索关键词。
        max_results: 最大结果条数（1–25）。
        freshness: 时间过滤（noLimit/day/week/month/semiyear/year）。
        country: 国家代码过滤（博查支持）。
        language: 语言代码过滤（博查支持）。

    Returns:
        格式化搜索结果或错误说明。
    """
    if not (query or "").strip():
        logger.warning("[web_search] query 为空")
        return "错误：query 不能为空"
    config = _load_config()
    provider = create_provider(config)
    if provider is None:
        logger.warning("[web_search] 未配置")
        return "错误：web_search 未配置，请在 aion.json 中配置 search.webSearch"
    max_r = max(1, min(int(max_results or 8), 25))
    items = provider.search(
        SearchRequest(
            query=query.strip(),
            max_results=max_r,
            freshness=freshness,
            country=country,
            language=language,
        )
    )
    return _format_results(items, provider.provider_id)


# ── @tool 版本只暴露 LLM 需要的参数 ──


@tool(parse_docstring=True)
def web_fetch(
    url: str,
    max_chars: int = DEFAULT_FETCH_CHARS,
    timeout: float = DEFAULT_TIMEOUT,
    extractMode: str = "text",
) -> str:
    """从 URL 抓取并提取可读内容（HTML 转文本）。
    用于不需要浏览器自动化的轻量级页面访问。
    如需搜索网页，使用 web_search。
    提取模式 extractMode="markdown" 可输出更紧凑的格式。

    Args:
        url: 目标 URL
        max_chars: 最大返回字符，默认 80000
        timeout: 超时秒数，默认 30
        extractMode: 提取模式，"text"（纯文本）或 "markdown"（紧凑格式）
    """
    return web_fetch_impl(url, max_chars=max_chars, timeout=timeout, extract_mode=extractMode)


@tool(parse_docstring=True)
def web_search(
    query: str,
    max_results: int = 8,
    freshness: str = "noLimit",
    country: str = "",
    language: str = "",
) -> str:
    """搜索网络。返回搜索结果用于查找当前信息。
    如需抓取特定 URL 的内容，使用 web_fetch。
    支持 freshness、country、language 等过滤参数。

    Args:
        query: 搜索关键词
        max_results: 默认 8，最大 25
        freshness: 时间过滤，可选 noLimit/day/week/month/semiyear/year，默认 noLimit
        country: 国家代码过滤（如 CN、US），可选
        language: 语言代码过滤（如 zh、en），可选
    """
    return web_search_impl(
        query,
        max_results=max_results,
        freshness=freshness,
        country=country,
        language=language,
    )
