"""Write 工具 - 文件写入

本模块提供文件写入功能。

核心设计理念：
- LLM 通过 System Prompt 中的工具描述自主决定何时调用 write
- 无特殊拦截，LLM 根据任务自主决定调用 write
- 支持两种模式：普通写入和 Memory Flush 模式

两种工作模式：

1. 普通模式（默认）：
   - 可以写入工作空间内的任意文件
   - 路径安全检查：防止写入 workspace 外部
   - 用于：通用文件写入、记忆保存等

2. Memory Flush 模式（Compaction 前调用）：
   - 只能追加内容到指定的记忆文件
   - 其他路径的写入请求会被拒绝
   - 用于：上下文窗口快满时，LLM 将重要记忆追加到日记文件

使用示例：
    # 普通写入
    write("notes.md", "# 我的笔记")

    # Memory Flush 模式（只能追加到 memory/YYYY-MM-DD.md）
    from pathlib import Path
    tool = WriteTool(
        workspace_root=Path("~/.aion/workspaces/default"),
        memory_flush_path="memory/2026-04-19.md"
    )
    tool.write("any_path.txt", "内容")  # 会被拒绝，只能写入 memory_flush_path
"""

from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

from aion.core.context import current_workspace
from aion.log import get_logger

logger = get_logger(__name__)


class WriteTool:
    """
    Write 工具类

    支持普通模式和 Memory Flush 模式的文件写入。

    Attributes:
        workspace_root: 工作空间根目录，用于路径安全检查
        memory_flush_path: Memory Flush 模式下允许写入的相对路径
        _in_flush_mode: 是否处于 Memory Flush 模式

    Memory Flush 模式说明：
        当上下文窗口快满时，会触发 Memory Flush。
        此时 write 工具会被"包装"成只能追加到指定文件的版本。
        这是为了确保 LLM 在 silent turn 中只将重要记忆追加到日记，
        而不是写入其他文件污染工作空间。
    """

    def __init__(
        self,
        workspace_root: Path,
        memory_flush_path: Optional[str] = None,
    ):
        """
        初始化 WriteTool

        Args:
            workspace_root: 工作空间根目录
                           用于验证相对路径和防止路径遍历攻击
            memory_flush_path: Memory Flush 模式下允许的写入路径
                              格式为相对于 workspace_root 的路径
                              如 "memory/2026-04-19.md"
                              当此参数不为 None 时，开启 Memory Flush 模式
        """
        self.workspace_root = workspace_root
        self.memory_flush_path = memory_flush_path  # 如 "memory/YYYY-MM-DD.md"
        # memory_flush_path 非空即进入 Compaction 前仅允许追加日记的模式
        self._in_flush_mode = memory_flush_path is not None

    def _resolve_path(self, file_path: str) -> Path:
        """
        解析文件路径

        支持两种路径格式：
        1. 绝对路径：直接使用，如 /tmp/test.txt
        2. 相对路径：基于 workspace_root，如 memory/test.md

        还支持 ~ 展开为用户主目录。

        Args:
            file_path: 文件路径字符串

        Returns:
            解析后的绝对 Path 对象

        Example:
            _resolve_path("memory/test.md")
            # -> Path("/home/user/.aion/workspaces/default/memory/test.md")

            _resolve_path("~/notes.txt")
            # -> Path("/home/user/notes.txt")
        """
        p = Path(file_path).expanduser()
        # 如果是相对路径，基于 workspace_root 解析
        if not p.is_absolute():
            p = self.workspace_root / p
        return p

    def _verify_path_safe(self, resolved_path: Path) -> None:
        """
        验证路径安全性，防止路径遍历攻击

        安全策略：
        1. 绝对路径在 workspace 外部时：允许（用于测试等场景）
        2. 相对路径：必须位于 workspace_root 内部

        路径遍历攻击示例：
            假设 workspace 是 /aion，攻击者尝试写入 /aion/../etc/passwd
            通过 relative_to() 检查可以发现 /etc 不在 /aion 下。

        Args:
            resolved_path: 已经 resolve() 的绝对路径

        Raises:
            ValueError: 当路径在 workspace 外部时抛出
        """
        # 绝对路径且在 workspace 外部 - 允许（如测试用的 tmp_path）
        # 这是合理的设计，因为测试临时文件应该在系统临时目录
        if resolved_path.is_absolute():
            try:
                resolved_path.resolve().relative_to(self.workspace_root.resolve())
            except ValueError:
                # 绝对路径在 workspace 外部 - 允许
                return

        # 相对路径 - 检查是否在 workspace 内
        try:
            resolved_path.resolve().relative_to(self.workspace_root.resolve())
        except ValueError:
            raise ValueError(f"Path is outside workspace: {resolved_path}")

    def write(self, file_path: str, content: str) -> str:
        """
        写入文件内容

        根据工作模式不同，行为也不同：

        1. 普通模式：
           - 将 content 写入指定文件
           - 文件不存在时创建，存在时覆盖

        2. Memory Flush 模式：
           - 只能写入 memory_flush_path 指定的文件
           - 其他路径的写入请求被拒绝并返回错误信息
           - 以追加模式写入（不是覆盖）
           - 这是为了保留原有的日记内容

        Args:
            file_path: 文件路径（绝对路径或相对于 workspace_root）
            content: 要写入的内容

        Returns:
            操作结果字符串：
            - 成功：写入成功/已追加内容
            - 失败：路径安全检查失败/Memory Flush 模式拒绝/其他错误
        """
        try:
            resolved_path = self._resolve_path(file_path)

            # Memory Flush 模式检查
            if self.memory_flush_path is not None:
                # 在 Memory Flush 模式下，只能写入指定文件
                allowed_path = self.workspace_root / self.memory_flush_path

                # 检查目标路径是否匹配允许的路径
                if resolved_path.resolve() != allowed_path.resolve():
                    logger.warning("[write] 拒绝: Memory Flush 模式禁止写入 %s", file_path)
                    return f"[Memory Flush 模式] 只能写入 {self.memory_flush_path}，拒绝写入 {file_path}"

                # 追加模式：打开文件并追加内容
                # 使用 'a' 模式而不是 'w' 模式，保留原有内容
                allowed_path.parent.mkdir(parents=True, exist_ok=True)
                with open(allowed_path, "a") as f:
                    f.write(content)
                return f"[Memory Flush] 已追加内容到 {self.memory_flush_path}"

            # 普通模式：直接写入文件
            self._verify_path_safe(resolved_path)
            resolved_path.parent.mkdir(parents=True, exist_ok=True)
            with open(resolved_path, "w") as f:
                f.write(content)
            return f"写入成功: {file_path}"

        except ValueError as e:
            logger.error("[write] 路径安全检查失败: %s", e)
            # 路径安全检查失败
            return f"路径安全检查失败: {e}"
        except Exception as e:
            logger.error("[write] 写入失败: %s", e)
            # 其他写入错误
            return f"写入失败: {e}"

    def append(self, file_path: str, content: str) -> str:
        """
        追加内容到文件

        与 write() 不同，此方法始终以追加模式写入。
        适用于：日志追加、记忆追加等场景。

        Args:
            file_path: 文件路径
            content: 要追加的内容

        Returns:
            操作结果字符串
        """
        try:
            resolved_path = self._resolve_path(file_path)
            self._verify_path_safe(resolved_path)
            resolved_path.parent.mkdir(parents=True, exist_ok=True)
            with open(resolved_path, "a") as f:
                f.write(content)
            return f"追加成功: {file_path}"
        except ValueError as e:
            logger.error("[write] 路径安全检查失败: %s", e)
            return f"路径安全检查失败: {e}"
        except Exception as e:
            logger.error("[write] 追加失败: %s", e)
            return f"追加失败: {e}"


