"""Skills 数据模型

Skill 数据类，表示 workspace/skills/<name>/SKILL.md 中定义的一项技能。
"""

from pathlib import Path


class Skill:
    """技能对象，对应 skills 目录下的一个 SKILL.md。

    Attributes:
        name: 展示名（frontmatter 或目录名）
        description: 技能简介，注入 available_skills XML
        file_path: SKILL.md 绝对路径
        base_dir: 技能根目录，解析技能内相对路径的基准
    """

    def __init__(self, name: str, description: str, file_path: Path, base_dir: Path):
        """初始化 Skill 元数据。

        Args:
            name: 技能展示名（来自 frontmatter 或目录名）
            description: 技能描述
            file_path: SKILL.md 绝对路径
            base_dir: 技能目录（SKILL.md 的父目录，用于解析相对路径）
        """
        self.name = name
        self.description = description
        self.file_path = file_path
        self.base_dir = base_dir
