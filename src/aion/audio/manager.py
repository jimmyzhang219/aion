"""ASR 高层编排 — ASRManager 会话管理器。"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import AsyncIterator

from aion.audio.capture import AudioSource
from aion.audio.capture.file_import import FileAudioSource
from aion.audio.capture.microphone import MicrophoneSource
from aion.audio.capture.system_audio import SystemAudioSource
from aion.audio.provider import ASRProvider, create_provider
from aion.audio.transcript import TranscriptRecorder
from aion.config.loader import load_config, resolve_workspace_dir
from aion.core.constants import AION_HOME

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 会话 ID 生成（线程安全）
# ---------------------------------------------------------------------------

_lock: threading.Lock = threading.Lock()
_counter: int = 0


def _next_session_id() -> str:
    """生成 ``asr-YYYYMMDD-HHMMSS-N`` 格式的会话 ID。

    N 是进程生命周期内的单调递增计数器，由线程锁保护。
    """
    global _counter
    now = datetime.now()
    date_part = now.strftime("%Y%m%d")
    time_part = now.strftime("%H%M%S")
    with _lock:
        _counter += 1
        n = _counter
    return f"asr-{date_part}-{time_part}-{n}"


# ---------------------------------------------------------------------------
# 自定义异常
# ---------------------------------------------------------------------------


class ASRError(Exception):
    """ASR 操作自定义异常基类。"""


# ---------------------------------------------------------------------------
# ASRManager
# ---------------------------------------------------------------------------


class ASRManager:
    """ASR 高层编排管理器。

    ::

        mgr = ASRManager(foreground=True)
        session_id = mgr.start_mic()          # 同步启动
        async for line in mgr.run():          # 异步转写循环
            print(line)

    职责范围：
    - 配置快照（aion.json → workspace_dir / provider 配置）
    - Provider 创建与健康检查
    - 音频源生命周期管理（start／stop）
    - 转录循环调度（tee 音频流、调用 provider、写入 recorder）
    - 前台模式实时输出格式化文字
    - 会话结束后资源清理
    """

    def __init__(self, foreground: bool = False) -> None:
        self._foreground = foreground
        self._source: AudioSource | None = None
        self._provider: ASRProvider | None = None
        self._recorder: TranscriptRecorder | None = None
        self._session_id: str = ""
        self._running: bool = False

    # ── 属性 ────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        """当前是否有活跃的 ASR 会话。"""
        return self._running

    @property
    def session_id(self) -> str:
        """当前会话 ID；无活跃会话时返回空字符串。"""
        return self._session_id

    @property
    def recorder(self) -> TranscriptRecorder | None:
        """当前会话的 TranscriptRecorder 实例；无活跃会话时返回 ``None``。"""
        return self._recorder

    # ── 启动（异步）───────────────────────────────────────────

    async def start_mic(self, device_index: int | None = None) -> str:
        """启动麦克风录音会话（异步）。

        Args:
            device_index: pyaudio 设备索引，None 表示自动选择内置麦克风。
        """
        return await self._start(MicrophoneSource(device_index=device_index))

    async def start_system_audio(self) -> str:
        """启动系统音频捕获会话（异步）。"""
        return await self._start(SystemAudioSource())

    async def start_file(self, path: str) -> str:
        """启动音频文件转写会话（异步）。"""
        return await self._start(FileAudioSource(path))

    # ── 内部启动 ────────────────────────────────────────────────

    async def _start(self, source: AudioSource) -> str:
        """通用启动编排。

        步骤：
        1. 生成会话 ID
        2. 快照配置（workspace_dir, provider_name, provider_config）
        3. 创建 ASRProvider 实例
        4. 创建 TranscriptRecorder
        5. 执行 Provider 健康检查（失败仅记录警告，不阻塞启动）
        6. 启动音频源
        """
        session_id = _next_session_id()
        workspace_dir, provider_name, provider_config = self._snapshot_config()

        provider = create_provider(provider_name, provider_config)
        recorder = TranscriptRecorder(
            workspace_dir=workspace_dir,
            session_id=session_id,
            provider=provider_name,
        )

        # -- 健康检查（非致命） --
        try:
            await provider.check_health()
        except Exception as exc:
            logger.warning("ASR provider health check failed: %s", exc)

        # -- 启动音频源 --
        try:
            await source.start()
        except Exception as exc:
            raise ASRError(f"Failed to start audio source: {exc}") from exc

        self._source = source
        self._provider = provider
        self._recorder = recorder
        self._session_id = session_id
        self._running = True

        logger.info("ASR session started: %s (provider=%s)", session_id, provider_name)
        return session_id

    # ── 配置快照 ────────────────────────────────────────────────

    @staticmethod
    def _snapshot_config() -> tuple[str, str, dict]:
        """读取 aion.json 快照配置，失败时优雅降级。

        Returns:
            ``(workspace_dir, provider_name, provider_config)`` 三元组。
            降级时 ``workspace_dir`` 为 ``~/.aion/recordings``，
            ``provider_name`` 为 ``"aliyun"``，
            ``provider_config`` 为空字典。
        """
        try:
            config = load_config()
        except (FileNotFoundError, ValueError) as exc:
            logger.debug("Config load failed, using defaults: %s", exc)
            config = None

        if config is not None and config.asr is not None:
            provider_name = config.asr.provider.value
            provider_config = getattr(config.asr, provider_name, {})
            try:
                workspace_dir = str(resolve_workspace_dir(config=config))
            except ValueError:
                workspace_dir = str(AION_HOME / "recordings")
        else:
            provider_name = "aliyun"
            provider_config = {}
            workspace_dir = str(AION_HOME / "recordings")

        return workspace_dir, provider_name, provider_config

    # ── 转写循环 ────────────────────────────────────────────────

    async def run(self) -> AsyncIterator[str]:
        """运行转写循环。

        将音频源数据同时：
        - 写入原始 PCM 文件（通过 ``TranscriptRecorder.record_raw_audio``）
        - 送入 ``Provider.transcribe_stream`` 进行识别

        识别结果写入 ``TranscriptRecorder``（自动过滤中间结果与空文本）。

        前台模式（``foreground=True``）下 yield 格式化文字 ``[mm:ss] text``，
        仅对 ``is_final=True`` 且 ``text`` 非空的结果输出。

        Yields:
            前台模式下每句最终识别结果的时间戳 + 文字。

        Raises:
            ASRError: 未调用 ``start_*()`` 方法直接调用 ``run()``。
        """
        if not self._running or self._source is None or self._provider is None:
            raise ASRError("call start_*() before run()")

        recorder = self._recorder
        provider = self._provider
        source = self._source

        # ---- tee：同一条音频流同时写入磁盘 + 送入 provider ----
        async def _tee_audio() -> AsyncIterator[bytes]:
            async for chunk in source.read_chunks():
                if recorder:
                    recorder.record_raw_audio(chunk)
                yield chunk

        async for result in provider.transcribe_stream(_tee_audio()):
            if recorder:
                recorder.record_sentence(result)

            if self._foreground and result.is_final and result.text:
                minutes = result.begin_time // 60000
                seconds = (result.begin_time // 1000) % 60
                yield f"[{minutes:02d}:{seconds:02d}] {result.text}"

    # ── 停止 ────────────────────────────────────────────────────

    async def stop(self) -> None:
        """停止当前 ASR 会话。

        多次调用以及未启动时调用均安全（不抛异常）。
        依次执行：
        1. 标记 ``_running = False``
        2. 停止音频源
        3. Finalize TranscriptRecorder（关闭 JSON、生成 txt）
        4. 清空内部引用
        """
        if not self._running:
            return
        self._running = False
        await self._cleanup()

    async def _cleanup(self) -> None:
        """释放所有资源。"""
        if self._source is not None:
            try:
                await self._source.stop()
            except Exception as exc:
                logger.warning("Error stopping audio source: %s", exc)
            self._source = None

        if self._recorder is not None:
            try:
                self._recorder.finalize()
            except Exception as exc:
                logger.warning("Error finalizing recorder: %s", exc)
            self._recorder = None

        self._provider = None
        self._session_id = ""
