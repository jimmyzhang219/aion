"""Edit 工具模块

在工作空间内对文本文件执行 oldText -> newText 片段替换，
所有 edits 均基于原文件匹配，从文件底部向上逐一替换，避免位置偏移。
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from aion.log import get_logger

logger = get_logger(__name__)

from langchain_core.tools import tool

from aion.core.context import current_workspace


class EditTool:
    """文本片段替换编辑工具

    在工作空间根目录约束下，对指定文件按 edits 列表依次替换，
    每段 oldText 必须在文件中唯一出现，否则拒绝写入。

    Attributes:
        workspace_root: 工作空间根目录（绝对路径）
    """

    def __init__(self, workspace_root: Path):
        """初始化 EditTool

        Args:
            workspace_root: 工作空间根目录，相对 path 基于此解析
        """
        self.workspace_root = workspace_root.resolve()

    def _resolve_path(self, path_str: str) -> Path:
        """将路径字符串解析为绝对 Path

        Args:
            path_str: 绝对或相对于 workspace_root 的路径

        Returns:
            解析后的绝对路径
        """
        p = Path(path_str).expanduser()
        if p.is_absolute():
            return p.resolve()
        return (self.workspace_root / p).resolve()

    def _verify_path_safe(self, path: Path) -> None:
        """验证路径安全性

        绝对路径在 workspace 外部时允许（如 Obsidian 知识库）。
        相对路径必须位于 workspace_root 内部。
        """
        if path.is_absolute():
            try:
                path.resolve().relative_to(self.workspace_root.resolve())
            except ValueError:
                return  # 绝对路径在 workspace 外部 - 允许
        try:
            path.resolve().relative_to(self.workspace_root.resolve())
        except ValueError:
            raise ValueError(f"路径必须位于工作空间内: {path}")

    def edit(self, path: str, edits: list[Any]) -> str:
        """对文件应用一组片段替换并写回

        Args:
            path: 目标文件路径（相对或绝对）
            edits: 替换列表，每项为含 oldText/newText（或 old_text/new_text）的字典

        Returns:
            成功时返回 unified diff 预览；失败时返回错误说明字符串
        """
        if not path or not str(path).strip():
            logger.warning("[edit] path 为空")
            return "错误：path 不能为空"
        if not edits:
            logger.warning("[edit] edits 为空列表")
            return "错误：edits 不能为空列表"

        abs_path = self._resolve_path(path)
        try:
            self._verify_path_safe(abs_path)
        except ValueError as e:
            logger.error("[edit] 路径安全检查失败: %s", e)
            return f"错误：{e}"
        if not abs_path.is_file():
            logger.warning("[edit] 文件不存在: %s", abs_path)
            return f"错误：文件不存在: {abs_path}"

        text = abs_path.read_text(encoding="utf-8", errors="replace")
        original = text

        # 第一轮：解析所有 edit 参数，在原文中定位
        positions: list[dict] = []  # [{idx, start, end, old, new, old_text, new_text}]
        for i, ed in enumerate(edits):
            if not isinstance(ed, dict):
                logger.warning("[edit] edits[%d] 不是对象", i)
                return f"错误：edits[{i}] 必须是对象（含 oldText/newText）"
            old = ed.get("oldText")
            new = ed.get("newText")
            if old is None or new is None:
                oalt = ed.get("old_text")
                nalt = ed.get("new_text")
                if oalt is not None and nalt is not None:
                    old, new = oalt, nalt
                else:
                    logger.warning("[edit] edits[%d] 缺少 oldText/newText", i)
                    return f"错误：edits[{i}] 缺少 oldText/newText"
            if not isinstance(old, str) or not isinstance(new, str):
                logger.warning("[edit] edits[%d] oldText/newText 非字符串", i)
                return f"错误：edits[{i}] 的 oldText/newText 须为字符串"
            if old == "":
                logger.warning("[edit] edits[%d] oldText 为空", i)
                return f"错误：edits[{i}] 的 oldText 不能为空（请用 write 创建文件）"

            # 在原文中定位（非增量匹配）
            start = original.find(old)
            if start == -1:
                logger.warning("[edit] edits[%d] oldText 未匹配: %.60s", i, old)
                return f"错误：edits[{i}] 在文件中未找到匹配的 oldText（唯一匹配要求）"
            # 确认唯一
            if original.find(old, start + 1) != -1:
                logger.warning("[edit] edits[%d] oldText 重复匹配", i)
                return f"错误：edits[{i}] 的 oldText 在文件中出现多次，请扩大上下文使唯一"
            positions.append(
                {
                    "idx": i,
                    "start": start,
                    "end": start + len(old),
                    "old": old,
                    "new": new,
                    "old_text": old,
                    "new_text": new,
                }
            )

        # 第二轮：检查重叠
        positions.sort(key=lambda p: p["start"])
        for a, b in zip(positions, positions[1:]):
            if a["end"] > b["start"]:
                logger.warning("[edit] edits 重叠: %d vs %d", a["idx"], b["idx"])
                return f"错误：edits[{a['idx']}] 与 edits[{b['idx']}] 的 oldText 重叠，请合并为一个 edit"

        # 第三轮：从文件底部向顶部依次替换，避免前面替换破坏后面位置
        text = original
        for p in sorted(positions, key=lambda p: p["start"], reverse=True):
            text = text[: p["start"]] + p["new"] + text[p["end"] :]

        abs_path.write_text(text, encoding="utf-8")
        diff = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                text.splitlines(keepends=True),
                fromfile=str(abs_path),
                tofile=str(abs_path),
                n=3,
            )
        )
        preview = diff if diff.strip() else "(内容未变)"
        # unified diff 过长时截断中间，保留首尾供 LLM 审阅
        if len(preview) > 50_000:
            preview = preview[:25_000] + "\n... [diff truncated] ...\n" + preview[-25_000:]
        # 小文件追加完整内容，避免 LLM 另发起 read 验证
        if text and len(text) < 800:
            return f"已写入 {abs_path}\n\n{preview}\n\n--- 当前文件完整内容 ---\n{text}"
        return f"已写入 {abs_path}\n\n{preview}"


@tool(parse_docstring=True)
def edit(path: str, edits: list[Any]) -> str:
    """用精确文本替换编辑单个文件，返回 unified diff 预览。
    所有 edits 均基于原文件匹配（不是增量应用），各 oldText 须唯一且不重叠。
    合并相邻修改为一个 edit，避免发出重叠的 edits。
    如果两个修改影响同一块或相邻行，合并为一个 edit，而不是发出重叠的 edits。

    Args:
        path: 文件路径（支持绝对路径和相对工作空间的路径）
        edits: 替换列表，每项含 oldText 与 newText。每个 oldText 在原文件中必须唯一
    """
    ws = current_workspace.get()
    tool = EditTool(workspace_root=ws)
    return tool.edit(path, edits)
