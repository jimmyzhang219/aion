"""tiktoken-based token counting utility.

统一计算文本/消息列表的 token 数。内置 cl100k_base 编码表（约 1.6MB），
无需首次联网下载。tiktoken 不可用时回退 CJK 感知粗估。
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ── cl100k_base 编码常量（来自 tiktoken_ext.openai_public） ──
_ENCODING_NAME = "cl100k_base"
_PAT_STR = (
    r"""'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+|"""
    r""" ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s"""
)
_SPECIAL_TOKENS = {
    "<|endoftext|>": 100257,
    "<|fim_prefix|>": 100258,
    "<|fim_middle|>": 100259,
    "<|fim_suffix|>": 100260,
    "<|endofprompt|>": 100276,
}
_EXPECTED_HASH = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"

# ── 内置编码资源路径（PyInstaller 兼容） ──
try:
    from importlib.resources import files as _res_files

    _ENC_PATH = str(_res_files("aion.llm._encodings").joinpath("cl100k_base.tiktoken"))
except Exception:
    _ENC_PATH = str(Path(__file__).parent / "_encodings" / "cl100k_base.tiktoken")

# ── 初始化 tiktoken 编码器 ──
_tiktoken_enc: object | None = None
try:
    import tiktoken
    import tiktoken.load as _tiktoken_load

    _mergeable_ranks = _tiktoken_load.load_tiktoken_bpe(_ENC_PATH, expected_hash=_EXPECTED_HASH)
    _tiktoken_enc = tiktoken.core.Encoding(
        name=_ENCODING_NAME,
        pat_str=_PAT_STR,
        mergeable_ranks=_mergeable_ranks,
        special_tokens=_SPECIAL_TOKENS,
    )
except Exception as exc:
    logger.warning("tiktoken init failed (will use fallback): %s", exc)
    _tiktoken_enc = None


def _get_encoding() -> object | None:
    """返回缓存中的 tiktoken encoding，None 表示不可用。"""
    return _tiktoken_enc


def _estimate_fallback(text: str) -> int:
    """CJK 感知的 token 粗估（tiktoken 不可用时的回退）。"""
    total = 0
    for c in text:
        total += 4 if ord(c) > 127 else 1
    return total // 4


def count_tokens(text: str, model: str = _ENCODING_NAME) -> int:
    """返回 text 中的 token 数。

    Args:
        text: 待计数字符串。非 str 或空串返回 0。
        model: 仅用于 API 兼容签名，实际使用模块加载时初始化的 encoding（默认 cl100k_base）。
               项目使用的 DeepSeek / GPT-4 系列与 cl100k_base 兼容。

    Returns:
        token 总数（tiktoken 不可用时回退 CJK 感知粗估）。
    """
    if not isinstance(text, str) or not text:
        return 0
    enc = _get_encoding()
    if enc is None:
        return _estimate_fallback(text)
    assert hasattr(enc, "encode")
    return len(enc.encode(text, disallowed_special=()))


from aion.channels.constants import ContentBlockType


# 多模态块 token 粗估（各模型不尽相同，取合理下界）
# image: ~100-170 tokens, video: ~1000-2000 tokens, audio: ~500 tokens
_MULTIMODAL_TOKEN_ESTIMATES: dict[str, int] = {
    ContentBlockType.IMAGE.value: 100,
    "image_url": 100,
    ContentBlockType.VIDEO.value: 1500,
    "video_url": 1500,
    ContentBlockType.AUDIO.value: 500,
}


def count_message_tokens(messages: list[dict], model: str = _ENCODING_NAME) -> int:
    """返回消息列表中所有 content 的 token 总数。

    支持 content 为 str 或 list[block]（多模态）两种格式。
    多模态块（image/video/audio）按粗估计入，不计 base64 实际长度
    （API 对图片/视频按分辨率/帧数计费，而非传输体量）。

    Args:
        messages: 消息字典列表。
        model: 透传给 count_tokens，仅用于 API 兼容签名。

    Returns:
        token 总数。
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += count_tokens(content, model)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if "text" in block:
                        total += count_tokens(block["text"], model)
                    elif block.get("type") in _MULTIMODAL_TOKEN_ESTIMATES:
                        total += _MULTIMODAL_TOKEN_ESTIMATES[block["type"]]
    return total
