"""macOS 原生 ASR 提供者。——使用 SFSpeechRecognizer（Apple Neural Engine）。

依赖: pyobjc (macOS only)
"""

from __future__ import annotations

import asyncio
import array
import logging
from typing import Any, AsyncIterator

from . import ASRProvider, FileTranscript, TranscriptChunk

logger = logging.getLogger(__name__)

# macOS 权限状态
_NOT_DETERMINED = 0
_AUTHORIZED = 1
_DENIED = 2
_RESTRICTED = 3

_AUTH_LABELS = {
    _NOT_DETERMINED: "未确定（需要用户授权）",
    _AUTHORIZED: "已授权",
    _DENIED: "已拒绝",
    _RESTRICTED: "系统限制（需在系统偏好设置中开启）",
}


class MacOSProvider(ASRProvider):
    """macOS 原生语音识别提供者 —— 使用 SFSpeechRecognizer 在 Apple Silicon
    Neural Engine 上离线运行，零网络延迟。"""

    name = "macos"

    def __init__(self, config: dict[str, Any]) -> None:
        self._locale = config.get("locale", "zh-CN")
        self._require_authorized = config.get("require_authorized", False)  # 是否要求权限状态为已授权

    # ── 权限管理 ────────────────────────────────────────────

    @staticmethod
    def _get_imports():
        """惰性导入 macOS 框架。"""
        import AVFoundation  # type: ignore[import-untyped]  # noqa: F401
        import Speech  # type: ignore[import-untyped]  # noqa: F401
        from Cocoa import NSLocale  # type: ignore[import-untyped]  # noqa: F401

        return Speech, AVFoundation, NSLocale

    @staticmethod
    def _get_auth_status() -> int:
        """获取当前语音识别权限状态。"""
        Speech, _, _ = MacOSProvider._get_imports()
        return Speech.SFSpeechRecognizer.authorizationStatus()

    @staticmethod
    def _auth_label(status: int) -> str:
        return _AUTH_LABELS.get(status, f"未知({status})")

    @classmethod
    def _request_authorization(cls) -> tuple[bool, int]:
        """请求语音识别权限。

        Returns:
            (是否授权, 当前状态码)
        """

        Speech, _, _ = cls._get_imports()

        status = cls._get_auth_status()
        if status == _AUTHORIZED:
            return True, status
        if status == _RESTRICTED:
            return False, status

        # 弹出系统授权对话框
        from threading import Event

        result = []
        event = Event()

        def _handler(s: int) -> None:
            result.append(s)
            event.set()

        Speech.SFSpeechRecognizer.requestAuthorization_(_handler)
        event.wait(timeout=30)

        if result:
            new_status = result[0]
            return new_status == _AUTHORIZED, new_status

        return False, cls._get_auth_status()

    # ── 实时流式识别 ────────────────────────────────────────

    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
    ) -> AsyncIterator[TranscriptChunk]:
        """通过 SFSpeechRecognizer 实时语音识别。

        将 audio_stream 中的 PCM int16 数据转换为 AVAudioPCMBuffer，
        喂给 SFSpeechAudioBufferRecognitionRequest，通过回调队列桥接
        到 async 生成器。
        """
        Speech, AVFoundation, NSLocale = self._get_imports()

        status = self._get_auth_status()
        if status in (_NOT_DETERMINED, _RESTRICTED):
            ok, status = self._request_authorization()
            if ok:
                logger.info("语音识别已授权")
        if status == _DENIED:
            raise RuntimeError("语音识别权限被拒绝。请在 系统偏好设置 → 隐私与安全性 → 语音识别 中启用。")
        if status == _RESTRICTED:
            raise RuntimeError("语音识别权限受系统限制。请在 系统偏好设置 → 隐私与安全性 → 语音识别 中启用。")

        locale = NSLocale.localeWithLocaleIdentifier_(self._locale)
        recognizer = Speech.SFSpeechRecognizer.alloc().initWithLocale_(locale)
        if not recognizer or not recognizer.isAvailable():
            raise RuntimeError(f"SFSpeechRecognizer 不可用（locale={self._locale}）")

        request = Speech.SFSpeechAudioBufferRecognitionRequest.alloc().init()
        request.setShouldReportPartialResults_(True)

        # AVAudioFormat: Float32, 16kHz, mono, non-interleaved
        fmt = AVFoundation.AVAudioFormat.alloc().initWithCommonFormat_sampleRate_channels_interleaved_(
            AVFoundation.AVAudioPCMFormatFloat32,
            16000.0,
            1,
            False,
        )

        queue: asyncio.Queue[TranscriptChunk | None] = asyncio.Queue()
        recognition_error: list[Exception | None] = [None]

        # ── 回调：SFSpeechRecognizer 结果 → asyncio.Queue ──
        def _on_result(result, error):
            if error:
                logger.error("SFSpeechRecognizer error: %s", error)
                recognition_error[0] = RuntimeError(str(error))
                queue.put_nowait(None)
                return
            if result is None:
                return

            if result.isFinal():
                for seg in result.bestTranscription().segments():
                    queue.put_nowait(
                        TranscriptChunk(
                            text=str(seg.substring() or ""),
                            is_final=True,
                            begin_time=int(seg.timestamp() * 1000),
                            end_time=int((seg.timestamp() + seg.duration()) * 1000),
                            confidence=seg.confidence(),
                        )
                    )
            else:
                text = str(result.bestTranscription().formattedString() or "")
                if text.strip():
                    queue.put_nowait(
                        TranscriptChunk(
                            text=text,
                            is_final=False,
                            begin_time=0,
                            end_time=0,
                            confidence=0.0,
                        )
                    )

        task = recognizer.recognitionTaskWithRequest_resultHandler_(request, _on_result)
        logger.info("SFSpeechRecognitionTask started: %s", task)

        # ── 发送任务：从 audio_stream 读取 PCM → AVAudioPCMBuffer ──
        async def _feed_audio() -> None:
            try:
                async for chunk in audio_stream:
                    if len(chunk) < 2:
                        continue
                    samples = array.array("h", chunk)
                    frame_len = len(samples)
                    if frame_len == 0:
                        continue

                    buf = AVFoundation.AVAudioPCMBuffer.alloc().initWithPCMFormat_frameCapacity_(fmt, frame_len)
                    buf._.frameLength = frame_len
                    chan = buf.floatChannelData()
                    if chan:
                        for i in range(frame_len):
                            chan[0][i] = samples[i] / 32768.0
                        request.appendAudioPCMBuffer_(buf)
            finally:
                request.endAudio()

        feed_task = asyncio.create_task(_feed_audio())

        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                yield chunk
                if recognition_error[0]:
                    raise recognition_error[0]
        finally:
            feed_task.cancel()
            try:
                await feed_task
            except asyncio.CancelledError:
                pass
            if task:
                task.cancel()

    # ── 录音文件识别 ────────────────────────────────────────

    async def transcribe_file(self, audio_path: str) -> FileTranscript:
        """通过 SFSpeechURLRecognitionRequest 识别录音文件。

        macOS 原生支持文件识别，这是区别于 Aliyun/Baidu 存根的优势。
        """
        Speech, _, NSLocale = self._get_imports()
        from Cocoa import NSURL  # type: ignore[import-untyped]

        status = self._get_auth_status()
        if status in (_NOT_DETERMINED, _RESTRICTED):
            ok, status = self._request_authorization()
        if status == _DENIED:
            raise RuntimeError("语音识别权限被拒绝。")
        if status == _RESTRICTED:
            raise RuntimeError("语音识别权限受系统限制。")

        locale = NSLocale.localeWithLocaleIdentifier_(self._locale)
        recognizer = Speech.SFSpeechRecognizer.alloc().initWithLocale_(locale)
        if not recognizer or not recognizer.isAvailable():
            raise RuntimeError(f"SFSpeechRecognizer 不可用（locale={self._locale}）")

        url = NSURL.fileURLWithPath_(audio_path)
        request = Speech.SFSpeechURLRecognitionRequest.alloc().initWithURL_(url)

        queue: asyncio.Queue[TranscriptChunk | None] = asyncio.Queue()
        recognition_error: list[Exception | None] = [None]

        def _on_result(result, error):
            if error:
                recognition_error[0] = RuntimeError(str(error))
                queue.put_nowait(None)
                return
            if result is None:
                return
            if result.isFinal():
                for seg in result.bestTranscription().segments():
                    queue.put_nowait(
                        TranscriptChunk(
                            text=str(seg.substring() or ""),
                            is_final=True,
                            begin_time=int(seg.timestamp() * 1000),
                            end_time=int((seg.timestamp() + seg.duration()) * 1000),
                            confidence=seg.confidence(),
                        )
                    )
                queue.put_nowait(None)

        recognizer.recognitionTaskWithRequest_resultHandler_(request, _on_result)

        sentences: list[TranscriptChunk] = []
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            sentences.append(chunk)

        if recognition_error[0]:
            raise recognition_error[0]

        return FileTranscript(sentences=sentences)

    # ── 健康检查 ────────────────────────────────────────────

    async def check_health(self) -> bool:
        status = self._get_auth_status()
        logger.debug("SFSpeechRecognizer auth status: %s", self._auth_label(status))

        # 未确定或受限时尝试请求权限
        if status in (_NOT_DETERMINED, _RESTRICTED):
            if not self._require_authorized:
                ok, status = self._request_authorization()
                if ok:
                    return True

        if status != _AUTHORIZED:
            logger.warning("SFSpeechRecognizer 未授权: %s", self._auth_label(status))
            return False

        locale_label = self._locale
        Speech, _, NSLocale = self._get_imports()
        locale = NSLocale.localeWithLocaleIdentifier_(locale_label)
        recognizer = Speech.SFSpeechRecognizer.alloc().initWithLocale_(locale)

        available = bool(recognizer and recognizer.isAvailable())
        if not available:
            logger.warning("SFSpeechRecognizer 不可用（locale=%s）", locale_label)
        return available
