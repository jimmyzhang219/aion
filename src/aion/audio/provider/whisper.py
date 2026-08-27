"""Whisper 本地 ASR 提供者。——使用 faster-whisper（CTranslate2），MPS 硬件加速。

依赖: faster-whisper, webrtcvad
模型: base（首次使用自动从 HuggingFace 下载，~300MB）
"""

from __future__ import annotations

import asyncio
import logging
import struct
from typing import Any, AsyncIterator

import numpy as np

from . import ASRProvider, FileTranscript, TranscriptChunk

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16000
_VAD_FRAME_MS = 30
_VAD_FRAME_BYTES = int(_SAMPLE_RATE * _VAD_FRAME_MS / 1000) * 2  # 960
_SILENCE_FRAMES = 500 // _VAD_FRAME_MS  # 500ms ≈ 17 frames
_MAX_BUF_SECONDS = 10  # 缓冲区超过此秒数强制转写
_MIN_INTERVAL = 1.5  # 两次转写最短间隔（防重复）


class WhisperProvider(ASRProvider):
    """Whisper 本地语音识别提供者 —— 使用 faster-whisper 引擎。

    使用 WebRTC VAD 检测句末停顿，每句话独立转写，
    实现近实时延迟（~1-2s/句）。
    """

    name = "whisper"

    WHISPER_MODELS = {
        "tiny": "tiny",
        "base": "base",
        "small": "small",
        "medium": "medium",
        "large-v3": "large-v3",
    }

    def __init__(self, config: dict[str, Any]) -> None:
        self._model_size = config.get("model_size", "base")
        self._device = config.get("device", "auto")
        self._compute_type = config.get("compute_type", "auto")
        self._language = config.get("language", "zh")
        self._beam_size = config.get("beam_size", 5)

        if self._model_size not in self.WHISPER_MODELS:
            raise ValueError(f"不支持的模型大小: {self._model_size}，可选: {list(self.WHISPER_MODELS)}")

        self._model = None
        self._vad = None

    def _get_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel  # type: ignore[import-untyped]

            logger.info(
                "正在加载 Whisper 模型: %s (device=%s, compute=%s)",
                self._model_size,
                self._device,
                self._compute_type,
            )
            logger.info("首次加载会自动从 HuggingFace 下载 ~%s，请稍候...", self._model_size)
            self._model = WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=self._compute_type,
                cpu_threads=4,
                num_workers=1,
            )
            logger.info("Whisper 模型加载完成")
        return self._model

    def _get_vad(self):
        if self._vad is None:
            import webrtcvad  # type: ignore[import-untyped]

            self._vad = webrtcvad.Vad(0)  # 最敏感
        return self._vad

    @staticmethod
    def _pcm_to_float(pcm: bytes) -> np.ndarray:
        count = len(pcm) // 2
        samples = struct.unpack(f"<{count}h", pcm[: count * 2])
        return np.array(samples, dtype=np.float32) / 32768.0

    # ── 实时流式识别 ────────────────────────────────────────

    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
    ) -> AsyncIterator[TranscriptChunk]:
        """VAD 驱动流式识别。

        策略：
        - 30ms 帧逐帧做 VAD
        - 检测到句末停顿（~500ms 静音）→ 转写
        - 连续说话超过 10 秒 → 强制转写
        - 缓冲区保留在内部，不受切分影响
        """
        model = self._get_model()
        vad = self._get_vad()
        loop = asyncio.get_running_loop()
        last_transcribe = 0.0

        buf = bytearray()
        silence_frames = 0
        has_speech = False

        async def _transcribe(data: bytes) -> list[TranscriptChunk]:
            if len(data) < _SAMPLE_RATE * 0.3:
                return []
            audio = await loop.run_in_executor(None, self._pcm_to_float, data)

            def _run():
                segs, _ = model.transcribe(
                    audio,
                    beam_size=self._beam_size,
                    language=self._language,
                    word_timestamps=True,
                    condition_on_previous_text=False,
                    no_speech_threshold=0.6,
                )
                return list(segs)

            try:
                future = loop.run_in_executor(None, _run)
                raw = await asyncio.wait_for(future, timeout=30)
            except asyncio.TimeoutError:
                logger.warning("Whisper 转写超时")
                return []
            except asyncio.CancelledError:
                future.cancel()
                raise

            chunks = []
            for seg in raw:
                text = seg.text.strip() if seg.text else ""
                if text:
                    chunks.append(
                        TranscriptChunk(
                            text=text,
                            is_final=True,
                            begin_time=int(seg.start * 1000),
                            end_time=int(seg.end * 1000),
                            confidence=getattr(seg, "avg_logprob", 0.0),
                        )
                    )
            return chunks

        try:
            async for chunk in audio_stream:
                if (t := asyncio.current_task()) and t.cancelled():
                    break
                if len(chunk) < 2:
                    continue

                buf.extend(chunk)
                buf_secs = len(buf) / (_SAMPLE_RATE * 2)

                # 逐帧 VAD（用临时切片，不修改 buf）
                pos = 0
                while pos + _VAD_FRAME_BYTES <= len(buf):
                    frame = bytes(buf[pos : pos + _VAD_FRAME_BYTES])
                    pos += _VAD_FRAME_BYTES

                    is_speech = vad.is_speech(frame, _SAMPLE_RATE)

                    if is_speech:
                        silence_frames = 0
                        has_speech = True
                    else:
                        silence_frames += 1

                # 触发转写（句末停顿 / 长度超限）
                now = loop.time()
                should_run = (
                    has_speech
                    and (now - last_transcribe > _MIN_INTERVAL)
                    and ((silence_frames >= _SILENCE_FRAMES) or (buf_secs >= _MAX_BUF_SECONDS))
                )
                if should_run:
                    data = bytes(buf)
                    buf.clear()
                    silence_frames = 0
                    has_speech = False
                    last_transcribe = now
                    for seg in await _transcribe(data):
                        yield seg

            # 流结束：剩余数据
            if buf:
                for seg in await _transcribe(bytes(buf)):
                    yield seg

        except asyncio.CancelledError:
            logger.info("Whisper 转写已取消")
            raise
        except Exception:
            logger.exception("Whisper 转写失败")
            raise

    # ── 录音文件识别 ────────────────────────────────────────

    async def transcribe_file(self, audio_path: str) -> FileTranscript:
        model = self._get_model()
        loop = asyncio.get_running_loop()

        def _run() -> tuple[list[TranscriptChunk], float]:
            segments, info = model.transcribe(
                audio_path,
                beam_size=self._beam_size,
                language=self._language,
                word_timestamps=True,
                vad_filter=True,
            )
            sentences = [
                TranscriptChunk(
                    text=seg.text.strip(),
                    is_final=True,
                    begin_time=int(seg.start * 1000),
                    end_time=int(seg.end * 1000),
                    confidence=getattr(seg, "avg_logprob", 0.0),
                )
                for seg in segments
                if seg.text.strip()
            ]
            return sentences, info.duration

        sentences, duration = await loop.run_in_executor(None, _run)
        return FileTranscript(sentences=sentences, duration_secs=duration)

    # ── 健康检查 ────────────────────────────────────────────

    async def check_health(self) -> bool:
        try:
            model = await asyncio.to_thread(self._get_model)
            return model is not None
        except Exception as e:
            logger.warning("Whisper health check failed: %s", e)
            return False
