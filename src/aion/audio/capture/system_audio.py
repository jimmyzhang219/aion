"""系统音频捕获 — 通过 BlackHole 虚拟音频驱动。"""

from __future__ import annotations

import logging
from typing import AsyncIterator

import pyaudio  # type: ignore[import-untyped]

from . import AudioSource
from .microphone import MicrophoneSource

logger = logging.getLogger(__name__)

BLACKHOLE_KEYWORDS = ("BlackHole", "blackhole")


def find_blackhole_device() -> int | None:
    pa = pyaudio.PyAudio()
    try:
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            name = str(info.get("name", ""))
            if any(kw.lower() in name.lower() for kw in BLACKHOLE_KEYWORDS):
                max_channels = int(info.get("maxInputChannels", 0))
                if max_channels > 0:
                    return i
        return None
    finally:
        pa.terminate()


class SystemAudioSource(AudioSource):
    """系统音频源 — 从 BlackHole 虚拟设备捕获。"""

    def __init__(self) -> None:
        self._device_index: int | None = None
        self._inner: MicrophoneSource | None = None

    async def start(self) -> None:
        device = find_blackhole_device()
        if device is None:
            raise RuntimeError("BlackHole 虚拟音频设备未找到。请先安装: brew install blackhole-2ch")
        self._device_index = device
        self._inner = MicrophoneSource(device_index=device)
        await self._inner.start()

    async def read_chunks(self) -> AsyncIterator[bytes]:
        if self._inner is None:
            raise RuntimeError("start() must be called before reading")
        async for chunk in self._inner.read_chunks():
            yield chunk

    async def stop(self) -> None:
        if self._inner:
            await self._inner.stop()
            self._inner = None
