"""Config 子模型（embedding 等）"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EmbeddingProvider(str, Enum):
    """支持的 Embedding Provider 枚举。"""

    OPENAI = "openai"
    OLLAMA = "ollama"


class EmbeddingConfig(BaseModel):
    """Embedding 模型多 Provider 配置。"""

    provider: EmbeddingProvider = Field(default=EmbeddingProvider.OLLAMA)

    openai: dict[str, Any] = Field(
        default_factory=lambda: {
            "api_key": "",
            "model": "text-embedding-3-small",
        }
    )
    ollama: dict[str, Any] = Field(
        default_factory=lambda: {
            "model": "bge-m3",
            "base_url": "http://localhost:11434",
        }
    )


class ASRProvider(str, Enum):
    """支持的 ASR 服务商 / 引擎枚举。"""

    ALIYUN = "aliyun"
    BAIDU = "baidu"
    MACOS = "macos"
    WHISPER = "whisper"


class ASRConfig(BaseModel):
    """ASR 语音识别全局配置。"""

    provider: ASRProvider = Field(default=ASRProvider.ALIYUN)
    aliyun: dict[str, Any] = Field(
        default_factory=lambda: {
            "app_key": "",
            "access_key_id": "",
            "access_key_secret": "",
            "region": "cn-shanghai",
            "format": "pcm",
            "sample_rate": 16000,
        }
    )
    baidu: dict[str, Any] = Field(
        default_factory=lambda: {
            "app_id": "",
            "api_key": "",
            "secret_key": "",
        }
    )
    macos: dict[str, Any] = Field(
        default_factory=lambda: {
            "locale": "zh-CN",
            "require_authorized": False,
        }
    )
    whisper: dict[str, Any] = Field(
        default_factory=lambda: {
            "model_size": "base",
            "language": "zh",
            "device": "auto",
            "compute_type": "auto",
        }
    )
