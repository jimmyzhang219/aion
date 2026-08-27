"""Skills 模块

从 workspace/skills/ 目录加载技能定义，
以 <available_skills> XML 块的形式注入 system prompt。

与 MCP 模块（aion.mcp）对应，两者均为 workspace 级外部能力扩展机制。
"""

from .model import Skill
from .loader import SkillsLoader, get_skills_loader

__all__ = [
    "Skill",
    "SkillsLoader",
    "get_skills_loader",
]
