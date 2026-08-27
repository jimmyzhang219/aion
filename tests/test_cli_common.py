"""Tests for cli/_common.py — get_current_workspace"""

import pytest
from aion.cli._common import get_current_workspace


def test_get_current_workspace_missing():
    """当前工作空间缺失时抛 ValueError"""
    config_dict = {"workspaces": {"scopes": [{"myws": {}}]}}
    with pytest.raises(ValueError, match="workspaces.current"):
        get_current_workspace(config_dict)
