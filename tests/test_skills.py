"""P1 Skills 加载与提示词格式化单元测试

测试 Skill 数据类、从目录扫描 SKILL.md、
format_skills_for_prompt XML 片段，以及 SkillsLoader 缓存与 reload。

覆盖新旧两条 import 路径：
  - aion.skills（新模块入口）
  - aion.agent.skills（向后兼容转发）
"""

from pathlib import Path
import sys

# 将项目 src 加入导入路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# ── 新模块入口 ──
from aion.skills import Skill as NewSkill
from aion.skills import SkillsLoader as NewSkillsLoader
from aion.skills import get_skills_loader
from aion.skills.parser import (
    load_skills_from_dir,
    format_skills_for_prompt,
    parse_skill_frontmatter,
    escape_xml,
)
from aion.skills.loader import _skills_loaders

# ── 旧兼容入口 ──
from aion.agent.skills import (
    Skill as LegacySkill,
    SkillsLoader as LegacySkillsLoader,
    get_skills_loader as legacy_get_skills_loader,
    load_skills_from_dir as legacy_load_skills,
    format_skills_for_prompt as legacy_format,
    parse_skill_frontmatter as legacy_parse,
    escape_xml as legacy_escape_xml,
)


class TestSkill:
    """Skill 值对象测试"""

    def test_create_skill_new(self, tmp_path):
        """aion.skills — 构造 Skill 应保存 name、description 与路径

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        skill = NewSkill(
            name="test-skill", description="A test skill", file_path=tmp_path / "SKILL.md", base_dir=tmp_path
        )
        assert skill.name == "test-skill"
        assert skill.description == "A test skill"
        assert skill.file_path == tmp_path / "SKILL.md"

    def test_create_skill_legacy(self, tmp_path):
        """aion.agent.skills — 兼容入口返回相同结构

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        skill = LegacySkill(
            name="test-skill", description="A test skill", file_path=tmp_path / "SKILL.md", base_dir=tmp_path
        )
        assert isinstance(skill, NewSkill)  # 类型应一致


class TestParseSkillFrontmatter:
    """parse_skill_frontmatter 解析测试"""

    def test_no_frontmatter(self):
        """无 frontmatter 时返回空字典

        Returns:
            None
        """
        assert parse_skill_frontmatter("just content") == {}

    def test_valid_frontmatter(self):
        """标准 YAML frontmatter 应正确解析

        Returns:
            None
        """
        content = """---
name: test-skill
description: A test
---
Body"""
        result = parse_skill_frontmatter(content)
        assert result.get("name") == "test-skill"
        assert result.get("description") == "A test"

    def test_legacy_parse(self):
        """兼容入口解析结果应与新入口一致

        Returns:
            None
        """
        content = "---\nname: legacy\n---"
        assert parse_skill_frontmatter(content) == legacy_parse(content)


class TestEscapeXml:
    """escape_xml 转义测试"""

    def test_special_chars(self):
        """XML 特殊字符应被正确转义

        Returns:
            None
        """
        assert "&" in escape_xml("a&b")  # 仅验证函数可调用
        assert "<" not in escape_xml("<tag>")

    def test_legacy_escape(self):
        """兼容入口转义结果应与新入口一致

        Returns:
            None
        """
        raw = 'a & b < c > d "e"'
        assert escape_xml(raw) == legacy_escape_xml(raw)


class TestLoadSkillsFromDir:
    """load_skills_from_dir 目录扫描测试"""

    def test_empty_dir(self, tmp_path):
        """空 skills 目录应返回空列表

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        skills = load_skills_from_dir(skills_dir)
        assert skills == []

    def test_no_skills_dir(self, tmp_path):
        """路径不存在 skills 子目录时应返回空列表

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        skills = load_skills_from_dir(tmp_path)
        assert skills == []

    def test_load_single_skill(self, tmp_path):
        """单个子目录含 SKILL.md 时应解析 front matter

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        skill_dir = skills_dir / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: test-skill
description: A test skill description
---
# Test Skill Content""")

        skills = load_skills_from_dir(skills_dir)
        assert len(skills) == 1
        assert skills[0].name == "test-skill"
        assert skills[0].description == "A test skill description"

    def test_load_multiple_skills(self, tmp_path):
        """多个技能目录应按名称字母序排序

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        for name in ["alpha", "beta", "gamma"]:
            skill_dir = skills_dir / name
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name} skill\n---")

        skills = load_skills_from_dir(skills_dir)
        assert len(skills) == 3
        assert [s.name for s in skills] == ["alpha", "beta", "gamma"]

    def test_ignores_non_directories(self, tmp_path):
        """skills 根下的普通文件不应被当作技能

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "not-a-skill.md").write_text("content")

        skills = load_skills_from_dir(skills_dir)
        assert skills == []

    def test_ignores_hidden_directories(self, tmp_path):
        """以点开头的隐藏目录应被忽略

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / ".hidden").mkdir()

        skills = load_skills_from_dir(skills_dir)
        assert skills == []

    def test_legacy_returns_same(self, tmp_path):
        """兼容入口 load_skills_from_dir 返回相同结构

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        sd = skills_dir / "test"
        sd.mkdir()
        (sd / "SKILL.md").write_text("---\nname: test\n---")

        new_result = load_skills_from_dir(skills_dir)
        legacy_result = legacy_load_skills(skills_dir)
        assert len(new_result) == len(legacy_result)
        assert new_result[0].name == legacy_result[0].name


