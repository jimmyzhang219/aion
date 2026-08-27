"""Bootstrap 文件收集与排序 — 文件发现、读取、截断、预算控制"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ._constants import (
    CONTEXT_FILE_ORDER,
    DEFAULT_BOOTSTRAP_MAX_CHARS,
    DEFAULT_BOOTSTRAP_TOTAL_MAX_CHARS,
    MIN_BOOTSTRAP_FILE_BUDGET_CHARS,
    BOOTSTRAP_HEAD_RATIO,
    BOOTSTRAP_TAIL_RATIO,
)
from ._utils import (
    _extract_md_field_value,
    _is_effective_value,
)


@dataclass
class WorkspaceBootstrapFile:
    """磁盘上的一条 bootstrap 记录。

    Attributes:
        name: 规范展示名（如 CONFIG.md），用于截断提示与排序
        path: 绝对路径，写入 Project Context 的 ``##`` 标题行
        content: 已读正文；文件缺失或未读时为 None
        missing: True 表示预期路径上文件不存在（注入 [MISSING] 占位）
    """

    name: str
    path: str
    content: Optional[str] = None
    missing: bool = False


@dataclass
class EmbeddedContextFile:
    """注入前经过预算与截断后的上下文文件。

    Attributes:
        path: 与 WorkspaceBootstrapFile.path 一致，用于 ``##`` 标题
        content: 经单文件/总预算截断后的 Markdown 正文
    """

    path: str
    content: str


def _normalize_path_for_prompt(path_value: str) -> str:
    """规范化路径用于 prompt 排序与比较（统一斜杠、去首尾空白）。

    Args:
        path_value: 原始路径字符串

    Returns:
        规范化后的路径
    """
    return path_value.strip().replace("\\", "/")


def _get_context_file_basename(path_value: str) -> str:
    """取路径最后一段并转小写，用于 CONTEXT_FILE_ORDER 查找。

    Args:
        path_value: 文件路径

    Returns:
        小写 basename
    """
    normalized = _normalize_path_for_prompt(path_value)
    return (normalized.split("/")[-1] or normalized).lower()


def _sort_order_for_path(path_value: str) -> int:
    """返回路径在 Project Context 中的排序权重。

    Args:
        path_value: 文件路径

    Returns:
        CONTEXT_FILE_ORDER 中的整数权重，未知文件为 sys.maxsize
    """
    base = _get_context_file_basename(path_value)
    return CONTEXT_FILE_ORDER.get(base, sys.maxsize)


def _resolve_case_insensitive_file(parent: Path, *candidates: str) -> Optional[Path]:
    """在 parent 下查找第一个存在的路径（依次尝试 candidates，含大小写变体）。

    Args:
        parent: 父目录
        *candidates: 候选文件名列表

    Returns:
        存在的文件 resolve 路径；均未找到时返回 None
    """
    for name in candidates:
        p = parent / name
        if p.is_file():
            return p.resolve()
    try:
        for child in parent.iterdir():
            if not child.is_file():
                continue
            low = child.name.lower()
            for name in candidates:
                if name.lower() == low:
                    return child.resolve()
    except OSError:
        pass
    return None


# —— 两级引导脚本：不同文件名，避免在 Project Context 中重名混淆 ——
WORKSPACE_RITUAL_FILENAMES: tuple[str, ...] = ("WORKSPACE_BOOTSTRAP.md",)
AGENT_RITUAL_FILENAMES: tuple[str, ...] = ("AGENT_BOOTSTRAP.md",)


def _resolve_workspace_ritual_path(ws: Path) -> Optional[Path]:
    """解析工作空间级引导文件实际路径（WORKSPACE_BOOTSTRAP.md）。

    Args:
        ws: 工作空间根目录

    Returns:
        存在的引导文件 Path；不存在时返回 None
    """
    ws = Path(ws).resolve()
    for fn in WORKSPACE_RITUAL_FILENAMES:
        f = _resolve_case_insensitive_file(ws, fn, fn.lower())
        if f and f.is_file():
            return f
    return None


def _resolve_agent_ritual_path(agent_dir: Path) -> Optional[Path]:
    """解析 Agent 级引导文件实际路径。

    Args:
        agent_dir: Agent 目录

    Returns:
        存在的引导文件 Path；不存在时返回 None
    """
    ad = Path(agent_dir).resolve()
    for fn in AGENT_RITUAL_FILENAMES:
        f = _resolve_case_insensitive_file(ad, fn, fn.lower())
        if f and f.is_file():
            return f
    return None


def _resolve_agent_dir(workspace_dir: Path, agent_id: str) -> Path:
    """返回 Agent 目录路径 agents/{agent_id}/。

    Args:
        workspace_dir: 工作空间根目录
        agent_id: Agent 标识

    Returns:
        agents/{agent_id}/ 的绝对路径
    """
    return (Path(workspace_dir).resolve() / "agents" / agent_id).resolve()


def _collect_bootstrap_file_specs(
    workspace_dir: Path,
    agent_id: Optional[str],
) -> list[tuple[str, Path, bool]]:
    """收集待注入 Project Context 的 bootstrap 文件规格列表。

    Args:
        workspace_dir: 工作空间根目录
        agent_id: 可选 Agent ID；有则追加 agents/<id>/ 下 CONFIG.md 等

    Returns:
        (规范 name, 绝对路径, 是否必须存在) 列表。
        仪式文件（WORKSPACE_BOOTSTRAP.md / AGENT_BOOTSTRAP.md）仅磁盘存在时加入；
        其他文件（USER.md / CONFIG.md 等）缺失时仍返回预期绝对路径供 [MISSING] 占位。
    """
    ws = Path(workspace_dir).resolve()
    specs: list[tuple[str, Path, bool]] = []

    # —— 公共（根目录）——
    # WORKSPACE.md：工作空间描述（项目是什么、目标、技术栈、领域知识）——可选，不存在时不注入
    ws_md = _resolve_case_insensitive_file(ws, "WORKSPACE.md", "workspace.md")
    if ws_md:
        specs.append(("WORKSPACE.md", ws_md, True))

    for canon, candidates in [
        ("USER.md", ("USER.md", "user.md")),
    ]:
        found = _resolve_case_insensitive_file(ws, *candidates)
        specs.append((canon, found if found else (ws / candidates[0]).resolve(), True))

    # CONFIG.md：Agent 配置（身份 + 人格 + 行为规则合一），仅 Agent 目录（agents/{id}/）
    if agent_id:
        agent_dir = _resolve_agent_dir(ws, agent_id)
        cfg = _resolve_case_insensitive_file(agent_dir, "CONFIG.md", "config.md")
        specs.append(("CONFIG.md", cfg if cfg else (agent_dir / "CONFIG.md").resolve(), True))

    # 工作空间级引导：WORKSPACE_BOOTSTRAP.md 仅当存在时加入
    ws_rit = _resolve_workspace_ritual_path(ws)
    if ws_rit:
        specs.append((ws_rit.name, ws_rit, True))

    # —— Agent 子目录（通过 _resolve_agent_dir 定位） ——
    if agent_id:
        agent_dir = _resolve_agent_dir(ws, agent_id)

        # MEMORY.md：永久记忆（agents/{id}/memory/），自动管理
        memory_file = agent_dir / "memory" / "MEMORY.md"
        specs.append(("MEMORY.md", memory_file, False))

        # Agent 级引导：AGENT_BOOTSTRAP.md 仅当存在时加入
        ag_rit = _resolve_agent_ritual_path(agent_dir)
        if ag_rit:
            specs.append((ag_rit.name, ag_rit, True))

    return specs


def _read_text_safe(path: Path) -> tuple[bool, str]:
    """安全读取文本文件。

    Args:
        path: 文件路径

    Returns:
        (ok, content) 元组；非文件或 OSError 时 ok=False、content=""
    """
    try:
        if not path.is_file():
            return False, ""
        return True, path.read_text(encoding="utf-8")
    except OSError:
        return False, ""


def read_user_timezone_iana(workspace_dir: Path | str, agent_id: Optional[str] = None) -> Optional[str]:
    """从 ``USER.md`` 的 ``- **时区：**`` 行读取 IANA 时区；优先 ``agents/<id>/USER.md``，其次工作空间根 ``USER.md``。

    与 aion 引导约定一致（见 ``_extract_md_field_value``）；不在 ``aion.json`` 中配置时区。

    Args:
        workspace_dir: 工作空间根目录
        agent_id: 可选 Agent ID，用于优先读取 Agent 目录下 USER.md

    Returns:
        合法的 IANA 时区名（如 Asia/Shanghai）；未配置或无效时返回 None
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    ws = Path(workspace_dir).resolve()
    ordered: list[Path] = []
    if agent_id:
        ad = _resolve_agent_dir(ws, agent_id)
        u_agent = _resolve_case_insensitive_file(ad, "USER.md", "user.md")
        if u_agent:
            ordered.append(u_agent)
    u_ws = _resolve_case_insensitive_file(ws, "USER.md", "user.md")
    if u_ws and u_ws not in ordered:
        ordered.append(u_ws)

    for path in ordered:
        ok, txt = _read_text_safe(path)
        if not ok or not txt.strip():
            continue
        tz_v = _extract_md_field_value(txt, "时区").strip()
        if not tz_v:
            m_en = re.search(r"^- \*\*Timezone:\*\*\s*(.+?)\s*$", txt, flags=re.MULTILINE)
            tz_v = m_en.group(1).strip() if m_en else ""
        if not _is_effective_value(tz_v):
            continue
        raw = tz_v.strip().strip("`\"' ").split()[0]
        try:
            ZoneInfo(raw)
            return raw
        except ZoneInfoNotFoundError:
            continue
    return None


