"""grep / find / apply_patch / web_fetch 内置工具单元测试

测试 GrepTool 正则匹配、FindTool glob、ApplyPatchTool 应用与路径逃逸拒绝，
以及 web_fetch 通过 mock httpx 拉取页面正文。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


# 将项目 src 加入导入路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aion.tools.builtin.apply_patch_tool import ApplyPatchTool
from aion.tools.builtin.grep_find import FindTool, GrepTool
from aion.tools.builtin.web_tools import web_fetch_impl
from aion.tools.builtin import web_tools


def test_grep_matches(tmp_path: Path):
    """grep 应在匹配行中返回文件路径与命中内容

    Args:
        tmp_path: pytest 临时目录（Path）

    Returns:
        None
    """
    (tmp_path / "a.py").write_text("foo = 1\nbar = 2\n", encoding="utf-8")
    g = GrepTool(tmp_path)
    r = g.grep(r"bar", path=".")
    assert "a.py" in r
    assert "bar" in r


def test_find_glob(tmp_path: Path):
    """find 使用 **/*.py 应列出子目录中的 Python 文件

    Args:
        tmp_path: pytest 临时目录（Path）

    Returns:
        None
    """
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "a.py").write_text("1", encoding="utf-8")
    (tmp_path / "b.md").write_text("1", encoding="utf-8")
    f = FindTool(tmp_path)
    r = f.find("**/*.py", path=".")
    assert "x/a.py" in r.replace("\\", "/")


def test_apply_patch_update(tmp_path: Path):
    """标准 unified diff 应能就地更新文件内容

    Args:
        tmp_path: pytest 临时目录（Path）

    Returns:
        None
    """
    (tmp_path / "f.txt").write_text("line1\nold\n", encoding="utf-8")
    patch = """diff --git a/f.txt b/f.txt
--- a/f.txt
+++ b/f.txt
@@ -1,2 +1,2 @@
 line1
-old
+new
"""
    t = ApplyPatchTool(tmp_path)
    msg = t.apply_patch(patch)
    assert "已写入" in msg or "f.txt" in msg
    assert (tmp_path / "f.txt").read_text() == "line1\nnew\n"


def test_apply_patch_rejects_escape(tmp_path: Path):
    """试图 patch 工作区外路径时应拒绝

    Args:
        tmp_path: pytest 临时目录（Path）

    Returns:
        None
    """
    t = ApplyPatchTool(tmp_path)
    patch = """diff --git a/../outside.txt b/../outside.txt
--- a/../outside.txt
+++ b/../outside.txt
@@ -1 +1 @@
-a
+b
"""
    msg = t.apply_patch(patch)
    assert "拒绝" in msg or "错误" in msg or "无效" in msg


@patch.object(web_tools.httpx, "Client")
def test_web_fetch_uses_httpx(mock_client_cls: MagicMock):
    """web_fetch 应通过 httpx Client 发起 GET 并返回响应文本

    Args:
        mock_client_cls: 被 patch 的 httpx.Client 类（MagicMock）

    Returns:
        None
    """
    mock_resp = MagicMock()
    mock_resp.headers = {"content-type": "text/plain"}
    mock_resp.text = "hello world"
    inst = MagicMock()
    inst.get.return_value = mock_resp
    inst.__enter__.return_value = inst
    inst.__exit__.return_value = None
    mock_client_cls.return_value = inst

    out = web_fetch_impl("https://example.com/test", max_chars=1000)
    assert "hello world" in out
    inst.get.assert_called_once()
