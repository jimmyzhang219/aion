"""Skills 模块（兼容转发 — 实现已移至 aion.skills）

保留此文件供旧 import 路径（from aion.agent.skills import ...）使用。
新代码应直接 import aion.skills。
"""

from aion.skills import (
    Skill,
    SkillsLoader,
    get_skills_loader,
)
from aion.skills.parser import (
    escape_xml,
    load_skills_from_dir,
    format_skills_for_prompt,
    parse_skill_frontmatter,
)

__all__ = [
    "Skill",
    "SkillsLoader",
    "get_skills_loader",
    "escape_xml",
    "load_skills_from_dir",
    "format_skills_for_prompt",
    "parse_skill_frontmatter",
]