def collect_workspace_bootstrap_files(
    workspace_dir: Path | str,
    agent_id: Optional[str] = None,
) -> list[WorkspaceBootstrapFile]:
    """扫描工作空间与 Agent 目录，生成 WorkspaceBootstrapFile 列表（未做预算截断）。

    Args:
        workspace_dir: 工作空间根目录
        agent_id: 可选 Agent ID

    Returns:
        磁盘上各 bootstrap 文件的元数据列表
    """
    ws = Path(workspace_dir)
    out: list[WorkspaceBootstrapFile] = []
    for canon_name, abs_path, _ in _collect_bootstrap_file_specs(ws, agent_id):
        ok, body = _read_text_safe(abs_path)
        exists = ok and abs_path.is_file()
        out.append(
            WorkspaceBootstrapFile(
                name=canon_name,
                path=str(abs_path),
                content=body if exists else None,
                missing=not exists,
            )
        )
    return out


def _trim_bootstrap_content(content: str, file_name: str, max_chars: int) -> tuple[str, bool, int]:
    """头 70% + 尾 20%，中间插入截断说明。

    Args:
        content: 原始正文
        file_name: 展示用文件名
        max_chars: 单文件字符预算

    Returns:
        (trimmed_text, was_truncated, original_length)
    """
    trimmed = content.rstrip()
    if len(trimmed) <= max_chars:
        return trimmed, False, len(trimmed)
    head_chars = int(max_chars * BOOTSTRAP_HEAD_RATIO)
    tail_chars = int(max_chars * BOOTSTRAP_TAIL_RATIO)
    head = trimmed[:head_chars]
    tail = trimmed[-tail_chars:]
    marker = (
        f"\n[...truncated, read {file_name} for full content...]\n"
        f"…(truncated {file_name}: kept {head_chars}+{tail_chars} chars of {len(trimmed)})…\n"
    )
    return head + marker + tail, True, len(trimmed)


