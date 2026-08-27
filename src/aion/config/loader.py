"""配置加载器

从 ``~/.aion/aion.json`` 读取 JSON 并校验为 Pydantic ``Config`` 模型。
"""

import json
from pathlib import Path
from typing import Union

from .schema import Config

from ..core import constants

_ConfigLike = Union[Config, dict]


def save_config(config: _ConfigLike, path: Path | None = None) -> None:
    """将 ``Config`` 对象或字典写入 JSON 文件。

    Args:
        config: ``Config`` 对象或字典。
        path: 写入路径；为 None 时使用 ``DEFAULT_CONFIG_PATH``。
    """
    config_path = path or constants.DEFAULT_CONFIG_PATH
    data = config.model_dump(by_alias=True) if isinstance(config, Config) else config
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_config(path: Path | None = None) -> Config:
    """从 JSON 文件加载并校验配置。

    Args:
        path: 配置文件路径；为 None 时使用 ``DEFAULT_CONFIG_PATH``。

    Returns:
        校验后的 ``Config`` 实例。

    Raises:
        FileNotFoundError: 配置文件不存在。
        pydantic.ValidationError: JSON 结构不符合 schema。
    """
    config_path = path or constants.DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    with open(config_path) as f:
        raw = json.load(f)
    config = Config.model_validate(raw)

    # -- 校验 workspaces.current --
    if not config.workspaces.current:
        raise ValueError("workspaces.current is not set in config. Run 'aion setup' to initialize.")
    ws_dir = constants.DEFAULT_WORKSPACES_DIR / config.workspaces.current
    if not ws_dir.exists():
        raise ValueError(
            f"Workspace directory does not exist: {ws_dir} "
            f"(workspace '{config.workspaces.current}' may have been deleted). "
            "Run 'aion workspace use <name>' to select a valid workspace."
        )

    return config


def resolve_workspace_dir(
    workspace_name: str | None = None,
    config: Config | None = None,
) -> Path:
    """统一解析工作空间名称 → 绝对路径，校验目录存在。

    Args:
        workspace_name: 工作空间名称。为 None 时从 config.workspaces.current 读取。
        config: 配置对象。为 None 时自动加载。

    Returns:
        工作空间绝对路径。

    Raises:
        ValueError: workspace_name 为空，或目录不存在。
    """
    if workspace_name:
        name = workspace_name
    else:
        cfg = config or load_config()
        name = cfg.workspaces.current
    if not name:
        raise ValueError("workspaces.current is not set in config")
    workspace_dir = constants.DEFAULT_WORKSPACES_DIR / name
    if not workspace_dir.exists():
        raise ValueError(
            f"Workspace directory does not exist: {workspace_dir} (workspace '{name}' may have been deleted)"
        )
    return workspace_dir
