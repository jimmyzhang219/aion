"""ASR Provider 抽象 + 工厂 + 百度云占位测试"""

import asyncio
from unittest.mock import patch

import pytest

from aion.audio.provider import (
    TranscriptChunk,
    FileTranscript,
    create_provider,
)


class TestTranscriptChunk:
    def test_create_chunk(self):
        c = TranscriptChunk(
            begin_time=0,
            end_time=2800,
            text="你好",
            is_final=True,
            confidence=0.95,
        )
        assert c.text == "你好"
        assert c.is_final
        assert c.confidence == 0.95


class TestFileTranscript:
    def test_create_file_transcript(self):
        ft = FileTranscript(sentences=[], duration_secs=120.0)
        assert ft.duration_secs == 120.0
        assert ft.sentences == []


class TestCreateProvider:
    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown ASR provider"):
            create_provider("unknown", {})

    def test_provider_name_case_insensitive(self):
        provider = create_provider("Baidu", {"app_id": "1", "api_key": "k"})
        assert provider.name == "baidu"

    def test_baidu_provider_methods_states(self):
        """验证实现后各方法的状态：check_health 优雅降级、transcribe_file 未实现、transcribe_stream 返回生成器。"""
        from unittest.mock import AsyncMock, patch

        mock_cm = AsyncMock()
        mock_cm.__aenter__.side_effect = Exception("connection refused")

        with patch("websockets.connect", return_value=mock_cm):
            provider = create_provider("baidu", {"app_id": "1", "api_key": "k"})
            assert provider.name == "baidu"

            # check_health 应优雅返回 False（无法连接时）
            assert asyncio.run(provider.check_health()) is False

        # transcribe_file 仍为 NotImplementedError
        with pytest.raises(NotImplementedError):
            asyncio.run(provider.transcribe_file("test.wav"))

        # transcribe_stream 是普通函数返回 async generator，不应抛出异常
        gen = provider.transcribe_stream(aiter_bytes())
        assert hasattr(gen, "__aiter__")


def aiter_bytes():
    """辅助函数：创建一个异步字节流迭代器（返回空 bytes）。"""
    return _aiter_bytes(b"")


async def _aiter_bytes(data: bytes):
    """辅助函数：从 bytes 创建异步迭代器。"""
    yield data
    return


class TestAliyunProvider:
    """阿里云 NLS Provider 测试。"""

    MINIMAL_CONFIG = {
        "app_key": "test-key",
        "access_key_id": "test-id",
        "access_key_secret": "test-secret",
    }

    def test_create_aliyun(self):
        provider = create_provider("aliyun", self.MINIMAL_CONFIG)
        assert provider.name == "aliyun"

    def test_check_health_mocked(self):
        with patch("nls.token.getToken", side_effect=Exception("no network")):
            provider = create_provider("aliyun", self.MINIMAL_CONFIG)
            result = asyncio.run(provider.check_health())
            assert result is False

    def test_check_health_success(self):
        with patch("nls.token.getToken", return_value="fake-token"):
            provider = create_provider("aliyun", self.MINIMAL_CONFIG)
            result = asyncio.run(provider.check_health())
            assert result is True

    @pytest.mark.asyncio
    async def test_transcribe_file_raises(self):
        provider = create_provider("aliyun", self.MINIMAL_CONFIG)
        with pytest.raises(NotImplementedError, match="录音文件识别待完善"):
            await provider.transcribe_file("test.wav")


