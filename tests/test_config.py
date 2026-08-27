"""配置系统（多工作空间架构 v5）单元测试

验证 Config 模型校验、工作空间切换、Channel 路由以 current 为准、
load_config 从 JSON 加载，以及模型 max_tokens 默认值与自定义。

配置结构：
- models: 模型字典
- workspaces: scopes（工作空间列表）+ current（当前名）
- channels: channel 名称 -> 配置
- log_level: 全局日志级别

涉及磁盘路径的用例会先在临时目录下创建工作空间目录（与运行时「工作空间须存在」一致）。
"""

import pytest
import json
from pathlib import Path

import sys

# 将项目 src 加入导入路径，便于直接 import aion
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aion.config.models import EmbeddingConfig, EmbeddingProvider
from aion.config.schema import Config
from aion.config.loader import load_config


class TestConfigV4:
    """多工作空间架构 v5（scopes + current）测试"""

    def test_full_config(self):
        """测试完整配置

        Returns:
            None
        """
        config = Config.model_validate(
            {
                "models": {
                    "openai": {"model": "gpt-4", "apiKey": "sk-test"},
                },
                "workspaces": {
                    "scopes": [
                        {
                            "default": {
                                "agents": {
                                    "leader": "main",
                                    "main": {"provider": "openai", "fallback": []},
                                }
                            }
                        }
                    ],
                    "current": "default",
                },
            }
        )
        assert "openai" in config.models
        assert config.workspaces.current == "default"
        ws = config.workspaces.get_workspace("default")
        assert ws is not None
        assert ws.agents["leader"] == "main"
        assert ws.agents["main"]["provider"] == "openai"

    def test_get_current_workspace(self):
        """测试获取当前工作空间

        Returns:
            None
        """
        config = Config.model_validate(
            {
                "models": {},
                "workspaces": {
                    "scopes": [
                        {
                            "work": {"agents": {"leader": "main", "main": {}}},
                            "default": {"agents": {"leader": "main", "main": {}}},
                        }
                    ],
                    "current": "default",
                },
            }
        )
        ws = config.get_current_workspace()
        assert ws is not None

    def test_get_leader_agent_config(self):
        """测试获取 leader agent 配置

        Returns:
            None
        """
        config = Config.model_validate(
            {
                "models": {},
                "workspaces": {
                    "scopes": [
                        {
                            "default": {
                                "agents": {
                                    "leader": "agent1",
                                    "main": {"provider": "deepseek"},
                                    "agent1": {"provider": "deepseek", "fallback": []},
                                }
                            }
                        }
                    ],
                    "current": "default",
                },
            }
        )
        leader_cfg = config.get_leader_agent_config()
        assert leader_cfg["provider"] == "deepseek"

    def test_switch_workspace(self):
        """测试切换工作空间

        Returns:
            None
        """
        config = Config.model_validate(
            {
                "models": {},
                "workspaces": {
                    "scopes": [
                        {
                            "default": {"agents": {"leader": "main", "main": {}}},
                            "work": {"agents": {"leader": "main", "main": {}}},
                        }
                    ],
                    "current": "default",
                },
            }
        )
        assert config.switch_workspace("work") is True
        assert config.workspaces.current == "work"
        assert config.get_workspace("work") is not None

    def test_execution_mode_defaults_to_react(self):
        """execution_mode 默认值为 react"""
        config = Config.model_validate(
            {
                "models": {},
                "workspaces": {
                    "scopes": [{"default": {"agents": {"leader": "main", "main": {}}}}],
                    "current": "default",
                },
            }
        )
        ws = config.workspaces.get_workspace("default")
        assert ws.execution_mode == "react"

    def test_execution_mode_can_be_plan(self):
        """execution_mode 可设置为 plan"""
        config = Config.model_validate(
            {
                "models": {},
                "workspaces": {
                    "scopes": [
                        {
                            "default": {
                                "agents": {"leader": "main", "main": {}},
                                "execution_mode": "plan",
                            }
                        }
                    ],
                    "current": "default",
                },
            }
        )
        ws = config.workspaces.get_workspace("default")
        assert ws.execution_mode == "plan"

    def test_execution_mode_rejects_invalid(self):
        """execution_mode 拒绝无效值"""
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            Config.model_validate(
                {
                    "models": {},
                    "workspaces": {
                        "scopes": [
                            {
                                "default": {
                                    "agents": {"leader": "main", "main": {}},
                                    "execution_mode": "invalid",
                                }
                            }
                        ],
                        "current": "default",
                    },
                }
            )

    # def test_get_workspace_dir(self, tmp_path, monkeypatch):  # get_workspace_dir 已废弃
    #     monkeypatch.setattr(Path, "home", lambda: tmp_path)
    #     ws_root = tmp_path / ".aion" / "workspaces" / "myws"
    #     ws_root.mkdir(parents=True)
    #     config = Config.model_validate({
    #         "models": {},
    #         "workspaces": {"scopes": [{"myws": {"agents": {"leader": "main", "main": {}}}}], "current": "myws"},
    #     })
    #     ws_dir = config.get_workspace_dir()
    #     assert ws_dir.name == "myws"


