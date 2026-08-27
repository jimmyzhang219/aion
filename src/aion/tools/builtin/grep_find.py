"""grep / find 工具模块

工作区内内容正则搜索（grep）与路径 glob 查找（find），
仅使用标准库，对齐 pi 只读探查能力。
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from aion.core.context import current_workspace
from aion.log import get_logger

logger = get_logger(__name__)

# 遍历时跳过的目录名（降低噪音与扫描体积）
_SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        ".aion",
        ".idea",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
    }
)

# 单文件最大读取字节数（默认 256KB）
_MAX_FILE_BYTES_DEFAULT = 256 * 1024


class GrepTool:
    """工作区内正则内容搜索工具

    递归遍历目录，对文本文件逐行匹配 pattern，输出 相对路径:行号:内容。

    Attributes:
        workspace_root: 工作空间根目录
    """

    def __init__(self, workspace_root: Path):
        """初始化 GrepTool

        Args:
            workspace_root: 工作空间根目录，搜索结果路径相对此根
        """
        self.workspace_root = workspace_root.resolve()

    def _resolve_root(self, path: str | None) -> Path:
        """解析搜索根目录

        Args:
            path: 相对或绝对路径；None 或 "." 表示 workspace_root

        Returns:
            解析后的绝对搜索根路径
        """
        raw = (path or ".").strip() or "."
        p = Path(raw).expanduser()
        if p.is_absolute():
            return p.resolve()
        return (self.workspace_root / p).resolve()

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob_pattern: str | None = None,
        max_matches: int = 100,
        max_file_bytes: int = _MAX_FILE_BYTES_DEFAULT,
        **_: Any,
    ) -> str:
        """在工作区内按正则搜索文件内容

        Args:
            pattern: Python 正则表达式
            path: 搜索起始目录，默认 workspace 根
            glob_pattern: 可选文件名 glob 过滤（如 *.py）
            max_matches: 最大匹配行数（1–5000）
            max_file_bytes: 跳过超过此大小的文件

        Returns:
            匹配行列表（path:line:content）；无匹配时返回提示
        """
        if not (pattern or "").strip():
            logger.warning("[grep] pattern 为空")
            return "错误：pattern 不能为空"
        try:
            regex = re.compile(pattern)
        except re.error as e:
            logger.warning("[grep] 无效正则: %s", e)
            return f"错误：无效正则 {e}"

        try:
            root = self._resolve_root(path)
        except ValueError as e:
            logger.error("[grep] 执行失败: %s", e)
            return f"错误：{e}"

        max_m = max(1, min(int(max_matches or 100), 5000))
        max_bytes = max(1024, min(int(max_file_bytes or _MAX_FILE_BYTES_DEFAULT), 2 * 1024 * 1024))

        lines_out: list[str] = []
        n = 0
        for dirpath, dirnames, filenames in os.walk(root, topdown=True):
            # 原地过滤 dirnames，跳过常见构建/缓存目录
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES]
            for name in filenames:
                fp = Path(dirpath) / name
                if glob_pattern and not (
                    fnmatch.fnmatch(name, glob_pattern) or fnmatch.fnmatch(str(fp.relative_to(root)), glob_pattern)
                ):
                    continue
                try:
                    if fp.stat().st_size > max_bytes:
                        continue
                except OSError:
                    continue
                try:
                    data = fp.read_bytes()
                except OSError as e:
                    lines_out.append(f"# skip {fp}: {e}")
                    continue
                # 前 4KB 含 NUL 则视为二进制，跳过
                if b"\x00" in data[:4096]:
                    continue
                try:
                    text = data.decode("utf-8", errors="replace")
                except Exception:
                    continue
                for i, line in enumerate(text.splitlines(), start=1):
                    if regex.search(line):
                        try:
                            rel = fp.resolve().relative_to(self.workspace_root)
                        except ValueError:
                            rel = fp.resolve()
                        lines_out.append(f"{rel}:{i}:{line[:500]}")
                        n += 1
                        if n >= max_m:
                            lines_out.append(f"... 已达 max_matches={max_m}，请缩小范围或提高精确度")
                            return "\n".join(lines_out)
        return "\n".join(lines_out) if lines_out else "（无匹配）"


class FindTool:
    """工作区内路径 glob 查找工具

    使用 pathlib glob（支持 ** 递归），返回相对工作空间的路径列表。

    Attributes:
        workspace_root: 工作空间根目录
    """

    def __init__(self, workspace_root: Path):
        """初始化 FindTool

        Args:
            workspace_root: 工作空间根目录
        """
        self.workspace_root = workspace_root.resolve()

    def _resolve_root(self, path: str | None) -> Path:
        """解析查找根目录

        Args:
            path: 相对或绝对路径；None 或 "." 表示 workspace_root

        Returns:
            解析后的绝对查找根路径
        """
        raw = (path or ".").strip() or "."
        p = Path(raw).expanduser()
        if p.is_absolute():
            return p.resolve()
        return (self.workspace_root / p).resolve()

    def find(
        self,
        pattern: str,
        path: str | None = None,
        max_files: int = 500,
        **_: Any,
    ) -> str:
        """按 glob 模式查找文件

        Args:
            pattern: glob 模式，相对 root，可用 ** 递归（如 **/*.py）
            path: 查找起始目录
            max_files: 最多返回文件数（1–10000）

        Returns:
            路径列表，每行一个；无匹配时返回提示
        """
        if not (pattern or "").strip():
            logger.warning("[find] pattern 为空")
            return "错误：pattern 不能为空（例如 **/*.py）"
        try:
            root = self._resolve_root(path)
        except ValueError as e:
            logger.error("[find] 执行失败: %s", e)
            return f"错误：{e}"

        max_n = max(1, min(int(max_files or 500), 10_000))
        out: list[str] = []
        try:
            it = root.glob(pattern)
        except Exception as e:
            logger.warning("[find] glob 无效: %s", e)
            return f"错误：glob 无效 {e}"

        n = 0
        for fp in sorted(it):
            try:
                fp.relative_to(self.workspace_root)
            except ValueError:
                out.append(str(fp.resolve()))
                n += 1
                if n >= max_n:
                    out.append(f"... 已达 max_files={max_n}")
                    break
                continue
            if any(x in _SKIP_DIR_NAMES for x in fp.parts):
                continue
            if fp.is_file():
                out.append(str(fp.resolve().relative_to(self.workspace_root)))
                n += 1
                if n >= max_n:
                    out.append(f"... 已达 max_files={max_n}")
                    break
        return "\n".join(out) if out else "（无匹配文件）"


