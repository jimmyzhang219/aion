"""Bootstrap 删除校验 — 引导完成前禁止删除仪式文件"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ...core.constants import _is_ritual_markdown_basename

from ._parsers import _extract_section_content, _is_placeholder_only
from ._utils import (
    _extract_md_field_value,
    _is_effective_value,
)
from .files import (
    _resolve_agent_dir,
)


def _resolve_workspace_root(p: Path) -> Optional[Path]:
    """从仪式文件路径推导 workspace 根（openclaw anchored pattern）。

    策略1: 运行时 current_workspace context var（根已知，不需要发现）
    策略2: 路径结构推导 + WORKSPACE.md/USER.md 验证

    Args:
        p: 仪式文件绝对路径

    Returns:
        workspace 根目录 Path，无法确定时返回 None
    """
    p = Path(p).resolve()

    # 策略1: 运行时 context
    try:
        from aion.core.context import current_workspace

        ws = current_workspace.get()
        try:
            p.relative_to(ws)
            return ws
        except ValueError:
            pass
    except (LookupError, ValueError):
        pass

    # 策略2: 路径结构推导（仪式文件在 workspace 内的位置固定）
    name = p.name.lower()
    if name == "workspace_bootstrap.md":
        candidate = p.parent
    elif name == "agent_bootstrap.md":
        # 路径: <ws>/agents/<id>/AGENT_BOOTSTRAP.md
        # 找到 agents 目录，其 parent 即为 workspace 根
        for parent in p.parents:
            if parent.name == "agents":
                candidate = parent.parent
                break
        else:
            return None
    else:
        return None

    # 验证: WORKSPACE.md 或 USER.md 在 candidate 下
    if candidate and ((candidate / "WORKSPACE.md").is_file() or (candidate / "USER.md").is_file()):
        return candidate
    return None


def validate_bootstrap_delete_allowed(path: Path | str) -> tuple[bool, str]:
    """删除工作区 / Agent 引导文件前的硬校验（`WORKSPACE_BOOTSTRAP.md` / `AGENT_BOOTSTRAP.md`）。

    当 USER.md / WORKSPACE.md / CONFIG.md 等必填字段未满足时拒绝删除，避免「引导未完成但仪式文件被删」。

    Args:
        path: 待删除的引导文件路径（绝对或相对）

    Returns:
        (allowed, reason) 元组：allowed 为 True 时允许删除；拒绝时 reason 为中文说明
    """
    p = Path(path).resolve()
    if not _is_ritual_markdown_basename(p.name.lower()):
        return True, ""

    workspace_dir = _resolve_workspace_root(p)
    if not workspace_dir:
        return False, "无法定位工作空间根目录，拒绝删除引导文件"

    rel = p.relative_to(workspace_dir)
    parts = list(rel.parts)
    wsn = p.name.lower()
    # 工作区根：工作空间级引导
    if len(parts) == 1 and wsn in ("workspace_bootstrap.md",):
        user_md = workspace_dir / "USER.md"
        if not user_md.is_file():
            return False, "USER.md 不存在，初始化未完成，拒绝删除工作空间级引导文件"
        txt = user_md.read_text(encoding="utf-8")
        name_v = _extract_md_field_value(txt, "名字")
        call_v = _extract_md_field_value(txt, "称呼")
        tz_v = _extract_md_field_value(txt, "时区")
        missing = []
        if not _is_effective_value(name_v):
            missing.append("名字")
        if not _is_effective_value(call_v):
            missing.append("称呼")
        if not _is_effective_value(tz_v):
            missing.append("时区")
        if missing:
            return (
                False,
                f"工作区初始化未完成：USER.md 缺少有效字段 {', '.join(missing)}。"
                "请对 Project Context 里 `## …/USER.md` 章节标题所示路径执行 write，不要自行改换目录。",
            )

        # 检查 WORKSPACE.md 三个必填章节
        ws_md = workspace_dir / "WORKSPACE.md"
        required_sections = ["项目 / 领域", "当前目标", "通用约束"]
        if ws_md.is_file():
            ws_txt = ws_md.read_text(encoding="utf-8")
            ws_missing = [s for s in required_sections if _is_placeholder_only(_extract_section_content(ws_txt, s))]
            if ws_missing:
                return (
                    False,
                    f"工作区初始化未完成：WORKSPACE.md 中 {', '.join(ws_missing)} 章节尚未填写实际内容。"
                    "请对 Project Context 里 `## …/WORKSPACE.md` 章节标题所示路径执行 write，"
                    "用实际信息替换占位符内容。",
                )
        else:
            return False, "WORKSPACE.md 不存在，初始化未完成，拒绝删除工作空间级引导文件"

        return True, ""

    agent_id: Optional[str] = None
    if len(parts) >= 3 and parts[0] == "agents" and parts[2].lower() in ("agent_bootstrap.md",):
        agent_id = parts[1]
    elif len(parts) == 2 and parts[1].lower() in ("agent_bootstrap.md",):
        agent_id = parts[0]
    if not agent_id:
        return False, "无法识别引导文件层级，拒绝删除"

    ad = _resolve_agent_dir(workspace_dir, agent_id)
    config = ad / "CONFIG.md"
    if not config.is_file():
        return False, f"{agent_id} 的 CONFIG.md 不存在，拒绝删除 Agent 级引导文件"
    txt = config.read_text(encoding="utf-8")
    identity_section = _extract_section_content(txt, "Identity")
    if not identity_section:
        return False, f"{agent_id} 的 CONFIG.md 缺少 ## Identity 段，拒绝删除 Agent 级引导文件"
    name_v = _extract_md_field_value(identity_section, "名字")
    style_v = _extract_md_field_value(identity_section, "风格")
    emoji_v = _extract_md_field_value(identity_section, "Emoji")
    missing = []
    if not _is_effective_value(name_v):
        missing.append("名字")
    if not _is_effective_value(style_v):
        missing.append("风格")
    if not _is_effective_value(emoji_v):
        missing.append("Emoji")
    if missing:
        return False, f"Agent 初始化未完成：CONFIG.md ## Identity 段缺少有效字段 {', '.join(missing)}"
    return True, ""