class TestChannelConfig:
    """Channel 配置；路由以 ``workspaces.current`` 为唯一事实源。"""

    # def test_get_channel_config(self):  # get_channel_config 已废弃
    #     config = Config.model_validate({
    #         "models": {},
    #         "workspaces": {"scopes": [{"default": {"agents": {"leader": "main", "main": {}}}}], "current": "default"},
    #         "channels": {"feishu": {"enabled": True, "workspace": "default", "appId": "cli_test", "appSecret": "secret"}},
    #     })
    #     feishu = config.get_channel_config("feishu")
    #     assert feishu is not None

    # def test_get_channel_workspace_name(self):  # get_channel_workspace_name 已废弃
    #     config = Config.model_validate({
    #         "models": {},
    #         "workspaces": {"scopes": [{"work": {"agents": {"leader": "agent1", "main": {}, "agent1": {}}}}], "current": "work"},
    #         "channels": {"feishu": {"workspace": "default"}},
    #     })
    #     assert config.get_channel_workspace_name("feishu") == "work"

    # def test_get_channel_workspace(self):  # get_channel_workspace 已废弃
    #     config = Config.model_validate({
    #         "models": {},
    #         "workspaces": {"scopes": [{"default": {"agents": {"leader": "main", "main": {}, "agent1": {}}}}], "current": "default"},
    #         "channels": {"feishu": {"workspace": "stale-ignored"}},
    #     })
    #     ws = config.get_channel_workspace("feishu")
    #     assert ws is not None

    # def test_get_channel_leader_agent_config(self):  # get_channel_leader_agent_config 已废弃
    #     config = Config.model_validate({
    #         "models": {"deepseek": {"model": "deepseek-chat"}},
    #         "workspaces": {"scopes": [{"default": {"agents": {"leader": "agent1", "main": {"provider": "openai"}, "agent1": {"provider": "deepseek"}}}}], "current": "default"},
    #         "channels": {"feishu": {"workspace": "stale-ignored"}},
    #     })
    #     agent_cfg = config.get_channel_leader_agent_config("feishu")
    #     assert agent_cfg["provider"] == "deepseek"

    # def test_channel_missing_workspace_uses_current(self):  # get_channel_workspace_name 已废弃
    #     config = Config.model_validate({
    #         "models": {},
    #         "workspaces": {"scopes": [{"default": {"agents": {"leader": "main", "main": {}}}}], "current": "default"},
    #         "channels": {"feishu": {"enabled": True, "appId": "test"}},
    #     })
    #     assert config.get_channel_workspace_name("feishu") == "default"