@tool(parse_docstring=True)
def grep(
    pattern: str,
    path: str | None = None,
    glob_pattern: str | None = None,
    max_matches: int = 100,
    max_file_bytes: int = _MAX_FILE_BYTES_DEFAULT,
) -> str:
    """在工作空间内用正则搜索文件内容。自动跳过 .git/.venv 等目录。
    当需要查找包含特定模式或关键词的代码、配置或文本时使用。
    如需按文件名查找而非内容，使用 find。如需读取文件内容，使用 read。

    Args:
        pattern: 正则表达式（Python re 语法）
        path: 起始目录，默认工作区根
        glob_pattern: 可选，仅匹配文件名或相对路径的 glob，如 *.py
        max_matches: 最多命中条数，默认 100
        max_file_bytes: 跳过的单文件最大字节，默认 256KB
    """
    ws = current_workspace.get()
    tool = GrepTool(workspace_root=ws)
    return tool.grep(
        pattern, path=path, glob_pattern=glob_pattern, max_matches=max_matches, max_file_bytes=max_file_bytes
    )


@tool(parse_docstring=True)
def find(
    pattern: str,
    path: str | None = None,
    max_files: int = 500,
) -> str:
    """在工作空间内按 glob 模式列出文件路径。当需要查找符合命名模式的文件时使用。
    如需搜索文件内容而非文件名，使用 grep。

    Args:
        pattern: glob 模式，相对 path，如 **/*.md
        path: 起始目录，默认工作区根
        max_files: 最多返回文件数，默认 500
    """
    ws = current_workspace.get()
    tool = FindTool(workspace_root=ws)
    return tool.find(pattern, path=path, max_files=max_files)