def _clamp_to_budget(content: str, budget: int) -> str:
    """将 content 截断到 budget 字符以内。

    Args:
        content: 原始文本
        budget: 最大字符数

    Returns:
        截断后的文本；budget<=0 时返回空串
    """
    if budget <= 0:
        return ""
    if len(content) <= budget:
        return content
    if budget <= 3:
        return content[:budget]
    return content[: budget - 1] + "…"


def build_bootstrap_context_files(
    files: list[WorkspaceBootstrapFile],
    *,
    max_chars: int = DEFAULT_BOOTSTRAP_MAX_CHARS,
    total_max_chars: int = DEFAULT_BOOTSTRAP_TOTAL_MAX_CHARS,
    warn: Optional[Callable[[str], None]] = None,
) -> list[EmbeddedContextFile]:
    """将 WorkspaceBootstrapFile 转为带预算的 EmbeddedContextFile 列表。

    Args:
        files: 原始 bootstrap 文件列表
        max_chars: 单文件最大字符
        total_max_chars: 全部文件合计最大字符
        warn: 可选警告回调

    Returns:
        截断后的 EmbeddedContextFile 列表
    """
    remaining = max(1, int(total_max_chars))
    result: list[EmbeddedContextFile] = []

    for file in files:
        if remaining <= 0:
            break
        path_value = (file.path or "").strip()
        if not path_value:
            if warn:
                warn(f'skipping bootstrap file "{file.name}" — missing path')
            continue

        if file.missing or file.content is None:
            missing_text = f"[MISSING] Expected at: {path_value}"
            capped = _clamp_to_budget(missing_text, remaining)
            if not capped:
                break
            remaining = max(0, remaining - len(capped))
            result.append(EmbeddedContextFile(path=path_value, content=capped))
            continue

        if remaining < MIN_BOOTSTRAP_FILE_BUDGET_CHARS:
            if warn:
                warn(
                    f"remaining bootstrap budget is {remaining} chars (<{MIN_BOOTSTRAP_FILE_BUDGET_CHARS}); "
                    "skipping additional bootstrap files"
                )
            break

        file_max = max(1, min(max_chars, remaining))
        trimmed, truncated, orig_len = _trim_bootstrap_content(file.content, file.name, file_max)
        capped = _clamp_to_budget(trimmed, remaining)
        if not capped:
            continue
        if truncated or len(capped) < len(trimmed):
            if warn:
                warn(
                    f"workspace bootstrap file {file.name} is {orig_len} chars (limit {file_max}); "
                    "truncating in injected context"
                )
        remaining = max(0, remaining - len(capped))
        result.append(EmbeddedContextFile(path=path_value, content=capped))

    return result


def get_bootstrap_file_status(workspace_dir: Path, agent_id: Optional[str] = None) -> dict[str, bool]:
    """返回 (workspace_pending, agent_pending) — 仅凭仪式 .md 文件存在性判定。

    Args:
        workspace_dir: 工作空间根目录
        agent_id: 可选 Agent ID

    Returns:
        dict 含 workspace_pending、agent_pending 两个 bool 键
    """
    ws = Path(workspace_dir).resolve()
    workspace_pending = _resolve_workspace_ritual_path(ws) is not None

    agent_pending = False
    if agent_id:
        ad = _resolve_agent_dir(ws, agent_id)
        agent_pending = _resolve_agent_ritual_path(ad) is not None

    return {
        "workspace_pending": workspace_pending,
        "agent_pending": agent_pending,
    }
