"""Skills 加载器

从 workspace/skills/ 目录加载技能定义并缓存。
支持 mtime 检测：skills 目录文件变化时自动重读，无需重启。
"""

import os
from pathlib import Path
from typing import Optional

from .model import Skill
from .parser import load_skills_from_dir, format_skills_for_prompt


class SkillsLoader:
    """技能加载器

    从 workspace/skills/ 加载 SKILL.md 并缓存，供 build_prompt 生成提示词。
    通过追踪 skills 目录的 mtime 检测文件变更，自动刷新缓存。
    """

    def __init__(
        self,
        workspace_dir: Path | str,
        max_skills: int = 50,
        max_file_bytes: int = 256000,
    ):
        """初始化加载器。

        Args:
            workspace_dir: 工作空间根目录
            max_skills: 最多加载技能数
            max_file_bytes: 单个 SKILL.md 最大字节数
        """
        self.workspace_dir = Path(workspace_dir)
        self.max_skills = max_skills
        self.max_file_bytes = max_file_bytes
        self._skills_cache: Optional[list[Skill]] = None
        self._cached_mtime: float = 0.0  # skills 目录的最新 mtime

    @property
    def skills_dir(self) -> Path:
        """技能目录：{workspace}/skills/

        Returns:
            skills 子目录 Path
        """
        return self.workspace_dir / "skills"

    def _get_current_mtime(self) -> float:
        """获取 skills 目录及其内容的当前最新 mtime。

        Returns:
            最新 mtime 时间戳；目录不存在时返回 0。
        """
        skills_dir = self.skills_dir
        if not skills_dir.exists():
            return 0.0

        latest = skills_dir.stat().st_mtime
        try:
            for entry in os.scandir(skills_dir):
                if entry.is_dir():
                    skill_md = Path(entry.path) / "SKILL.md"
                    if skill_md.exists():
                        mtime = skill_md.stat().st_mtime
                        if mtime > latest:
                            latest = mtime
                elif entry.name == "SKILL.md":
                    mtime = entry.stat().st_mtime
                    if mtime > latest:
                        latest = mtime
        except OSError:
            pass
        return latest

    def load(self) -> list[Skill]:
        """加载所有技能（带 mtime 缓存失效）。

        Returns:
            Skill 列表
        """
        current_mtime = self._get_current_mtime()
        if self._skills_cache is not None and current_mtime <= self._cached_mtime:
            return self._skills_cache

        self._skills_cache = load_skills_from_dir(
            self.skills_dir,
            max_skills=self.max_skills,
            max_file_bytes=self.max_file_bytes,
        )
        self._cached_mtime = current_mtime
        return self._skills_cache

    def build_prompt(self) -> str:
        """构建可注入 system prompt 的技能 XML 块。

        Returns:
            format_skills_for_prompt 的结果
        """
        skills = self.load()
        return format_skills_for_prompt(skills)

    # def reload(self) -> list[Skill]:  # 未使用
    #     """清除缓存并重新从磁盘加载技能。"""
    #     self._skills_cache = None
    #     return self.load()


# 模块级缓存（workspace_dir → SkillsLoader）
_skills_loaders: dict[Path, SkillsLoader] = {}


def get_skills_loader(
    workspace_dir: Path | str,
    max_skills: int = 50,
    max_file_bytes: int = 256000,
) -> SkillsLoader:
    """获取 workspace 级 SkillsLoader 单例。

    同一 workspace 共享一个 SkillsLoader 实例，
    避免重复扫描 skills/ 目录。

    Args:
        workspace_dir: 工作空间根目录
        max_skills: 最多加载技能数
        max_file_bytes: 单个 SKILL.md 最大字节数

    Returns:
        SkillsLoader 实例
    """
    ws_dir = Path(workspace_dir).resolve()
    if ws_dir not in _skills_loaders:
        _skills_loaders[ws_dir] = SkillsLoader(
            workspace_dir=ws_dir,
            max_skills=max_skills,
            max_file_bytes=max_file_bytes,
        )
    return _skills_loaders[ws_dir]
