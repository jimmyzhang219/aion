"""macOS 原生 ASR Provider 测试。"""

import sys

import pytest

from aion.audio.provider import create_provider

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="macOS native Speech framework is only available on macOS",
)

MINIMAL_CONFIG = {
    "locale": "zh-CN",
    "require_authorized": False,
}


class TestMacOSProvider:
    def test_create_macos(self):
        provider = create_provider("macos", MINIMAL_CONFIG)
        assert provider.name == "macos"
        assert provider._locale == "zh-CN"
        assert provider._require_authorized is False

    def test_auth_status(self):
        """验证可以读取权限状态，不应炸。"""
        from aion.audio.provider.macos import MacOSProvider

        status = MacOSProvider._get_auth_status()
        assert status in (0, 1, 2, 3)  # 任意状态值均合法

    def test_auth_label(self):
        """权限状态标签不应为空。"""
        from aion.audio.provider.macos import MacOSProvider

        for s in (0, 1, 2, 3):
            label = MacOSProvider._auth_label(s)
            assert label and len(label) > 0

    @pytest.mark.asyncio
    async def test_check_health(self):
        """check_health 不应抛出异常。"""
        provider = create_provider("macos", MINIMAL_CONFIG)
        result = await provider.check_health()
        # 结果取决于系统权限状态，可能是 True 或 False，但不应该抛异常
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_transcribe_file_raises_without_auth(self):
        """未授权时 transcribe_file 抛出 RuntimeError。"""
        from aion.audio.provider.macos import MacOSProvider

        status = MacOSProvider._get_auth_status()
        provider = create_provider("macos", MINIMAL_CONFIG)

        if status != 1:  # 未授权
            with pytest.raises(RuntimeError):
                await provider.transcribe_file("/dev/null")
        else:
            pytest.skip("已授权，跳过无权限测试")

    @pytest.mark.asyncio
    async def test_transcribe_stream_raises_without_auth(self):
        """未授权时 transcribe_stream 抛出 RuntimeError。"""
        from aion.audio.provider.macos import MacOSProvider

        status = MacOSProvider._get_auth_status()

        async def _empty_stream():
            yield b""

        provider = create_provider("macos", MINIMAL_CONFIG)

        if status != 1:  # 未授权
            with pytest.raises(RuntimeError):
                async for _ in provider.transcribe_stream(_empty_stream()):
                    pass
        else:
            pytest.skip("已授权，跳过无权限测试")
