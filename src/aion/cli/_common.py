"""aion CLI 共享模块 — 配置读写与工作空间目录创建"""

import json
from pathlib import Path

from ..core.constants import DEFAULT_CONFIG_PATH, AION_HOME, DEFAULT_WORKSPACES_DIR as WORKSPACES_DIR


def create_workspace(workspace_name: str, agent_id: str = "main") -> None:
    from ..agent.bootstrap.templates import (
        WORKSPACE_MD,
        WORKSPACE_BOOTSTRAP_MD,
        USER_MD,
        CONFIG_MD,
        AGENT_BOOTSTRAP_MD,
    )

    ws_dir = WORKSPACES_DIR / workspace_name
    agent_dir = ws_dir / "agents" / agent_id

    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "memory").mkdir(parents=True, exist_ok=True)
    (agent_dir / "sessions").mkdir(parents=True, exist_ok=True)

    (ws_dir / "WORKSPACE.md").write_text(WORKSPACE_MD, encoding="utf-8")
    (ws_dir / "WORKSPACE_BOOTSTRAP.md").write_text(WORKSPACE_BOOTSTRAP_MD, encoding="utf-8")
    (ws_dir / "USER.md").write_text(USER_MD, encoding="utf-8")
    (agent_dir / "CONFIG.md").write_text(CONFIG_MD(agent_id), encoding="utf-8")
    (agent_dir / "AGENT_BOOTSTRAP.md").write_text(AGENT_BOOTSTRAP_MD(agent_id, workspace_name), encoding="utf-8")


def ensure_aion_base() -> None:
    AION_HOME.mkdir(parents=True, exist_ok=True)


def load_existing_config(config_path: Path | None = None) -> dict | None:
    path = config_path or DEFAULT_CONFIG_PATH
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def merge_configs(existing: dict, defaults: dict) -> dict:
    result = json.loads(json.dumps(existing))

    if "models" not in result:
        result["models"] = defaults["models"]
    else:
        for model_name, default_cfg in defaults.get("models", {}).items():
            if model_name not in result["models"]:
                result["models"][model_name] = default_cfg
            elif isinstance(default_cfg, dict):
                existing_cfg = result["models"][model_name]
                if isinstance(existing_cfg, dict):
                    for k, v in default_cfg.items():
                        if k not in existing_cfg:
                            existing_cfg[k] = v

    if "scopes" not in result.get("workspaces", {}):
        result.setdefault("workspaces", {})["scopes"] = defaults["workspaces"]["scopes"]
    else:
        if "current" not in result["workspaces"]:
            result["workspaces"]["current"] = defaults["workspaces"]["current"]

    if "channels" not in result:
        result["channels"] = defaults.get("channels", {})

    return result


def write_config(config_path: Path, config: dict) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def load_config() -> dict:
    """加载 aion.json 配置字典（委托 config/loader.py）。

    Returns:
        配置字典。

    Raises:
        FileNotFoundError: 配置文件不存在时抛出。
    """
    from ..config.loader import load_config as _loader_load

    return _loader_load().model_dump(by_alias=True)


def save_config(config_dict: dict) -> None:
    """将配置字典写回 aion.json（委托 config/loader.py）。

    Args:
        config_dict: 完整配置字典。
    """
    from ..config.loader import save_config as _loader_save

    _loader_save(config_dict)


def get_current_workspace(config_dict: dict) -> str:
    """获取当前工作空间名称。

    Args:
        config_dict: 完整配置字典。

    Returns:
        当前工作空间名字符串。

    Raises:
        ValueError: workspaces.current 未设置。
    """
    workspaces = config_dict.get("workspaces", {})
    current = workspaces.get("current")
    if not current:
        raise ValueError("workspaces.current is not set in config")
    return current


def find_workspace_in_scopes(config_dict: dict, ws_name: str) -> tuple:
    """在 workspaces.scopes 中查找指定工作空间。

    Args:
        config_dict: 完整配置字典。
        ws_name: 目标工作空间名称。

    Returns:
        ``(scope_index, ws_config)``；未找到时 ``(-1, None)``。
    """
    scopes = config_dict.get("workspaces", {}).get("scopes", [])
    for i, scope in enumerate(scopes):
        if ws_name in scope:
            return i, scope[ws_name]
    return -1, None


# ── 技能（SKILL.md）解析与扫描 ────────────────────────────────────
# 替代 cli/status.py 和 cli/skill.py 中的重复实现

import re as _re


def parse_skill_frontmatter(content: str) -> dict:
    """解析 SKILL.md 的 YAML-like frontmatter（无需 yaml 依赖）。"""
    m = _re.match(r"^---\n(.*?)\n---", content, _re.DOTALL)
    if not m:
        return {}
    result = {}
    for line in m.group(1).split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        kv = _re.match(r'^(["\']?)([^"\':]+)\1\s*:\s*(.*)$', line)
        if kv:
            result[kv.group(2).strip()] = kv.group(3).strip().strip("\"'")
    return result


def scan_skills(workspace_dir: Path) -> list[dict]:
    """扫描 workspace/skills/*/SKILL.md 目录，返回 [{name, description, dir}]。"""
    skills_dir = workspace_dir / "skills"
    if not skills_dir.is_dir():
        return []
    results = []
    import os as _os

    for entry in _os.scandir(skills_dir):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        skill_md = Path(entry.path) / "SKILL.md"
        if not skill_md.is_file():
            continue
        try:
            content = skill_md.read_text(encoding="utf-8")
        except Exception:
            continue
        fm = parse_skill_frontmatter(content)
        results.append(
            {
                "name": fm.get("name", entry.name),
                "description": fm.get("description", ""),
                "dir": entry.name,
            }
        )
    results.sort(key=lambda s: s["name"])
    return results
