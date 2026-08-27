"""models CLI（增删列与 KNOWN_PROVIDERS）单元测试

测试 aion.cli.models 中已知提供商默认配置、add_model / remove_model /
format_models_list 在临时配置文件上的行为。
"""

import json
import tempfile
from pathlib import Path

import sys

# 将项目 src 加入导入路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# 用于 CLI 测试的示例配置（models + workspaces），写入临时 JSON 文件
SAMPLE_CONFIG = {
    "models": {
        "deepseek": {
            "model": "deepseek-chat",
            "apiKey": "test-key",
            "baseUrl": "https://api.deepseek.com",
            "context_window": 1000000,
            "max_tokens": 8192,
        }
    },
    "workspaces": {
        "scopes": [{"default": {"agents": {"leader": "main", "main": {}}}}],
        "current": "default",
    },
}


def test_known_providers_openai():
    """openai 在 KNOWN_PROVIDERS 中应有 OpenAI 默认端点

    Returns:
        None
    """
    from aion.cli.models import KNOWN_PROVIDERS

    oa = KNOWN_PROVIDERS["openai"]
    assert oa["baseUrl"] == "https://api.openai.com"
    assert oa["context_window"] == 128000


# def test_known_providers_deepseek():
#     """deepseek 在 KNOWN_PROVIDERS 中应有默认配置"""
#     from aion.cli.models import KNOWN_PROVIDERS
#     ds = KNOWN_PROVIDERS["deepseek"]
#     assert ds["baseUrl"] == "https://api.deepseek.com"
#     assert ds["context_window"] == 200000


# def test_models_add_deepseek():
#     """add_model 应写入 deepseek 条目并填充已知默认 baseUrl
#
#     Returns:
#         None
#     """
#     from aion.cli.models import add_model
#     with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
#         json.dump(SAMPLE_CONFIG, f)
#         cfg_path = f.name
#     try:
#         msg = add_model(cfg_path, "deepseek", "deepseek-chat", "sk-test")
#         assert "已添加" in msg
#         with open(cfg_path) as f:
#             cfg = json.load(f)
#         assert cfg["models"]["deepseek"]["model"] == "deepseek-chat"
#         assert cfg["models"]["deepseek"]["apiKey"] == "sk-test"
#         assert cfg["models"]["deepseek"]["baseUrl"] == "https://api.deepseek.com"
#         assert cfg["models"]["deepseek"]["context_window"] == 200000
#         assert cfg["models"]["deepseek"]["max_tokens"] == 8192
#     finally:
#         Path(cfg_path).unlink(missing_ok=True)


def test_models_add_custom_baseurl():
    """传入 base_url 时应覆盖 KNOWN_PROVIDERS 默认值

    Returns:
        None
    """
    from aion.cli.models import add_model

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(SAMPLE_CONFIG, f)
        cfg_path = f.name
    try:
        msg = add_model(cfg_path, "openai", "gpt-4o", "sk-test", base_url="https://custom.openai.com")
        assert "已添加" in msg
        with open(cfg_path) as f:
            cfg = json.load(f)
        assert cfg["models"]["openai"]["baseUrl"] == "https://custom.openai.com"
    finally:
        Path(cfg_path).unlink(missing_ok=True)


def test_models_add_duplicate_name():
    """重复模型名应拒绝添加

    Returns:
        None
    """
    from aion.cli.models import add_model

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(SAMPLE_CONFIG, f)
        cfg_path = f.name
    try:
        msg = add_model(cfg_path, "deepseek", "some-model", "key")
        assert "已存在" in msg
        assert "已添加" not in msg
    finally:
        Path(cfg_path).unlink(missing_ok=True)


def test_models_add_unknown_provider_requires_baseurl():
    """未知 provider 且无 base_url 时应提示需要 --base-url

    Returns:
        None
    """
    from aion.cli.models import add_model

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(SAMPLE_CONFIG, f)
        cfg_path = f.name
    try:
        msg = add_model(cfg_path, "custom-provider", "my-model", "key")
        assert "--base-url" in msg
        assert "已添加" not in msg
    finally:
        Path(cfg_path).unlink(missing_ok=True)


def test_models_list_censored():
    """默认列表应脱敏 apiKey

    Returns:
        None
    """
    from aion.cli.models import format_models_list

    output = format_models_list(SAMPLE_CONFIG["models"], show_keys=False)
    assert "test-key" not in output
    assert "***" in output


def test_models_list_show_keys():
    """show_keys=True 时应显示完整 apiKey

    Returns:
        None
    """
    from aion.cli.models import format_models_list

    output = format_models_list(SAMPLE_CONFIG["models"], show_keys=True)
    assert "test-key" in output


def test_models_remove_existing():
    """remove_model 应删除已存在的模型条目

    Returns:
        None
    """
    from aion.cli.models import remove_model

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(SAMPLE_CONFIG, f)
        cfg_path = f.name
    try:
        msg = remove_model(cfg_path, "deepseek")
        assert "已删除" in msg
        with open(cfg_path) as f:
            cfg = json.load(f)
        assert "deepseek" not in cfg["models"]
    finally:
        Path(cfg_path).unlink(missing_ok=True)


def test_models_remove_nonexistent():
    """删除不存在的模型名应返回不存在提示

    Returns:
        None
    """
    from aion.cli.models import remove_model

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(SAMPLE_CONFIG, f)
        cfg_path = f.name
    try:
        msg = remove_model(cfg_path, "nonexistent")
        assert "不存在" in msg
        assert "已删除" not in msg
    finally:
        Path(cfg_path).unlink(missing_ok=True)
