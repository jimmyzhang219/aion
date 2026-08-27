"""阿里云 NLS（智能语音交互）ASR 提供者实现。

实时语音识别: NlsSpeechTranscriber (WebSocket)
录音文件识别: 阿里云录音文件识别 REST API

依赖: alibaba-nls-python-sdk
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
from collections.abc import Iterator
from typing import AsyncIterator

from . import ASRProvider, FileTranscript, TranscriptChunk

logger = logging.getLogger(__name__)


class AliyunProvider(ASRProvider):
    """阿里云 NLS ASR 提供者。"""

    name = "aliyun"

    def __init__(self, config: dict) -> None:
        self._config = config
        self._app_key = config["app_key"]
        self._access_key_id = config["access_key_id"]
        self._access_key_secret = config["access_key_secret"]
        self._region = config.get("region", "cn-shanghai")
        self._format = config.get("format", "pcm")
        self._sample_rate = config.get("sample_rate", 16000)

    # ── Token 管理 ──────────────────────────────────────────

    def _get_token(self) -> str:
        """获取阿里云 NLS 临时 Token。"""
        from nls.token import getToken  # type: ignore[import-untyped]

        return getToken(self._access_key_id, self._access_key_secret)

    # ── 实时流式识别 ────────────────────────────────────────

    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
    ) -> AsyncIterator[TranscriptChunk]:
        """通过阿里云 NLS WebSocket 实时语音识别。"""
        loop = asyncio.get_running_loop()
        result_queue: asyncio.Queue[TranscriptChunk | None] = asyncio.Queue()

        token = await loop.run_in_executor(None, self._get_token)

        def _run():
            self._run_transcriber(audio_stream, result_queue, token, loop)

        future = loop.run_in_executor(None, _run)

        def _on_executor_error(fut: concurrent.futures.Future) -> None:
            try:
                fut.result()
            except (asyncio.CancelledError, concurrent.futures.CancelledError):
                pass  # Ctrl+C 中断，预期行为
            except Exception as exc:
                logger.error("ASR executor thread failed: %s", exc)
                result_queue.put_nowait(None)

        future.add_done_callback(_on_executor_error)  # type: ignore[arg-type]

        while True:
            chunk = await result_queue.get()
            if chunk is None:
                break
            yield chunk

    def _run_transcriber(
        self,
        audio_stream: AsyncIterator[bytes],
        result_queue: asyncio.Queue[TranscriptChunk | None],
        token: str,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """在同步线程中运行 NlsSpeechTranscriber。"""
        import nls  # type: ignore[import-untyped]  # 惰性导入

        audio_iter = self._consume_async_iter(audio_stream, loop)

        sr = nls.NlsSpeechTranscriber(
            url=f"wss://nls-gateway-{self._region}.aliyuncs.com/ws/v1",
            token=token,
            appkey=self._app_key,
            on_sentence_begin=lambda *a, **kw: None,
            on_sentence_end=lambda result, *a, **kw: self._on_sentence_end(result, result_queue),
            on_result_changed=lambda result, *a, **kw: self._on_result_changed(result, result_queue),
            on_completed=lambda *a, **kw: result_queue.put_nowait(None),
            on_error=lambda *a, **kw: self._on_error(a, kw, result_queue),
            on_close=lambda *a, **kw: None,
        )

        sr.start(
            aformat=self._format,
            sample_rate=self._sample_rate,
            enable_intermediate_result=True,
            enable_punctuation_prediction=True,
            enable_inverse_text_normalization=True,
            ex={
                "enable_semantic_sentence_detection": True,
                "enable_words": True,
                "max_sentence_silence": 2000,
                "speech_noise_threshold": 0.3,
                "disfluency": True,
            },
        )

        logger.info("sr.start() completed, starting audio loop")
        chunk_count = 0
        interrupted = False
        try:
            for chunk in audio_iter:
                chunk_count += 1
                sr.send_audio(chunk)
        except BaseException:
            interrupted = True
            raise
        finally:
            logger.info(
                "Transcriber finished: %d chunks (%d ms), interrupted=%s",
                chunk_count,
                chunk_count * 100,
                interrupted,
            )
            if interrupted:
                sr.shutdown()  # 直接断连，不等服务器响应
            else:
                sr.stop()
                sr.shutdown()

    @staticmethod
    def _consume_async_iter(
        audio_stream: AsyncIterator[bytes],
        loop: asyncio.AbstractEventLoop,
    ) -> Iterator[bytes]:
        """将异步迭代器转为同步阻塞迭代器。"""

        async def _anext() -> bytes:
            """用 proper coroutine 包装 __anext__()，确保 run_coroutine_threadsafe 可调度。"""
            try:
                return await audio_stream.__anext__()
            except StopAsyncIteration:
                raise

        count = 0
        while True:
            try:
                future: concurrent.futures.Future[bytes] = asyncio.run_coroutine_threadsafe(
                    _anext(),
                    loop,
                )
                chunk = future.result(timeout=2.0)
                count += 1
                if count % 10 == 0:
                    logger.debug("Consumed %d audio chunks", count)
                yield chunk
            except concurrent.futures.TimeoutError:
                logger.warning("No audio chunk for 2s, stopping")
                break
            except StopAsyncIteration:
                logger.debug("Audio stream exhausted after %d chunks", count)
                break

    @staticmethod
    def _parse_result(result: dict | str) -> dict | None:
        """解析阿里云 SDK 回调结果。

        SDK 的回调可能传 JSON 字符串或 dict，统一解析后返回 payload dict。
        """
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                return None
        if not isinstance(result, dict):
            return None
        payload = result.get("payload")
        return payload if isinstance(payload, dict) else None

    def _on_sentence_end(
        self,
        result: dict | str,
        queue: asyncio.Queue[TranscriptChunk | None],
    ) -> None:
        payload = self._parse_result(result)
        if payload is None:
            return
        chunk = TranscriptChunk(
            begin_time=int(payload.get("begin_time", 0)),
            end_time=int(payload.get("end_time", payload.get("time", 0))),
            text=payload.get("result", ""),
            is_final=True,
            confidence=float(payload.get("confidence", 1.0)),
        )
        if chunk.text.strip():
            logger.info("SentenceEnd: %s", chunk.text[:50])
            queue.put_nowait(chunk)

    def _on_result_changed(
        self,
        result: dict | str,
        queue: asyncio.Queue[TranscriptChunk | None],
    ) -> None:
        payload = self._parse_result(result)
        if payload is None:
            return
        chunk = TranscriptChunk(
            begin_time=int(payload.get("time", 0)),
            end_time=int(payload.get("time", 0)),
            text=payload.get("result", ""),
            is_final=False,
            confidence=float(payload.get("confidence", 0.0)),
        )
        if chunk.text.strip():
            queue.put_nowait(chunk)

    def _on_error(
        self,
        args: tuple,
        kwargs: dict,
        queue: asyncio.Queue[TranscriptChunk | None],
    ) -> None:
        logger.error("Aliyun NLS error: args=%s kwargs=%s", args, kwargs)
        queue.put_nowait(None)

    # ── 录音文件识别 ────────────────────────────────────────

    async def transcribe_file(self, audio_path: str) -> FileTranscript:
        """阿里云录音文件识别。当前为骨架实现。"""
        raise NotImplementedError("录音文件识别待完善")

    # ── 健康检查 ────────────────────────────────────────────

    async def check_health(self) -> bool:
        loop = asyncio.get_running_loop()
        try:
            token = await loop.run_in_executor(None, self._get_token)
            return bool(token)
        except Exception as e:
            logger.warning("Aliyun NLS health check failed: %s", e)
            return False
