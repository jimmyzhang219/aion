"""tests/test_audio_provider_init.py"""

from __future__ import annotations

import sys
import pytest


def test_create_provider_aliyun():
    """阿里云 provider 在所有平台可用。"""
    from aion.audio.provider import create_provider

    provider = create_provider(
        "aliyun",
        {
            "app_key": "test",
            "access_key_id": "test",
            "access_key_secret": "test",
        },
    )
    assert provider is not None


def test_create_provider_macos_only_on_darwin():
    """macos provider 只在 macOS 上注册。"""
    from aion.audio.provider import create_provider

    if sys.platform == "darwin":
        provider = create_provider("macos", {})
        assert provider is not None
    else:
        with pytest.raises(ValueError, match="Unknown ASR provider"):
            create_provider("macos", {})
