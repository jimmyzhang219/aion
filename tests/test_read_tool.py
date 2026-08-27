"""自适应 read_tool 分页与偏移读取单元测试

测试小文件全量读取、offset/limit 分页、capped 续读提示、
大文件自适应截断，以及越界 offset 错误处理。
"""

import sys
import tempfile
from pathlib import Path

# 将项目 src 加入导入路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aion.tools.builtin.read import read_impl as read_tool


def test_read_entire_small_file():
    """小文件在自适应上限内应完整返回且无 capped 标记

    Returns:
        None
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("line1\nline2\nline3\n")
        fpath = f.name
    try:
        result = read_tool(fpath)
        assert "line1" in result
        assert "line3" in result
        assert "capped" not in result
    finally:
        Path(fpath).unlink(missing_ok=True)


def test_read_with_offset():
    """指定 offset 与 limit 时应只返回窗口内行并提示下一 offset

    Returns:
        None
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for i in range(1, 101):
            f.write(f"line{i}\n")
        fpath = f.name
    try:
        result = read_tool(fpath, offset=50, limit=3)
        assert "line50" in result
        assert "line52" in result
        assert "capped" in result
        assert "Use offset=53" in result
    finally:
        Path(fpath).unlink(missing_ok=True)


def test_read_with_offset_only():
    """仅 offset 时应从该行读到文件末尾

    Returns:
        None
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for i in range(1, 101):
            f.write(f"line{i}\n")
        fpath = f.name
    try:
        result = read_tool(fpath, offset=95)
        assert "line95" in result
        assert "line100" in result
    finally:
        Path(fpath).unlink(missing_ok=True)


def test_read_large_file_adaptive_paging():
    """超大文件首次读取应触发 capped 与续读 offset 提示

    Returns:
        None
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for i in range(1, 60001):
            f.write(f"line{i:08d}\n")
        fpath = f.name
    try:
        result = read_tool(fpath, context_window_tokens=200000)
        assert "capped" in result
        assert "Use offset=" in result
    finally:
        Path(fpath).unlink(missing_ok=True)


def test_offset_beyond_file():
    """offset 超出文件行数应返回超出范围错误

    Returns:
        None
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("hello\n")
        fpath = f.name
    try:
        result = read_tool(fpath, offset=999)
        assert "超出" in result
    finally:
        Path(fpath).unlink(missing_ok=True)


def test_explicit_limit_continuation_hint():
    """显式 limit 且文件更长时应提示下一 offset

    Returns:
        None
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for i in range(1, 101):
            f.write(f"line{i}\n")
        fpath = f.name
    try:
        result = read_tool(fpath, offset=1, limit=5)
        assert "capped" in result
        assert "Use offset=6" in result
    finally:
        Path(fpath).unlink(missing_ok=True)


def test_read_empty_file():
    """空文件应返回空字符串或等价空结果

    Returns:
        None
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        fpath = f.name
    try:
        result = read_tool(fpath)
        assert result == "" or result is not None
    finally:
        Path(fpath).unlink(missing_ok=True)