# ============================================================================
# 全局单例模式
# ============================================================================

# 全局 WriteTool 实例（普通模式）
# 使用单例模式避免重复创建实例
_write_tool: Optional[WriteTool] = None


def _get_write_tool() -> WriteTool:
    """
    获取全局 WriteTool 单例实例

    Returns:
        普通模式的 WriteTool 实例

    Note:
        workspace_root 从 current_workspace ContextVar 动态获取。
    """
    global _write_tool
    if _write_tool is None:
        ws = current_workspace.get()
        _write_tool = WriteTool(workspace_root=ws)
    return _write_tool


# ============================================================================
# 工具函数接口（供 Agent 调用）
# ============================================================================


def write_tool(path: str, content: str) -> str:
    """
    写入文件内容（全局实例）

    这是 LLM 实际调用的入口函数。
    使用全局单例实例，适用于普通写入场景。

    Args:
        path: 文件路径（绝对路径或相对于 workspace_root）
        content: 要写入的内容

    Returns:
        操作结果字符串
    """
    tool = _get_write_tool()
    return tool.write(path, content)


@tool(parse_docstring=True)
def write(path: str, content: str) -> str:
    """写入内容到文件。文件不存在则创建，存在则覆盖。自动创建父目录。
    支持绝对路径和相对工作空间的路径。
    如需将内容保存到永久记忆，使用 memory_write 而不是此工具。

    Args:
        path: 文件路径（绝对路径或相对于工作空间）
        content: 要写入的文件内容
    """
    ws = current_workspace.get()
    tool = WriteTool(workspace_root=ws)
    return tool.write(path, content)
