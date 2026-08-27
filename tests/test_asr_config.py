"""ASR 配置模型测试"""

from aion.config.models import ASRProvider, ASRConfig


class TestASRProvider:
    def test_enum_values(self):
        assert ASRProvider.ALIYUN == "aliyun"
        assert ASRProvider.BAIDU == "baidu"


class TestASRConfig:
    def test_default_provider_is_aliyun(self):
        cfg = ASRConfig()
        assert cfg.provider == ASRProvider.ALIYUN

    def test_aliyun_defaults(self):
        cfg = ASRConfig()
        assert cfg.aliyun["sample_rate"] == 16000
        assert cfg.aliyun["format"] == "pcm"

    def test_baidu_defaults(self):
        cfg = ASRConfig()
        assert cfg.baidu["app_id"] == ""

    def test_custom_provider(self):
        cfg = ASRConfig(provider=ASRProvider.BAIDU)
        assert cfg.provider == ASRProvider.BAIDU
