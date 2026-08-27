"""Skill 安装与管理 CLI 命令

将 SKILL.md 安装到工作空间的 skills/ 目录下。
支持生态标准格式 owner/repo[@skill]、URL 或本地路径。
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import click

from ..config.loader import resolve_workspace_dir
from ._common import parse_skill_frontmatter, scan_skills
from ._common import load_config, get_current_workspace, find_workspace_in_scopes


def _get_workspace_dir(ws_name: str) -> Path:
    return resolve_workspace_dir(ws_name)


def _get_skills_dir(ws_name: str) -> Path:
    return _get_workspace_dir(ws_name) / "skills"


def _parse_github_source(source: str) -> tuple[str, str, str] | None:
    """解析 owner/repo[@skill] 格式，返回 (owner, repo, skill_name)。"""
    m = re.match(r"^([\w.-]+)/([\w.-]+)(?:@([\w.-]+))?$", source)
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)


_parse_skill_frontmatter = parse_skill_frontmatter  # 共享实现，来自 _common


def _install_skill_from_url(url: str, target_dir: Path, name: str) -> None:
    """从 URL 安装技能（暂未实现）"""
    click.echo(f"错误：从 URL 安装技能尚未实现: {url}")
    click.echo("请改用 'aion skill install <本地路径>'")


def _install_skill_from_local(src: Path, target_dir: Path, name: str) -> bool:
    """从本地路径复制 SKILL.md。"""
    target_file = target_dir / "SKILL.md"
    target_dir.mkdir(parents=True, exist_ok=True)

    if src.is_dir():
        src_file = src / "SKILL.md"
        if not src_file.exists():
            click.echo(f"  错误: 目录 '{src}' 中未找到 SKILL.md", err=True)
            shutil.rmtree(target_dir, ignore_errors=True)
            return False
        shutil.copy2(src_file, target_file)
    elif src.is_file():
        shutil.copy2(src, target_file)
    else:
        click.echo(f"  错误: 路径不存在: {src}", err=True)
        shutil.rmtree(target_dir, ignore_errors=True)
        return False

    content = target_file.read_text(encoding="utf-8")
    fm = _parse_skill_frontmatter(content)
    skill_name = fm.get("name", name)
    desc = fm.get("description", "")
    click.echo(f"  ✓ {click.style(skill_name, fg='cyan')}{' — ' + desc if desc else ''}")
    return True


def _git_clone_skills(owner: str, repo: str, skills_dir: Path, skill_filter: str | None = None) -> bool:
    """git clone 仓库，并将含 SKILL.md 的技能目录复制到 skills_dir。

    Args:
        owner: GitHub 所有者
        repo: GitHub 仓库名
        skills_dir: 目标 skills 根目录
        skill_filter: 可选，只安装指定技能名

    Returns:
        是否至少安装了一个技能
    """
    repo_url = f"https://github.com/{owner}/{repo}.git"

    with tempfile.TemporaryDirectory(prefix=".aion-skill-") as tmp:
        tmp_path = Path(tmp)
        click.echo(f"  克隆: {repo_url}")

        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", repo_url, str(tmp_path / "repo")],
                capture_output=True,
                text=True,
                timeout=120,
                check=True,
            )
        except FileNotFoundError:
            click.echo("  错误: 未找到 git 命令，请先安装 git", err=True)
            return False
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.strip() or "未知错误"
            click.echo(f"  克隆失败: {stderr}", err=True)
            return False
        except subprocess.TimeoutExpired:
            click.echo("  克隆超时（120s）", err=True)
            return False

        repo_dir = tmp_path / "repo"

        def _find_all_skill_dirs() -> list[Path]:
            """递归扫描整个仓库，找出所有含 SKILL.md 的目录。"""
            results = []
            for root, dirs, files in os.walk(repo_dir):
                # 跳过 .git
                if ".git" in root.split(os.sep):
                    continue
                if "SKILL.md" in files:
                    results.append(Path(root))
            return sorted(results)

        def _install_one(src_dir: Path, name: str) -> bool:
            """将一个技能目录复制到 skills_dir。"""
            target = skills_dir / name
            if target.exists():
                click.echo(f"  技能 '{name}' 已存在")
                if not click.confirm("覆盖安装？", default=False):
                    click.echo("  已跳过")
                    return False
                shutil.rmtree(target)

            shutil.copytree(src_dir, target)
            content = (target / "SKILL.md").read_text(encoding="utf-8")
            fm = _parse_skill_frontmatter(content)
            skill_name = fm.get("name", name)
            desc = fm.get("description", "")
            click.echo(f"  ✓ {click.style(skill_name, fg='cyan')}{' — ' + desc if desc else ''}")
            return True

        all_dirs = _find_all_skill_dirs()

        if skill_filter:
            # 安装单个技能: owner/repo@skill
            matches = [d for d in all_dirs if d.name == skill_filter]
            if not matches:
                click.echo(f"  错误: 仓库中未找到名为 '{skill_filter}' 的技能目录（递归搜索整个仓库）", err=True)
                return False
            # 取深度最浅的（根目录 > .claude/skills/ > skills/ 等子目录）
            skill_src = min(matches, key=lambda d: len(d.relative_to(repo_dir).parts))
            return _install_one(skill_src, skill_filter)
        else:
            # 安装仓库下所有含 SKILL.md 的技能
            if not all_dirs:
                click.echo("  未找到含 SKILL.md 的技能目录")
                return False
            for d in all_dirs:
                _install_one(d, d.name)
            return True


@click.group()
def skill():
    """技能管理命令组（add/list/remove）。"""
    pass


@skill.command("add")
@click.argument("source")
@click.option("--name", "-n", default=None, help="技能名称（默认从 SKILL.md frontmatter 或目录名自动推断）")
@click.option("--workspace", "ws_name", default=None, help="工作空间名称（默认当前）")
def add(source: str, name: str | None, ws_name: str | None):
    """安装技能到工作空间

    SOURCE 格式（生态标准）:

    \b
      owner/repo@skill    从 GitHub 安装指定技能（如 vercel-labs/skills@find-skills）
      owner/repo          从 GitHub 安装仓库下所有技能
      https://...         从 URL 下载 SKILL.md
      /local/path         从本地路径复制（文件或目录）
    """
    try:
        config = load_config()
    except FileNotFoundError as e:
        click.echo(str(e))
        click.echo("请先运行 aion setup")
        return

    if ws_name is None:
        ws_name = get_current_workspace(config)

    idx, ws = find_workspace_in_scopes(config, ws_name)
    if idx < 0:
        click.echo(f"工作空间不存在: {ws_name}")
        return

    skills_dir = _get_skills_dir(ws_name)

    # ---- 判断 source 类型 ----
    gh = _parse_github_source(source)

    if gh:
        # GitHub owner/repo[@skill] 格式 — 用 git clone 复制完整目录
        owner, repo, skill_name = gh
        label = f"{owner}/{repo}" + (f" @ {skill_name}" if skill_name else "")
        click.echo(f"从 GitHub 安装: {label}")

        skill_filter = skill_name or None
        success = _git_clone_skills(owner, repo, skills_dir, skill_filter=skill_filter)
        if not success:
            return
    elif source.startswith(("http://", "https://")):
        # URL 格式
        click.echo(f"从 URL 安装: {source}")
        skill_name = name or Path(source).stem.replace("_", "-").replace(".", "-")
        target_dir = skills_dir / skill_name

        if target_dir.exists():
            click.echo(f"技能 '{skill_name}' 已存在")
            if not click.confirm("覆盖安装？", default=False):
                click.echo("已取消")
                return

        _install_skill_from_url(source, target_dir, skill_name)
    else:
        # 本地路径
        src_path = Path(source)
        if name:
            skill_name = name
        elif src_path.is_file() and src_path.name == "SKILL.md":
            skill_name = src_path.parent.name
        else:
            skill_name = src_path.name.replace("_", "-")
        target_dir = skills_dir / skill_name

        if target_dir.exists():
            click.echo(f"技能 '{skill_name}' 已存在")
            if not click.confirm("覆盖安装？", default=False):
                click.echo("已取消")
                return

        _install_skill_from_local(src_path, target_dir, skill_name)


@skill.command("list")
@click.option("--workspace", "ws_name", default=None, help="工作空间名称（默认当前）")
def list_skills(ws_name: str | None):
    """列出工作空间已安装的技能

    \b
    示例：
        aion skill list
        aion skill list --workspace default
    """
    try:
        config = load_config()
    except FileNotFoundError:
        click.echo("配置文件不存在")
        return

    if ws_name is None:
        ws_name = get_current_workspace(config)

    idx, ws = find_workspace_in_scopes(config, ws_name)
    if idx < 0:
        click.echo(f"工作空间不存在: {ws_name}")
        return

    skills_dir = _get_skills_dir(ws_name)
    if not skills_dir.is_dir():
        click.echo(f"工作空间 '{ws_name}' 下无已安装技能")
        return

    # 扫描 SKILL.md（共享 scan_skills 实现）
    results = scan_skills(skills_dir.parent)

    click.echo(f"工作空间: {ws_name}")
    if not results:
        click.echo("  暂无已安装技能")
        return

    for s in results:
        desc = f" — {s['description']}" if s["description"] else ""
        click.echo(f"  {click.style(s['name'], fg='cyan')}{desc}")


@skill.command("remove")
@click.argument("name")
@click.option("--workspace", "ws_name", default=None, help="工作空间名称（默认当前）")
@click.option("--force", "-f", is_flag=True, help="跳过确认直接删除")
def remove(name: str, ws_name: str | None, force: bool):
    """从工作空间移除技能

    \b
    示例：
        aion skill remove find-skills
        aion skill remove find-skills --workspace default
        aion skill remove my-skill --force
    """
    try:
        config = load_config()
    except FileNotFoundError:
        click.echo("配置文件不存在")
        return

    if ws_name is None:
        ws_name = get_current_workspace(config)

    idx, ws = find_workspace_in_scopes(config, ws_name)
    if idx < 0:
        click.echo(f"工作空间不存在: {ws_name}")
        return

    target_dir = _get_skills_dir(ws_name) / name
    target_file = target_dir / "SKILL.md"

    if not target_file.exists():
        click.echo(f"技能 '{name}' 未找到（{target_dir}）")
        return

    if not force:
        click.confirm(f"确定要删除技能 '{name}' 吗？ [y/n]", default=False, show_default=False, abort=True)

    shutil.rmtree(target_dir)
    click.echo(f"✓ 已移除技能: {name}")
