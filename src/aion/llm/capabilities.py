"""LLM 能力检测 — 使用 llmcapa 判断模型是否支持指定模态

提供 llmcapa 的薄封装，处理 llmcapa 内置注册表未覆盖的模型。
"""

import logging

import llmcapa  # type: ignore[import]
from llmcapa import Capability, ModelNotFoundError  # type: ignore[import]

logger = logging.getLogger(__name__)

# ── Fallback 注册表：llmcapa 内置注册表未覆盖的模型 ──
# 当 llmcapa.get(model_name) 抛出 ModelNotFoundError 时，查此表。
# key = 模型名（小写），value = 支持的输入模态集合
_FALLBACK_REGISTRY: dict[str, set[str]] = {}


def _find_fallback(model_name: str) -> tuple[str, set[str]] | None:
    """在 Fallback 注册表中按前缀匹配模型名。

    Returns:
        (matched_key, modalities) — 无匹配时返回 None。
    """
    lower_name = model_name.lower()
    for key, mods in _FALLBACK_REGISTRY.items():
        if lower_name.startswith(key):
            return key, mods
    return None


def _ensure_registered(model_name: str) -> None:
    """将 fallback 模型注册到 llmcapa 注册表（首次使用时调用）"""
    match = _find_fallback(model_name)
    if match is None:
        return
    key, modalities = match

    reg = llmcapa.default_registry()
    # 检查是否已注册（避免重复注册）
    try:
        reg.get(model_name)
        return  # 已存在
    except ModelNotFoundError:
        pass

    try:
        cap = Capability(
            provider="unknown",
            model_id=model_name,
            display_name=model_name,
            input_modalities=list(modalities),
            supports_function_calling=True,
            supports_streaming=True,
            supports_reasoning=True,
        )
        reg.register(cap)
        logger.debug("Registered fallback capability for %s: %s", model_name, modalities)
    except Exception as e:
        logger.warning("Failed to register fallback capability for %s: %s", model_name, e)


def get_supported_modalities(model_name: str) -> set[str]:
    """获取模型支持的输入模态集合

    Args:
        model_name: 模型名称（如 ``"glm-5.1"``）

    Returns:
        支持的模态集合，如 ``{"text", "image"}``；
        llmcapa 和 fallback 都查不到时保守返回 ``{"text"}``
    """
    try:
        cap = llmcapa.get(model_name)
        return set(cap.input_modalities)
    except ModelNotFoundError:
        match = _find_fallback(model_name)
        if match is not None:
            _ensure_registered(model_name)
            return match[1]
        logger.debug("Model %s not found in llmcapa, assuming text-only", model_name)
        return {"text"}
    except Exception as e:
        logger.warning("llmcapa.get(%r) failed: %s", model_name, e)
        return {"text"}


def check_modality_support(
    model_name: str,
    required: set[str],
) -> tuple[bool, set[str]]:
    """检查模型是否支持所有要求的模态

    Args:
        model_name: 模型名称
        required: 需要的模态集合，如 ``{"image", "video"}``

    Returns:
        (all_supported, unsupported_modalities)
    """
    supported = get_supported_modalities(model_name)
    unsupported = required - supported
    return len(unsupported) == 0, unsupported
