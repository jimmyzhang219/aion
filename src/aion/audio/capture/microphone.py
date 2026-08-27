"""macOS 麦克风采集 -- 使用 pyaudio（CoreAudio）。

音频数据从 pyaudio 回调（非 asyncio 线程）进入 threading.Queue，
read_chunks 通过 run_in_executor 将 blocking get() 转为 async 迭代。
"""

from __future__ import annotations

import asyncio
import logging
import queue
from collections.abc import AsyncIterator, Mapping

import pyaudio

from . import AudioSource

logger = logging.getLogger(__name__)

CHUNK_SEC = 0.1
SAMPLE_RATE = 16000
FORMAT = pyaudio.paInt16
CHANNELS = 1
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_SEC)


class MicrophoneSource(AudioSource):
    """macOS 麦克风音频源。"""

    def __init__(self, device_index: int | None = None) -> None:
        self._device_index = device_index
        self._stream: pyaudio.Stream | None = None
        self._pa: pyaudio.PyAudio | None = None
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._running = False

    @staticmethod
    def _find_builtin_mic(pa: pyaudio.PyAudio | None = None) -> int | None:
        """获取系统当前默认输入设备（系统设置→声音→输入）。

        这是 macOS 推荐的方式，与 Zoom、微信等 App 行为一致。
        用户可以在系统设置中切换默认设备，会自动生效。
        """
        if pa is None:
            pa = pyaudio.PyAudio()
            try:
                return int(pa.get_default_input_device_info()["index"])
            except OSError:
                return None
            finally:
                pa.terminate()
        try:
            return int(pa.get_default_input_device_info()["index"])
        except OSError:
            return None

    async def start(self) -> None:
        self._pa = pyaudio.PyAudio()
        device = self._device_index
        if device is None:
            device = self._find_builtin_mic(self._pa)
        logger.info("Opening mic device: %s", device)
        self._stream = self._pa.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            input_device_index=device,
            frames_per_buffer=CHUNK_SIZE,
            stream_callback=self._callback,
        )
        self._stream.start_stream()
        self._running = True

    def _callback(
        self, in_data: bytes | None, frame_count: int, time_info: Mapping[str, float], status: int
    ) -> tuple[bytes | None, int]:
        self._queue.put_nowait(in_data)
        return (None, pyaudio.paContinue)

    async def read_chunks(self) -> AsyncIterator[bytes]:
        while self._running:
            try:
                chunk = self._queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.01)
                continue
            if chunk is None:
                break
            yield chunk

    async def stop(self) -> None:
        self._running = False
        self._queue.put_nowait(None)
        if self._stream:
            if self._stream.is_active():
                self._stream.stop_stream()
            self._stream.close()
            self._stream = None
        if self._pa:
            self._pa.terminate()
            self._pa = None
