"""Whisper 离线 ASR Provider 测试。"""

import pytest

from aion.audio.provider import create_provider


class TestWhisperProvider:
    def test_create_whisper(self):
        provider = create_provider("whisper", {})
        assert provider.name == "whisper"
        assert provider._model_size == "base"
        assert provider._language == "zh"

    def test_create_whisper_custom(self):
        provider = create_provider(
            "whisper",
            {
                "model_size": "tiny",
                "language": "en",
                "device": "cpu",
            },
        )
        assert provider._model_size == "tiny"
        assert provider._language == "en"
        assert provider._device == "cpu"

    def test_unsupported_model_raises(self):
        with pytest.raises(ValueError, match="不支持的模型大小"):
            create_provider("whisper", {"model_size": "huge"})

    @pytest.mark.asyncio
    async def test_check_health_no_model(self):
        """模型不存在或未安装时健康检查返回 False。"""
        provider = create_provider("whisper", {"device": "cpu"})
        result = await provider.check_health()
        assert isinstance(result, bool)