class TestFormatSkillsForPrompt:
    """format_skills_for_prompt 输出格式测试"""

    def test_empty_skills(self):
        """无技能时返回空字符串

        Returns:
            None
        """
        result = format_skills_for_prompt([])
        assert result == ""

    def test_single_skill(self, tmp_path):
        """单个技能应生成 available_skills XML 块

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        skill = NewSkill(
            name="test", description="A test", file_path=tmp_path / "test" / "SKILL.md", base_dir=tmp_path / "test"
        )
        result = format_skills_for_prompt([skill])
        assert "<available_skills>" in result
        assert "<name>test</name>" in result
        assert "<description>A test</description>" in result
        assert "</available_skills>" in result

    def test_multiple_skills(self, tmp_path):
        """多个技能名与描述均应出现在片段中

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        skills = [
            NewSkill("skill1", "First skill", tmp_path / "s1" / "SKILL.md", tmp_path / "s1"),
            NewSkill("skill2", "Second skill", tmp_path / "s2" / "SKILL.md", tmp_path / "s2"),
        ]
        result = format_skills_for_prompt(skills)
        assert "skill1" in result
        assert "skill2" in result

    def test_legacy_format(self, tmp_path):
        """兼容入口格式化结果应与新入口一致

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        skill = NewSkill("s", "d", tmp_path / "SKILL.md", tmp_path)
        assert format_skills_for_prompt([skill]) == legacy_format([skill])


class TestSkillsLoader:
    """SkillsLoader 工作区级加载与缓存测试"""

    def test_create_loader_new(self, tmp_path):
        """aion.skills — loader 应指向 workspace/skills 目录

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        loader = NewSkillsLoader(tmp_path)
        assert loader.workspace_dir == tmp_path
        assert loader.skills_dir == tmp_path / "skills"

    def test_create_loader_legacy(self, tmp_path):
        """aion.agent.skills — 兼容入口返回相同类型

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        loader = LegacySkillsLoader(tmp_path)
        assert isinstance(loader, NewSkillsLoader)

    def test_load_no_skills_dir(self, tmp_path):
        """无 skills 目录时 load 返回空列表

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        loader = NewSkillsLoader(tmp_path)
        skills = loader.load()
        assert skills == []

    def test_load_with_skills(self, tmp_path):
        """存在有效 SKILL.md 时应加载一条技能

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        skill_dir = skills_dir / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: test-skill\ndescription: Test\n---")

        loader = NewSkillsLoader(tmp_path)
        skills = loader.load()
        assert len(skills) == 1
        assert skills[0].name == "test-skill"

    def test_build_prompt(self, tmp_path):
        """build_prompt 应返回字符串（可为空）

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        loader = NewSkillsLoader(tmp_path)
        prompt = loader.build_prompt()
        assert isinstance(prompt, str)

    def test_build_prompt_with_skills(self, tmp_path):
        """build_prompt 含技能时应输出 XML 块

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        sd = skills_dir / "mytool"
        sd.mkdir()
        (sd / "SKILL.md").write_text("---\nname: mytool\ndescription: My tool\n---")

        loader = NewSkillsLoader(tmp_path)
        prompt = loader.build_prompt()
        assert "<available_skills>" in prompt
        assert "mytool" in prompt

    def test_cache_same_workspace(self, tmp_path):
        """同一 workspace_dir 的多个 SkillsLoader 应共享缓存（get_skills_loader）

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        loader_a = get_skills_loader(tmp_path)
        loader_b = get_skills_loader(tmp_path)
        assert loader_a is loader_b

    def test_cache_different_workspace(self, tmp_path):
        """不同 workspace_dir 应有不同的 SkillsLoader

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        ws_a = tmp_path / "ws-a"
        ws_a.mkdir()
        ws_b = tmp_path / "ws-b"
        ws_b.mkdir()

        loader_a = get_skills_loader(ws_a)
        loader_b = get_skills_loader(ws_b)
        assert loader_a is not loader_b

    def test_legacy_get_skills_loader(self, tmp_path):
        """兼容入口 get_skills_loader 返回相同实例

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        new_loader = get_skills_loader(tmp_path)
        legacy_loader = legacy_get_skills_loader(tmp_path)
        assert new_loader is legacy_loader


class TestSkillsCache:
    """模块级 _skills_loaders dict 缓存机制测试"""

    def test_cache_is_dict(self):
        """_skills_loaders 应为 dict 类型

        Returns:
            None
        """
        assert isinstance(_skills_loaders, dict)

    def test_clear_cache(self, tmp_path):
        """清理缓存后 get_skills_loader 应创建新实例

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        ws = tmp_path / "ws-clear"
        ws.mkdir()
        loader_a = get_skills_loader(ws)
        _skills_loaders.clear()
        loader_b = get_skills_loader(ws)
        assert loader_a is not loader_b