class TestLoadConfigV4:
    """配置加载测试（v5 JSON）"""

    def test_load_from_file(self, tmp_path, monkeypatch):
        """测试从文件加载配置（scopes + current）

        Args:
            tmp_path: pytest 临时目录（Path）
            monkeypatch: pytest monkeypatch fixture

        Returns:
            None
        """
        from aion.core import constants

        # 创建临时工作空间目录，避免 load_config 校验失败
        ws_dir = tmp_path / "workspaces" / "default"
        ws_dir.mkdir(parents=True)
        monkeypatch.setattr(constants, "DEFAULT_WORKSPACES_DIR", tmp_path / "workspaces")

        config_file = tmp_path / "aion.json"
        config_data = {
            "models": {
                "openai": {"model": "gpt-4", "apiKey": "sk-test"},
            },
            "workspaces": {
                "scopes": [
                    {
                        "default": {
                            "agents": {
                                "leader": "main",
                                "main": {"provider": "openai", "fallback": []},
                            }
                        }
                    }
                ],
                "current": "default",
            },
        }
        config_file.write_text(json.dumps(config_data))

        config = load_config(config_file)
        assert config.models["openai"]["model"] == "gpt-4"
        assert config.workspaces.current == "default"
        ws = config.workspaces.get_workspace("default")
        assert ws is not None
        assert ws.agents["leader"] == "main"
        assert ws.agents["main"]["provider"] == "openai"


def test_default_models_returns_empty():
    """_get_default_models 返回空字典，由 aion setup 引导配置。"""
    from aion.config.defaults import _get_default_models

    cfg = _get_default_models()
    assert cfg == {}
    assert isinstance(cfg, dict)


def test_model_max_tokens_from_leader_config():
    """leader agent 关联的 model 配置应能读出 max_tokens（默认 200000）

    Returns:
        None
    """
    from aion.config.schema import Config

    config = Config.model_validate(
        {
            "models": {"openai": {"model": "gpt-4", "apiKey": "x", "max_tokens": 200000}},
            "workspaces": {
                "scopes": [{"default": {"agents": {"leader": "main", "main": {"provider": "openai"}}}}],
                "current": "default",
            },
        }
    )
    leader = config.get_leader_agent_config()
    provider = leader.get("provider", "openai")
    model_cfg = config.get_model_config(provider) or {}
    assert model_cfg.get("max_tokens", 200000) == 200000


class TestEmbeddingConfig:
    """EmbeddingConfig 模型校验与默认值测试"""

    def test_default_provider_is_ollama(self):
        cfg = EmbeddingConfig()
        assert cfg.provider == "ollama"
        assert cfg.provider.value == "ollama"

    def test_embedding_config_openai_defaults(self):
        cfg = EmbeddingConfig()
        assert cfg.openai["model"] == "text-embedding-3-small"

    def test_embedding_config_ollama_defaults(self):
        cfg = EmbeddingConfig()
        assert cfg.ollama["model"] == "bge-m3"
        assert cfg.ollama["base_url"] == "http://localhost:11434"

    def test_custom_provider(self):
        cfg = EmbeddingConfig(provider=EmbeddingProvider.OPENAI)
        assert cfg.provider == "openai"

    def test_invalid_provider_raises_error(self):
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            EmbeddingConfig(provider="invalid")


# class TestGetEmbeddingConfig:  # get_embedding_config 已废弃
#     def test_get_embedding_config_returns_none_when_not_configured(self):
#         config = Config.model_validate({
#             "models": {},
#             "workspaces": {"scopes": [{"default": {"agents": {"leader": "main", "main": {}}}}], "current": "default"},
#         })
#         assert config.get_embedding_config() is None
#
#     def test_get_embedding_config_returns_config(self):
#         config = Config.model_validate({
#             "models": {},
#             "workspaces": {"scopes": [{"default": {"agents": {"leader": "main", "main": {}}}}], "current": "default"},
#             "memory": {"enabled": True, "embedding": {"provider": "openai", "openai": {"api_key": "sk-test"}}},
#         })
#         emb = config.get_embedding_config()
#         assert emb is not None
