"""音频文件导入 — 使用 pydub 解码后按 chunk 模拟实时流。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import AsyncIterator

from pydub import AudioSegment

from . import AudioSource

logger = logging.getLogger(__name__)

CHUNK_MS = 100
SUPPORTED_EXTENSIONS = frozenset({".mp3", ".wav", ".m4a", ".aac", ".ogg"})


class FileAudioSource(AudioSource):
    """文件音频源 — 将音频文件解码为 16kHz PCM 流。"""

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._audio: AudioSegment | None = None
        self._position = 0
        self._stopped = False
        if self._path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported audio format: {self._path.suffix}. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            )

    async def start(self) -> None:
        if not self._path.exists():
            raise FileNotFoundError(f"Audio file not found: {self._path}")
        loop = asyncio.get_running_loop()
        self._audio = await loop.run_in_executor(None, lambda: AudioSegment.from_file(str(self._path)))
        assert self._audio is not None  # narrow type for mypy
        self._audio = self._audio.set_frame_rate(16000).set_channels(1)
        self._position = 0

    async def read_chunks(self) -> AsyncIterator[bytes]:
        if self._audio is None:
            raise RuntimeError("start() must be called before reading")
        while self._position < len(self._audio) and not self._stopped:
            chunk = self._audio[self._position : self._position + CHUNK_MS]
            self._position += CHUNK_MS
            yield chunk.raw_data

    async def stop(self) -> None:
        self._stopped = True
        self._audio = None

    @property
    def duration_ms(self) -> int:
        return len(self._audio) if self._audio else 0
