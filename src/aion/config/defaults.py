"""配置生成器 - 多工作空间配置 v5

配置结构：
- models: 直接是模型字典
- workspaces.scopes: 数组格式，每个元素是一个工作空间（key 是工作空间名字）
- workspaces.current: 字符串，表示当前工作空间名字
- agents: leader 是字符串引用 + 具体 agent 配置
- log_level: 全局日志级别（默认 info）

{
    "models": { "deepseek": {...} },
    "workspaces": {
        "scopes": [
            {
                "default": {
                    "agents": {
                        "leader": "main",
                        "main": { "provider": "deepseek", "fallback": [] }
                    },
                    ...
                }
            }
        ],
        "current": "default"
    },
    "log_level": "info"
}
"""

import json
from collections import OrderedDict
from typing import Any

# 默认不启用任何 MCP 服务器
# 如需 MCP 支持，通过 aion mcp add 命令手动添加
DEFAULT_MCP_SERVERS: dict[str, Any] = {}

# Known provider defaults (baseUrl, context_window, max_tokens).
# Used by CLI setup/auto-fill and as a fallback when user config omits baseUrl.
PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "deepseek": {
        "baseUrl": "https://api.deepseek.com",
        "context_window": 1000000,
        "max_tokens": 384000,
        "request_timeout": 300,
    },
    "openai": {
        "baseUrl": "https://api.openai.com",
        "context_window": 128000,
        "max_tokens": 8192,
    },
    "alicloud": {
        "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "context_window": 1000000,
        "max_tokens": 65536,
        "request_timeout": 300,
    },
    "minimax": {
        "baseUrl": "https://api.minimax.chat/v1",
        "context_window": 128000,
        "max_tokens": 8192,
    },
    "moonshot": {
        "baseUrl": "https://api.moonshot.cn/v1",
        "context_window": 128000,
        "max_tokens": 8192,
    },
    "glm": {
        "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
        "context_window": 128000,
        "max_tokens": 8192,
    },
}


def _get_default_models() -> dict:
    """构建默认 models 段（空字典，由 aion setup 引导配置）。

    Returns:
        空 models 配置字典。
    """
    return {}


def _get_default_workspace(name: str = "default") -> dict:
    """构建单个工作空间的默认配置块。

    Args:
        name: 工作空间名称（当前仅用于文档语义，配置内容相同）。

    Returns:
        工作空间配置字典（agents/compaction/pruning/mcpServers 等）。
        memory 为顶层配置，不在工作空间层级重复。
    """
    return {
        "agents": {"leader": "main", "main": {"provider": "", "fallback": []}},
        "compaction": {
            "enabled": True,
            "trigger_ratio": 0.8,
            "max_tokens": 128000,
            "keep_recent": 4,
            "use_checkpoint": True,
        },
        "max_tool_rounds": 50,
        "pruning": {
            "enabled": True,
            "max_messages": 30,
            "keep_recent": 6,
            "keep_system": True,
            "max_context_chars": 50000,
        },
        "mcpServers": DEFAULT_MCP_SERVERS.copy(),
    }


def generate_complete_config(
    current_workspace: str = "default",
) -> str:
    """生成完整默认配置的 JSON 字符串。

    Args:
        current_workspace: 初始当前工作空间名称。

    Returns:
        缩进格式化的 JSON 字符串。
    """
    config = OrderedDict(
        [
            ("gateway", {"port": 19527}),
            ("models", _get_default_models()),
            (
                "search",
                {
                    "webSearch": {
                        "provider": "bocha",
                        "providers": {
                            "bocha": {"apiKey": ""},
                            "baidu": {"apiKey": ""},
                        },
                    }
                },
            ),
            (
                "workspaces",
                {
                    "scopes": [{current_workspace: _get_default_workspace(current_workspace)}],
                    "current": current_workspace,
                },
            ),
            ("channels", {}),
            (
                "memory",
                {
                    "enabled": True,
                    "startup_context_enabled": True,
                    "daily_memory_days": 2,
                    "max_file_bytes": 16384,
                    "max_file_chars": 1200,
                    "max_total_chars": 2800,
                    "bootstrap_max_chars": 20000,
                    "bootstrap_total_max_chars": 150000,
                    "memory_search": True,
                    "memory_get": True,
                    "context_injection": "always",
                    "embedding": {
                        "provider": "ollama",
                        "openai": {"api_key": "", "model": "text-embedding-3-small"},
                        "ollama": {"model": "bge-m3", "base_url": "http://localhost:11434"},
                    },
                },
            ),
            (
                "asr",
                {
                    "provider": "aliyun",
                    "aliyun": {
                        "app_key": "",
                        "access_key_id": "",
                        "access_key_secret": "",
                        "region": "cn-shanghai",
                        "format": "pcm",
                        "sample_rate": 16000,
                    },
                    "baidu": {
                        "app_id": "",
                        "api_key": "",
                        "secret_key": "",
                    },
                    "macos": {
                        "locale": "zh-CN",
                        "require_authorized": False,
                    },
                    "whisper": {
                        "model_size": "base",
                        "language": "zh",
                        "device": "auto",
                        "compute_type": "auto",
                    },
                },
            ),
            ("log_level", "info"),
            (
                "langfuse",
                {
                    "enabled": False,
                    "secret_key": "",
                    "public_key": "",
                    "host": "",
                    "flush_interval": 30,
                    "trace_level": "full",
                    "debug": False,
                },
            ),
        ]
    )
    return json.dumps(config, indent=2, ensure_ascii=False)


def generate_minimal_config(
    api_key: str,
    model_name: str = "deepseek-chat",
    current_workspace: str = "default",
) -> str:
    """生成最小化配置的 JSON 字符串（仅必要字段）。

    Args:
        api_key: API Key。
        model_name: 模型 ID。
        current_workspace: 初始当前工作空间名称。

    Returns:
        缩进格式化的 JSON 字符串。
    """
    config = {
        "models": {
            "default": {
                "model": model_name,
                "apiKey": api_key,
            }
        },
        "workspaces": {
            "scopes": [
                {
                    current_workspace: {
                        "agents": {"leader": "main", "main": {"provider": "default", "fallback": []}},
                        "memory": {"enabled": True, "daily_memory_days": 2},
                        "mcpServers": {},
                    }
                }
            ],
            "current": current_workspace,
        },
        "channels": {},
        "log_level": "info",
        "langfuse": {
            "enabled": False,
            "secret_key": "",
            "public_key": "",
            "host": "",
            "flush_interval": 30,
            "trace_level": "full",
            "debug": False,
        },
    }
    return json.dumps(config, indent=2, ensure_ascii=False)


def get_default_config() -> str:
    """返回完整默认配置 JSON 字符串（``aion setup`` 默认使用）。

    Returns:
        与 ``generate_complete_config()`` 相同结果的 JSON 字符串。
    """
    return generate_complete_config()
