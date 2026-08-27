"""Skills 解析与格式化

提供 SKILL.md 的 YAML frontmatter 解析、<available_skills> XML 格式化，
以及从 skills 目录加载技能的函数。
"""

import os
import re
from pathlib import Path

from .model import Skill


def escape_xml(s: str) -> str:
    """转义 XML 特殊字符，供 <available_skills> 块使用。

    Args:
        s: 原始字符串

    Returns:
        XML 安全字符串
    """
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")
    )


def parse_skill_frontmatter(content: str) -> dict:
    """解析 SKILL.md 的 YAML frontmatter（使用正则，不依赖 yaml 库）。

    Args:
        content: SKILL.md 全文

    Returns:
        frontmatter 键值 dict；无 frontmatter 时返回 {}
    """
    # Frontmatter 格式：--- 包裹的 YAML
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}

    result = {}
    for line in match.group(1).split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # 支持 "key: value" 和 'key: value' 格式
        m = re.match(r'^(["\']?)([^"\':]+)\1\s*:\s*(.*)$', line)
        if m:
            key = m.group(2).strip()
            value = m.group(3).strip().strip("\"'")
            result[key] = value
    return result


def load_skills_from_dir(
    skills_dir: Path,
    max_skills: int = 50,
    max_file_bytes: int = 256000,
) -> list[Skill]:
    """从 skills 目录加载所有技能。

    目录结构::
        skills/
            skill-name-1/
                SKILL.md
            skill-name-2/
                SKILL.md

    Args:
        skills_dir: skills 根目录
        max_skills: 最多加载技能数
        max_file_bytes: 单个 SKILL.md 最大字节数

    Returns:
        按名称排序的 Skill 列表
    """
    if not skills_dir.exists() or not skills_dir.is_dir():
        return []

    skills: list[Skill] = []

    # 遍历 skills_dir 下的每个子目录
    for entry in os.scandir(skills_dir):
        if not entry.is_dir():
            continue
        if entry.name.startswith("."):
            continue

        skill_md_path = Path(entry.path) / "SKILL.md"
        if not skill_md_path.exists():
            continue

        # 检查文件大小，过大则跳过
        try:
            size = skill_md_path.stat().st_size
            if size > max_file_bytes:
                print(f"[Skills] 跳过 {entry.name}：SKILL.md 太大 ({size} bytes)")
                continue
        except OSError:
            continue

        # 读取并解析 frontmatter
        try:
            content = skill_md_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            print(f"[Skills] 跳过 {entry.name}：编码错误")
            continue
        except OSError:
            continue

        frontmatter = parse_skill_frontmatter(content)

        name = frontmatter.get("name", entry.name)
        description = frontmatter.get("description", "")

        skills.append(
            Skill(
                name=name,
                description=description,
                file_path=skill_md_path,
                base_dir=skill_md_path.parent,
            )
        )

        if len(skills) >= max_skills:
            break

    # 按名称排序
    skills.sort(key=lambda s: s.name)
    return skills


def format_skills_for_prompt(skills: list[Skill]) -> str:
    """将技能列表格式化为 <available_skills> XML 块。

    Args:
        skills: Skill 对象列表

    Returns:
        可注入 system prompt 的 Markdown/XML 字符串；无技能时返回 ""
    """
    if not skills:
        return ""

    lines = [
        "",
        "The following skills provide specialized instructions for specific tasks.",
        "Use the read tool to load a skill's file when the task matches its description.",
        "When a skill file references a relative path, resolve it against the skill directory",
        "(parent of SKILL.md / dirname of the path) and use that absolute path in tool commands.",
        "",
        "<available_skills>",
    ]

    for skill in skills:
        lines.append("  <skill>")
        lines.append(f"    <name>{escape_xml(skill.name)}</name>")
        lines.append(f"    <description>{escape_xml(skill.description)}</description>")
        lines.append(f"    <location>{escape_xml(str(skill.file_path))}</location>")
        lines.append("  </skill>")

    lines.append("</available_skills>")
    return "\n".join(lines)