class TestBaiduProvider:
    """百度云 ASR Provider 测试。"""

    MINIMAL_CONFIG = {
        "app_id": "1234567",
        "api_key": "test-api-key",
        "dev_pid": 15372,
        "cuid": "test-cuid",
    }

    def test_create_baidu(self):
        provider = create_provider("baidu", self.MINIMAL_CONFIG)
        assert provider.name == "baidu"
        assert provider._app_id == "1234567"
        assert provider._api_key == "test-api-key"
        assert provider._dev_pid == 15372
        assert provider._cuid == "test-cuid"

    def test_create_baidu_defaults(self):
        """验证带默认值创建（只传必要字段）。"""
        provider = create_provider("baidu", {"app_id": "1", "api_key": "k"})
        assert provider._dev_pid == 15372
        assert provider._cuid == "aion-asr-1"

    @pytest.mark.asyncio
    async def test_check_health_success(self):
        """模拟 WebSocket：START + FINISH 后连接正常关闭。"""
        import asyncio
        from unittest.mock import AsyncMock, patch

        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock()
        # 百度协议：START 后无确认帧，发 FINISH 后 recv 超时 = 鉴权通过
        mock_ws.recv = AsyncMock(side_effect=asyncio.TimeoutError)

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_ws

        with patch("websockets.connect", return_value=mock_cm):
            provider = create_provider("baidu", self.MINIMAL_CONFIG)
            result = await provider.check_health()
            assert result is True
        # 验证 START 帧已发送（send 的第一个调用）
        sent_start = mock_ws.send.call_args_list[0][0][0]
        assert "START" in sent_start

    @pytest.mark.asyncio
    async def test_check_health_failure(self):
        """服务端返回鉴权错误时健康检查失败。"""
        import json
        from unittest.mock import AsyncMock, patch

        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock()
        mock_ws.recv = AsyncMock(
            return_value=json.dumps(
                {
                    "err_no": -3004,
                    "err_msg": "asr authentication failed",
                    "type": "FIN_TEXT",
                }
            )
        )

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_ws

        with patch("websockets.connect", return_value=mock_cm):
            provider = create_provider("baidu", self.MINIMAL_CONFIG)
            result = await provider.check_health()
            assert result is False

    @pytest.mark.asyncio
    async def test_check_health_connection_error(self):
        """连接异常时健康检查优雅降级。"""
        from unittest.mock import AsyncMock, patch

        mock_cm = AsyncMock()
        mock_cm.__aenter__.side_effect = Exception("connection refused")

        with patch("websockets.connect", return_value=mock_cm):
            provider = create_provider("baidu", self.MINIMAL_CONFIG)
            result = await provider.check_health()
            assert result is False

    @pytest.mark.asyncio
    async def test_transcribe_file_raises(self):
        """录音文件识别暂未实现。"""
        provider = create_provider("baidu", self.MINIMAL_CONFIG)
        with pytest.raises(NotImplementedError, match="录音文件识别待完善"):
            await provider.transcribe_file("test.wav")

    @pytest.mark.asyncio
    async def test_transcribe_stream_yields_chunks(self):
        """模拟 WebSocket 返回 MID_TEXT 和 FIN_TEXT。"""
        import json
        from unittest.mock import AsyncMock, patch

        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock()

        # async for 遍历的后续结果（__anext__ 由 AsyncMock 内部处理）
        responses = [
            json.dumps({"err_no": 0, "err_msg": "OK", "type": "MID_TEXT", "result": "你好世"}),
            json.dumps(
                {
                    "err_no": 0,
                    "err_msg": "OK",
                    "type": "FIN_TEXT",
                    "result": "你好世界",
                    "start_time": 100,
                    "end_time": 500,
                }
            ),
        ]
        mock_ws.__aiter__.return_value = responses

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_ws

        with patch("websockets.connect", return_value=mock_cm):
            provider = create_provider("baidu", self.MINIMAL_CONFIG)
            results = []
            async for chunk in provider.transcribe_stream(_aiter_bytes(b"fake_audio")):
                results.append(chunk)

        assert len(results) == 2
        assert results[0].text == "你好世"
        assert results[0].is_final is False
        assert results[0].begin_time == 0
        assert results[0].end_time == 0
        assert results[0].confidence == 0.0

        assert results[1].text == "你好世界"
        assert results[1].is_final is True
        assert results[1].begin_time == 100
        assert results[1].end_time == 500

    @pytest.mark.asyncio
    async def test_transcribe_stream_skips_error_fintext(self):
        """带错误码的 FIN_TEXT 被跳过但记录警告。"""
        import json
        from unittest.mock import AsyncMock, patch

        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock()

        responses = [
            json.dumps({"err_no": -3004, "err_msg": "asr error", "type": "FIN_TEXT", "result": ""}),
        ]
        mock_ws.__aiter__.return_value = responses

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_ws

        with patch("websockets.connect", return_value=mock_cm):
            provider = create_provider("baidu", self.MINIMAL_CONFIG)
            results = []
            async for chunk in provider.transcribe_stream(_aiter_bytes(b"")):
                results.append(chunk)
        assert len(results) == 0  # 错误帧被跳过

    @pytest.mark.asyncio
    async def test_transcribe_stream_sends_audio_and_finish(self):
        """验证 PCM 数据和 FINISH 帧被发送。"""
        from unittest.mock import AsyncMock, patch

        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock()
        mock_ws.__aiter__.return_value = []

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_ws

        with patch("websockets.connect", return_value=mock_cm):
            provider = create_provider("baidu", self.MINIMAL_CONFIG)
            async for _ in provider.transcribe_stream(_aiter_bytes(b"\x00\x01\x02" * 100)):
                pass

        # 验证：发送了 PCM 数据（至少一次 binary send）+ FINISH
        binary_sends = [call for call in mock_ws.send.call_args_list if isinstance(call[0][0], bytes)]
        text_sends = [call for call in mock_ws.send.call_args_list if isinstance(call[0][0], str)]

        assert len(binary_sends) >= 1
        assert any("FINISH" in call[0][0] for call in text_sends)
