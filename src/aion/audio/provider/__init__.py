"""ASR 云端服务策略模式 — 抽象基类、数据类型、工厂。"""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncIterator


@dataclass
class TranscriptChunk:
    """ASR 返回的一条识别结果。"""

    begin_time: int  # ms，相对音频起始
    end_time: int  # ms
    text: str
    is_final: bool  # False=中间结果, True=最终结果
    confidence: float  # 0-1


@dataclass
class FileTranscript:
    """录音文件识别的完整结果。"""

    sentences: list[TranscriptChunk]
    duration_secs: float | None = None


class ASRProvider(ABC):
    """ASR 云服务策略抽象接口。"""

    name: str = ""

    @abstractmethod
    def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
    ) -> AsyncIterator[TranscriptChunk]: ...

    @abstractmethod
    async def transcribe_file(self, audio_path: str) -> FileTranscript: ...

    @abstractmethod
    async def check_health(self) -> bool: ...


def create_provider(name: str, config: dict[str, Any]) -> ASRProvider:
    """ASR 提供者工厂。"""
    from .aliyun import AliyunProvider
    from .baidu import BaiduProvider

    registry: dict[str, type] = {
        "aliyun": AliyunProvider,
        "baidu": BaiduProvider,
    }

    # macOS 原生 ASR（仅 macOS）
    if sys.platform == "darwin":
        from .macos import MacOSProvider  # type: ignore[import-untyped]

        registry["macos"] = MacOSProvider

    # Whisper 本地模型（可选依赖）
    try:
        from .whisper import WhisperProvider

        registry["whisper"] = WhisperProvider
    except ImportError:
        pass

    name = name.lower()
    if name not in registry:
        raise ValueError(f"Unknown ASR provider: {name}, available: {list(registry)}")
    return registry[name](config)
