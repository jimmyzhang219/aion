"""Tests for config loader — resolve_workspace_dir"""

import pytest

from aion.config.loader import resolve_workspace_dir
from aion.config.schema import Config, WorkspacesConfig


def test_resolve_workspace_dir_from_config(tmp_path, monkeypatch):
    """使用 config.workspaces.current 解析"""
    ws_dir = tmp_path / "workspaces" / "myws"
    ws_dir.mkdir(parents=True)
    from aion.core import constants

    monkeypatch.setattr(constants, "DEFAULT_WORKSPACES_DIR", tmp_path / "workspaces")
    config = Config(workspaces=WorkspacesConfig(current="myws"))
    result = resolve_workspace_dir(config=config)
    assert result == ws_dir


def test_resolve_workspace_dir_by_name(tmp_path, monkeypatch):
    """显式传入 workspace_name"""
    ws_dir = tmp_path / "workspaces" / "custom"
    ws_dir.mkdir(parents=True)
    from aion.core import constants

    monkeypatch.setattr(constants, "DEFAULT_WORKSPACES_DIR", tmp_path / "workspaces")
    # 传入 config 避免隐式依赖 ~/.aion/aion.json
    config = Config(workspaces=WorkspacesConfig(current="myws"))
    result = resolve_workspace_dir("custom", config=config)
    assert result == ws_dir


def test_resolve_workspace_dir_empty_name(tmp_path):
    """workspace_name 为空时报错"""
    config = Config(workspaces=WorkspacesConfig(current=""))
    with pytest.raises(ValueError, match="workspaces.current is not set"):
        resolve_workspace_dir(config=config)


def test_resolve_workspace_dir_nonexistent(tmp_path, monkeypatch):
    """目录不存在时报错"""
    from aion.core import constants

    monkeypatch.setattr(constants, "DEFAULT_WORKSPACES_DIR", tmp_path / "workspaces")
    config = Config(workspaces=WorkspacesConfig(current="ghost"))
    with pytest.raises(ValueError, match="does not exist"):
        resolve_workspace_dir(config=config)


def test_load_config_validates_workspaces_current_null(tmp_path):
    """load_config 校验 workspaces.current 不能为空"""
    from aion.config.loader import load_config

    config_path = tmp_path / "aion.json"
    config_path.write_text('{"workspaces": {"current": "", "scopes": []}, "models": {}, "channels": {}}')
    with pytest.raises(ValueError, match="workspaces.current is not set"):
        load_config(config_path)


def test_load_config_validates_workspaces_exists(tmp_path, monkeypatch):
    """load_config 校验 workspaces.current 目录存在"""
    from aion.config.loader import load_config
    from aion.core import constants

    monkeypatch.setattr(constants, "DEFAULT_WORKSPACES_DIR", tmp_path / "workspaces")

    config_path = tmp_path / "aion.json"
    config_path.write_text(
        '{"workspaces": {"current": "nonexistent", "scopes": [{"nonexistent": {"agents": {"leader": "main"}}}]}, "models": {}, "channels": {}}'
    )
    with pytest.raises(ValueError, match="does not exist"):
        load_config(config_path)


def test_config_validation_current_required():
    """WorkspacesConfig.current 不应有默认值，缺失时抛 ValidationError"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WorkspacesConfig()
