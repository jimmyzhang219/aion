"""apply_patch 工具模块

解析并应用 git unified diff 格式的补丁（依赖 whatthepatch），
支持新增、修改、删除文件，路径须为工作空间内相对路径。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from whatthepatch import apply_diff, parse_patch

from aion.core.context import current_workspace
from aion.log import get_logger

logger = get_logger(__name__)


class ApplyPatchTool:
    """Unified diff 补丁应用工具

    将 git diff 文本解析为多段 diff，逐段应用到工作空间内文件。

    Attributes:
        workspace_root: 工作空间根目录
    """

    def __init__(self, workspace_root: Path):
        """初始化 ApplyPatchTool

        Args:
            workspace_root: 工作空间根目录，patch 内路径相对此根
        """
        self.workspace_root = workspace_root.resolve()

    @staticmethod
    def _strip_git_prefix(path: str) -> str:
        """去除 git diff 路径前缀 a/ 或 b/

        Args:
            path: diff header 中的路径字符串

        Returns:
            去除前缀后的路径；/dev/null 保持不变
        """
        p = path.strip().strip('"')
        if p in ("/dev/null", "dev/null"):
            return "/dev/null"
        for pref in ("a/", "b/"):
            if p.startswith(pref):
                return p[2:]
        return p

    def _safe_rel(self, rel: str) -> Path:
        """将相对路径解析为工作空间内的绝对 Path

        Args:
            rel: patch 中的相对路径

        Returns:
            解析后的绝对文件路径

        Raises:
            ValueError: 路径为 /dev/null、绝对路径或逃出工作空间
        """
        if rel in ("/dev/null", "dev/null"):
            raise ValueError("无效的单文件路径 /dev/null")
        p = Path(rel)
        if p.is_absolute():
            raise ValueError("patch 中路径必须为相对路径")
        full = (self.workspace_root / p).resolve()
        full.relative_to(self.workspace_root)
        return full

    def apply_patch(self, patch: str, **_: Any) -> str:
        """应用 unified diff 补丁文本

        Args:
            patch: git unified diff 或 git diff 格式字符串

        Returns:
            每段 diff 的操作报告，多行拼接
        """
        text = (patch or "").strip()
        if not text:
            logger.warning("[apply_patch] patch 为空")
            return "错误：patch 不能为空（git unified diff 文本）"

        diffs = list(parse_patch(text))
        if not diffs:
            logger.warning("[apply_patch] patch 解析失败")
            return "错误：无法解析为可用 patch（需 unified diff / git diff 格式）"

        reports: list[str] = []
        for diff in diffs:
            old_rel = self._strip_git_prefix(diff.header.old_path)
            new_rel = self._strip_git_prefix(diff.header.new_path)
            # 删除操作以 old_path 为目标；否则以 new_path
            rel_target = new_rel if new_rel != "/dev/null" else old_rel
            if rel_target == "/dev/null":
                reports.append("跳过：无法解析目标路径")
                continue

            # 通过 diff 文本中的 /dev/null 标记判断增删改类型
            is_delete = "+++ /dev/null" in diff.text
            is_new = "--- /dev/null" in diff.text

            try:
                target = self._safe_rel(rel_target)
            except ValueError as e:
                logger.warning("[apply_patch] 拒绝 %s: %s", rel_target, e)
                reports.append(f"拒绝：{rel_target} — {e}")
                continue

            if is_delete:
                if not target.is_file():
                    reports.append(f"跳过删除（文件不存在）{rel_target}")
                    continue
                old_content = target.read_text(encoding="utf-8", errors="replace")
                try:
                    apply_diff(diff, old_content)
                except Exception as e:
                    logger.warning("[apply_patch] 失败 %s: %s", rel_target, e)
                    reports.append(f"失败 {rel_target}: {e}")
                    continue
                target.unlink()
                reports.append(f"已删除 {rel_target}")
                continue

            if is_new:
                old_content = ""
            else:
                if not target.is_file():
                    logger.warning("[apply_patch] 失败 %s: 文件不存在", rel_target)
                    reports.append(f"失败 {rel_target}: 文件不存在，无法应用 patch")
                    continue
                old_content = target.read_text(encoding="utf-8", errors="replace")

            try:
                new_lines = apply_diff(diff, old_content)
            except Exception as e:
                logger.warning("[apply_patch] 失败 %s: %s", rel_target, e)
                reports.append(f"失败 {rel_target}: {e}")
                continue

            out = "\n".join(new_lines)
            if new_lines and not out.endswith("\n"):
                out += "\n"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(out, encoding="utf-8")
            reports.append(f"已写入 {rel_target}")

        return "\n".join(reports)


@tool(parse_docstring=True)
def apply_patch(patch: str) -> str:
    """应用 git unified diff 格式的补丁到多个文件。
    输入必须包含 unified diff 格式的完整补丁文本。
    适用于多文件修改场景，如代码审查后的批量修改。

    Args:
        patch: 完整 unified diff 格式的补丁文本
    """
    ws = current_workspace.get()
    tool = ApplyPatchTool(workspace_root=ws)
    return tool.apply_patch(patch)
