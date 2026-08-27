"""M4 内置工具（read / write / ls）单元测试

验证 read、write、ls 在正常与异常路径下的返回内容。
"""

from pathlib import Path
import sys

import pytest

# 将项目 src 加入导入路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aion.tools.builtin.read import read_impl as read_tool
from aion.tools.builtin.write import write_tool
from aion.tools.builtin.ls import ls as _ls_tool
from aion.core.context import current_workspace

ls_tool = _ls_tool.func


class TestReadTool:
    """read_tool 文件读取测试"""

    def test_read_file(self, tmp_path):
        """读取存在的文件应包含文件正文

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!")

        result = read_tool(str(test_file))
        assert "Hello, World!" in result

    def test_read_nonexistent(self):
        """读取不存在路径应返回错误提示

        Returns:
            None
        """
        result = read_tool("/nonexistent/file.txt")
        assert "不存在" in result or "not exist" in result.lower()

    def test_read_file_contents(self, tmp_path):
        """多行文件应能读出各行内容

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        test_file = tmp_path / "multiline.txt"
        test_file.write_text("Line 1\nLine 2\nLine 3\nLine 4\nLine 5")

        result = read_tool(str(test_file))
        assert "Line 1" in result
        assert "Line 3" in result


class TestWriteTool:
    """write_tool 文件写入测试"""

    @pytest.fixture(autouse=True)
    def setup_ctx(self, tmp_path):
        token = current_workspace.set(tmp_path)
        yield
        current_workspace.reset(token)

    def test_write_file(self, tmp_path):
        """写入后磁盘文件内容与参数一致

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        test_file = tmp_path / "output.txt"
        write_tool(str(test_file), "Hello, Write!")

        assert test_file.exists()
        assert test_file.read_text() == "Hello, Write!"

    def test_write_no_content(self, tmp_path):
        """空内容写入仍应创建文件

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        test_file = tmp_path / "empty.txt"
        write_tool(str(test_file), "")
        assert test_file.exists()


class TestLsTool:
    """ls_tool 目录列表测试"""

    def test_list_directory(self, tmp_path):
        """列出目录应包含文件与子目录名

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        (tmp_path / "file1.txt").write_text("content")
        (tmp_path / "file2.txt").write_text("content")
        (tmp_path / "subdir").mkdir()

        result = ls_tool(str(tmp_path))
        assert "file1.txt" in result
        assert "file2.txt" in result
        assert "subdir" in result

    def test_list_nonexistent_directory(self):
        """列出不存在的目录应返回错误提示

        Returns:
            None
        """
        result = ls_tool("/nonexistent/directory")
        assert "不存在" in result or "not exist" in result.lower()
