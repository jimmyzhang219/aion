"""音频采集抽象 — AudioSource 基类 + 统一流接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator


class AudioSource(ABC):
    """音频源抽象基类。

    所有采集方式（麦克风/系统音频/文件导入）继承此类，
    对外暴露统一的异步字节流接口。
    """

    @abstractmethod
    def read_chunks(self) -> AsyncIterator[bytes]:
        """产生 16kHz 16bit PCM 音频块。"""
        ...

    @abstractmethod
    async def start(self) -> None:
        """准备音频源（打开设备/文件等）。"""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """停止采集并释放资源。"""
        ...
