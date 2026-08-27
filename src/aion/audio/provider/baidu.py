"""百度云实时语音识别 ASR 提供者。——使用百度 WebSocket API。

协议文档: https://ai.baidu.com/ai-doc/SPEECH/jlbxejt2i
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator
from uuid import uuid4

from . import ASRProvider, FileTranscript, TranscriptChunk

logger = logging.getLogger(__name__)


class BaiduProvider(ASRProvider):
    """百度云实时语音识别 ASR 提供者。"""

    name = "baidu"

    def __init__(self, config: dict[str, Any]) -> None:
        self._app_id = config["app_id"]
        self._api_key = config.get("api_key", "")
        self._dev_pid = config.get("dev_pid", 15372)
        self._cuid = config.get("cuid", "aion-asr-1")

    # ── 构建辅助 ────────────────────────────────────────────

    @staticmethod
    def _build_ws_url() -> str:
        """生成带唯一 sn 的 WebSocket 连接 URL。"""
        sn = str(uuid4()).replace("-", "")
        return f"wss://vop.baidu.com/realtime_asr?sn={sn}"

    def _build_start_frame(self) -> dict[str, Any]:
        """构建 START 握手帧字典。"""
        return {
            "type": "START",
            "data": {
                "appid": int(self._app_id),
                "appkey": self._api_key,
                "dev_pid": self._dev_pid,
                "cuid": self._cuid,
                "format": "pcm",
                "sample": 16000,
            },
        }

    # ── 实时流式识别 ────────────────────────────────────────

    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
    ) -> AsyncIterator[TranscriptChunk]:
        """通过百度 WebSocket 实时语音识别。"""
        import websockets  # type: ignore[import-untyped]  # 惰性导入

        queue: asyncio.Queue[TranscriptChunk | None] = asyncio.Queue()

        task = asyncio.create_task(
            self._run_session(websockets, audio_stream, queue),
        )

        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                yield chunk
        finally:
            if not task.done():
                task.cancel()

    async def _run_session(
        self,
        websockets: Any,
        audio_stream: AsyncIterator[bytes],
        queue: asyncio.Queue[TranscriptChunk | None],
    ) -> None:
        """管理整个 WebSocket 会话生命周期。

        百度协议特点：START 后不会返回确认帧，服务端直接等音频数据。
        因此 START 后立即启动 sender（发音频）和 receiver（收结果）双任务。
        """
        try:
            async with websockets.connect(self._build_ws_url()) as ws:
                # ── START 握手：不等待确认，立即发送音频 ──
                await ws.send(json.dumps(self._build_start_frame()))

                # ── 发送任务：从 audio_stream 读取 PCM 数据并发送 ──
                async def sender() -> None:
                    try:
                        async for chunk in audio_stream:
                            await ws.send(chunk)
                    finally:
                        await ws.send(json.dumps({"type": "FINISH"}))

                # ── 接收任务：从 WebSocket 读取结果 ──
                async def receiver() -> None:
                    async for msg in ws:
                        data = json.loads(msg)
                        t = data["type"]
                        err_no = data.get("err_no", 0)

                        if t == "FIN_TEXT":
                            if err_no == 0:
                                queue.put_nowait(
                                    TranscriptChunk(
                                        text=data.get("result", ""),
                                        is_final=True,
                                        begin_time=data.get("start_time", 0),
                                        end_time=data.get("end_time", 0),
                                        confidence=0.0,
                                    )
                                )
                            else:
                                logger.warning(
                                    "Baidu ASR FIN_TEXT error: err_no=%d err_msg=%s",
                                    err_no,
                                    data.get("err_msg", ""),
                                )
                        elif t == "MID_TEXT" and err_no == 0:
                            queue.put_nowait(
                                TranscriptChunk(
                                    text=data.get("result", ""),
                                    is_final=False,
                                    begin_time=0,
                                    end_time=0,
                                    confidence=0.0,
                                )
                            )
                        # HEARTBEAT → 跳过

                send_task = asyncio.create_task(sender())
                recv_task = asyncio.create_task(receiver())

                try:
                    await asyncio.gather(send_task, recv_task)
                except BaseException:
                    send_task.cancel()
                    recv_task.cancel()
                    raise
        except Exception:
            logger.exception("Baidu ASR session failed")
            raise
        finally:
            queue.put_nowait(None)

    # ── 录音文件识别 ────────────────────────────────────────

    async def transcribe_file(self, audio_path: str) -> FileTranscript:
        """录音文件识别。当前为骨架实现。"""
        raise NotImplementedError("录音文件识别待完善")

    # ── 健康检查 ────────────────────────────────────────────

    async def check_health(self) -> bool:
        import websockets  # type: ignore[import-untyped]  # 惰性导入

        async def _check() -> bool:
            async with websockets.connect(self._build_ws_url()) as ws:
                await ws.send(json.dumps(self._build_start_frame()))
                # 百度不返回 START 确认帧，发 FINISH 以触发服务端响应
                await ws.send(json.dumps({"type": "FINISH"}))
                try:
                    resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    err_no = resp.get("err_no", 0)
                    err_msg = resp.get("err_msg", "")
                    # -3004 且含 "auth" 表示鉴权失败，其余错误（如无音频数据）说明连接正常
                    if err_no == -3004 and "auth" in err_msg.lower():
                        return False
                    return True
                except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
                    # 服务端正常关闭或超时 = 鉴权通过
                    return True

        try:
            return await asyncio.wait_for(_check(), timeout=5)
        except Exception as e:
            logger.warning("Baidu ASR health check failed: %s", e)
            return False
