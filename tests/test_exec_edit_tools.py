"""exec / edit 内置工具单元测试

测试 EditTool 唯一替换、非唯一拒绝、路径必须在工作区内；
ExecTool 同步 echo、后台 sleep 的 list/poll/kill 流程。
"""

from __future__ import annotations

import sys
from pathlib import Path


# 将项目 src 加入导入路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aion.tools.builtin.edit import EditTool
from aion.tools.builtin.exec import ExecTool


def test_edit_unique_replace(tmp_path: Path):
    """oldText 唯一出现时应成功替换并写回磁盘

    Args:
        tmp_path: pytest 临时目录（Path）

    Returns:
        None
    """
    f = tmp_path / "a.txt"
    f.write_text("hello world\n", encoding="utf-8")
    tool = EditTool(tmp_path)
    r = tool.edit(str(f), [{"oldText": "world", "newText": "there"}])
    assert "已写入" in r
    assert f.read_text() == "hello there\n"


def test_edit_rejects_non_unique(tmp_path: Path):
    """oldText 多次出现时应拒绝编辑

    Args:
        tmp_path: pytest 临时目录（Path）

    Returns:
        None
    """
    f = tmp_path / "b.txt"
    f.write_text("x x\n", encoding="utf-8")
    tool = EditTool(tmp_path)
    r = tool.edit(str(f), [{"oldText": "x", "newText": "y"}])
    assert "错误" in r


def test_edit_path_outside_workspace(tmp_path: Path):
    """工作区外路径应允许（无路径限制）

    Args:
        tmp_path: pytest 临时目录（Path）

    Returns:
        None
    """
    tool = EditTool(tmp_path)
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("a", encoding="utf-8")
    r = tool.edit(str(outside), [{"oldText": "a", "newText": "b"}])
    assert "成功" in r or "已写入" in r


def test_exec_echo(tmp_path: Path):
    """同步 exec 应返回 stdout 与 exit code 信息

    Args:
        tmp_path: pytest 临时目录（Path）

    Returns:
        None
    """
    ex = ExecTool(tmp_path, default_timeout_sec=10.0)
    r = ex.exec('printf "ok"', workdir=str(tmp_path))
    assert "ok" in r
    assert "exit code" in r


def test_exec_background_poll_kill(tmp_path: Path):
    """后台任务应可 list、poll，并最终 kill

    Args:
        tmp_path: pytest 临时目录（Path）

    Returns:
        None
    """
    ex = ExecTool(tmp_path, default_timeout_sec=10.0)
    r = ex.exec("sleep 30", background=True)
    assert "sessionId=" in r
    sid = r.split("sessionId=")[1].split()[0]
    listed = ex.process(action="list")
    assert sid in listed
    pol = ex.process(action="poll", sessionId=sid)
    assert "running" in pol or "no output" in pol.lower()
    killed = ex.process(action="kill", sessionId=sid)
    assert "终止" in killed or "已" in killed
