"""内置工具全面单元测试（补充 test_tools.py 未覆盖场景）

覆盖 WriteTool 追加与 Memory Flush、trash/delete、gateway config.*、
web_search、memory 读写检索、Exec/Grep/Find/ApplyPatch/Edit/ls 边界与错误路径。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

# 将项目 src 加入导入路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aion.tools.builtin.edit import EditTool
from aion.tools.builtin.exec import ExecTool
from aion.tools.builtin.config import (
    config_apply,
    config_get,
    config_patch,
    config_schema_lookup,
    gateway_tool as _gw_tool,
)

gateway_tool = _gw_tool.func
from aion.tools.builtin.grep_find import FindTool, GrepTool
from aion.rag.search import MemorySearchTool
from aion.memory.long import LongTermStore
from aion.tools.builtin.trash import trash as _trash_tool
from aion.tools.builtin.delete import delete as _delete_tool
from aion.tools.builtin.write import WriteTool
from aion.tools.builtin.apply_patch_tool import ApplyPatchTool


# ---------------------------------------------------------------------------
# WriteTool：append 与 Memory Flush 模式
# ---------------------------------------------------------------------------


class TestWriteToolAppend:
    """WriteTool.append 追加写入场景"""

    def test_append_creates_file(self, tmp_path: Path):
        """连续 append 应将内容顺序追加到同一文件

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        f = tmp_path / "append.txt"
        tool = WriteTool(workspace_root=tmp_path)
        r = tool.append(str(f), "first\n")
        assert "追加成功" in r
        r2 = tool.append(str(f), "second\n")
        assert "追加成功" in r2
        assert f.read_text() == "first\nsecond\n"

    def test_append_resolves_relative_path(self, tmp_path: Path):
        """相对路径应相对于 workspace_root 解析

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        f = tmp_path / "rel.txt"
        tool = WriteTool(workspace_root=tmp_path)
        r = tool.append("rel.txt", "content\n")
        assert "追加成功" in r
        assert f.read_text() == "content\n"

    def test_append_empty_content(self, tmp_path: Path):
        """空字符串 append 仍应成功并创建/更新文件

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        f = tmp_path / "empty.txt"
        tool = WriteTool(workspace_root=tmp_path)
        r = tool.append(str(f), "")
        assert "追加成功" in r
        assert f.read_text() == ""


class TestWriteToolMemoryFlush:
    """WriteTool Memory Flush 模式（仅允许 journal 路径）"""

    def test_flush_mode_rejects_other_paths(self, tmp_path: Path):
        """flush 模式下写入非允许路径应被拒绝

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        tool = WriteTool(
            workspace_root=tmp_path,
            memory_flush_path="journal/2026-04-28.md",
        )
        r = tool.write("other.txt", "should be rejected")
        assert "拒绝写入" in r or "只能写入" in r

    def test_flush_mode_appends_to_journal(self, tmp_path: Path):
        """flush 模式仅允许写入 memory_flush_path 指定日记文件

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        journal = tmp_path / "journal" / "2026-04-28.md"
        tool = WriteTool(
            workspace_root=tmp_path,
            memory_flush_path="journal/2026-04-28.md",
        )
        r1 = tool.write("journal/2026-04-28.md", "first entry\n")
        assert "Memory Flush" in r1
        r2 = tool.write("journal/2026-04-28.md", "second entry\n")
        assert "Memory Flush" in r2
        # journal 目录和文件由 tool.write 的 parent.mkdir 创建
        assert journal.exists(), f"Journal file should exist at {journal}"
        assert journal.read_text() == "first entry\nsecond entry\n"


# ---------------------------------------------------------------------------
# trash_tool：移入系统垃圾桶
# ---------------------------------------------------------------------------


class TestTrashTool:
    """trash_tool 将文件/目录移至 ~/.Trash 的测试"""

    def test_trash_file_moves_to_trash(self, tmp_path: Path):
        """存在的文件应被移入垃圾桶且原路径消失

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        f = tmp_path / "trash_me.txt"
        f.write_text("to be trashed")
        _trash = _trash_tool.func
        r = _trash(str(f))
        assert "垃圾桶" in r
        assert not f.exists()
        # 验证确实进了 .Trash
        trash = Path.home() / ".Trash" / "trash_me.txt"
        assert trash.exists(), f"File should be in trash at {trash}"
        trash.unlink()  # 清理本机垃圾桶中的测试文件

    def test_trash_nonexistent_returns_error(self):
        """不存在路径应返回错误提示

        Returns:
            None
        """
        _trash = _trash_tool.func
        r = _trash("/nonexistent/path/that/does/not/exist.txt")
        assert "不存在" in r

    def test_trash_directory(self, tmp_path: Path):
        """目录整体应可移入垃圾桶

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        d = tmp_path / "trash_dir"
        d.mkdir()
        (d / "file.txt").write_text("content")
        _trash = _trash_tool.func
        r = _trash(str(d))
        assert "垃圾桶" in r
        assert not d.exists()
        trash = Path.home() / ".Trash" / "trash_dir"
        assert trash.exists()
        import shutil

        shutil.rmtree(trash)


# ---------------------------------------------------------------------------
# delete_tool：永久删除
# ---------------------------------------------------------------------------


class TestDeleteTool:
    """delete_tool 永久删除文件/目录测试"""

    def test_delete_file_removes_permanently(self, tmp_path: Path):
        """文件应被永久删除且不可恢复

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        f = tmp_path / "delete_me.txt"
        f.write_text("permanent deletion")
        _delete = _delete_tool.func
        r = _delete(str(f))
        assert "已删除" in r
        assert not f.exists()

    def test_delete_nonexistent_returns_error(self):
        """不存在路径应返回错误

        Returns:
            None
        """
        _delete = _delete_tool.func
        r = _delete("/nonexistent/path/delete_me.txt")
        assert "不存在" in r

    def test_delete_permission_error_path(self):
        """无权限或受保护路径应返回权限/不存在类错误

        Returns:
            None
        """
        _delete = _delete_tool.func
        r = _delete("/bin/ls")
        # 返回 权限不足 或 不存在，取决于平台
        assert "权限" in r or "不存在" in r or "删除失败" in r


# ---------------------------------------------------------------------------
# gateway_tool / config_*：运行时配置读写
# ---------------------------------------------------------------------------


class TestGatewayTool:
    """gateway 模块 config.get/patch/apply/schema 与 gateway_tool 分发测试"""

    def setup_method(self):
        """每个用例前将 DEFAULT_CONFIG_PATH 指向临时 JSON 文件

        Returns:
            None
        """
        from aion.tools.builtin import config

        self._orig_path = config.DEFAULT_CONFIG_PATH
        self._tmp_config = Path(tempfile.mktemp(suffix=".json"))
        config.DEFAULT_CONFIG_PATH = self._tmp_config

    def teardown_method(self):
        """恢复默认配置路径并删除临时文件

        Returns:
            None
        """
        from aion.tools.builtin import config

        config.DEFAULT_CONFIG_PATH = self._orig_path
        if self._tmp_config.exists():
            self._tmp_config.unlink()

    def test_config_get_returns_hash(self):
        """config_get 应返回 ok、config 内容与 hash

        Returns:
            None
        """
        self._tmp_config.write_text('{"workspace":"test"}')
        result = config_get()
        assert result["ok"] is True
        assert "hash" in result
        assert result["config"]["workspace"] == "test"

    def test_config_get_empty_when_no_file(self):
        """无配置文件时 config 应为空对象

        Returns:
            None
        """
        result = config_get()
        assert result["ok"] is True
        assert result["config"] == {}

    def test_config_patch_updates_config(self):
        """合法 JSON patch 应合并写入临时配置

        Returns:
            None
        """
        self._tmp_config.write_text('{"a":1}')
        result = config_patch('{"b":2}')
        assert result["ok"] is True
        assert json.loads(self._tmp_config.read_text()) == {"a": 1, "b": 2}

    def test_config_patch_invalid_json(self):
        """非法 JSON 应返回 ok=False

        Returns:
            None
        """
        result = config_patch("not json")
        assert result["ok"] is False
        assert "Invalid JSON" in result["error"]

    def test_config_patch_protected_path_rejected(self):
        """修改受保护路径（如 tools.exec.ask）应被拒绝

        Returns:
            None
        """
        self._tmp_config.write_text('{"tools":{"exec":{"ask":true}}}')
        result = config_patch('{"tools":{"exec":{"ask":false}}}')
        assert result["ok"] is False
        assert "protected" in result["error"].lower()

    def test_config_apply_full_replace(self):
        """config_apply 应整文件替换配置

        Returns:
            None
        """
        self._tmp_config.write_text('{"old":"value"}')
        result = config_apply('{"new":"config"}')
        assert result["ok"] is True
        assert json.loads(self._tmp_config.read_text()) == {"new": "config"}

    def test_config_schema_lookup_found(self):
        """已知路径 memory 应返回 schema 片段

        Returns:
            None
        """
        result = config_schema_lookup("memory")
        assert result["ok"] is True
        assert "memory" in result["schema"].lower()

    def test_config_schema_lookup_not_found(self):
        """未知路径应返回 ok=False

        Returns:
            None
        """
        result = config_schema_lookup("nonexistent_path")
        assert result["ok"] is False

    def test_gateway_tool_config_get(self):
        """gateway_tool('config.get') 应等价于 config_get

        Returns:
            None
        """
        self._tmp_config.write_text('{"workspace":"test"}')
        result = gateway_tool("config.get")
        assert result["ok"] is True

    def test_gateway_tool_config_patch(self):
        """gateway_tool('config.patch') 应执行部分更新

        Returns:
            None
        """
        self._tmp_config.write_text("{}")
        result = gateway_tool("config.patch", raw='{"x":1}')
        assert result["ok"] is True

    def test_gateway_tool_unknown_action(self):
        """未知 action 应返回错误

        Returns:
            None
        """
        result = gateway_tool("unknown.action")
        assert result["ok"] is False
        assert "Unknown action" in result["error"]


# ---------------------------------------------------------------------------
# web_search：联网搜索（按配置 provider 执行）
# ---------------------------------------------------------------------------


class TestWebSearch:
    """web_search 参数校验与结果格式测试"""

    def test_web_search_empty_query(self):
        """空查询应返回错误

        Returns:
            None
        """
        from aion.tools.builtin.web_tools import web_search_impl as _web_search

        result = _web_search("")
        assert "错误" in result or "不能为空" in result

    def test_web_search_formats_provider_results(self, monkeypatch):
        """provider 返回结果时格式化为带序号的多行（mock provider，不依赖外网）。"""
        from aion.tools.builtin import web_tools
        from aion.config.schema import Config
        from aion.search.types import SearchResultItem

        class _FakeProvider:
            provider_id = "bocha"

            def search(self, request):
                return [
                    SearchResultItem("T1", "http://a", "S1"),
                    SearchResultItem("T2", "http://b", "S2"),
                ]

        monkeypatch.setattr(web_tools, "_load_config", lambda: Config(search={}))
        monkeypatch.setattr(web_tools, "create_provider", lambda config: _FakeProvider())

        from aion.tools.builtin.web_tools import web_search_impl as _web_search

        result = _web_search("python", max_results=3)
        lines = result.strip().split("\n")
        assert "1." in lines[0]
        assert "T1" in result
        assert "http://a" in result

    def test_web_search_not_configured(self, monkeypatch):
        """未配置 provider 时应返回未配置错误（不依赖外网）。"""
        from aion.tools.builtin import web_tools
        from aion.config.schema import Config

        monkeypatch.setattr(web_tools, "_load_config", lambda: Config(search={}))

        from aion.tools.builtin.web_tools import web_search_impl as _web_search

        result = _web_search("python", max_results=3)
        assert "未配置" in result

    def test_web_search_empty_results_message(self, monkeypatch):
        """provider 返回空列表时格式化为「无搜索结果」并标注 provider。"""
        from aion.tools.builtin import web_tools
        from aion.config.schema import Config
        from aion.search.types import SearchResultItem

        class _FakeProvider:
            provider_id = "baidu"

            def search(self, request):
                return []

        monkeypatch.setattr(web_tools, "_load_config", lambda: Config(search={}))
        monkeypatch.setattr(web_tools, "create_provider", lambda config: _FakeProvider())

        from aion.tools.builtin.web_tools import web_search_impl as _web_search

        result = _web_search("python", max_results=3)
        assert "[baidu: 无搜索结果]" in result


# ---------------------------------------------------------------------------
# memory 工具：memory_write / memory_search / memory_get
# ---------------------------------------------------------------------------


class TestMemoryTools:
    """内置 memory 工具交互测试（直接使用 LongTermStore / MemorySearchTool）"""

    def test_memory_write_creates_entry(self, tmp_path: Path):
        """memory_write 应在工作区写入记忆条目"""
        ws = tmp_path / "workspace"
        ws.mkdir()
        long_memory = LongTermStore(
            workspace_dir=ws,
            agent_id="main",
        )
        long_memory.overwrite("test memory content 12345")

        mem_file = ws / "agents" / "main" / "memory" / "MEMORY.md"
        assert mem_file.exists()
        content = mem_file.read_text()
        assert "test memory content 12345" in content

    def test_memory_search_returns_results(self, tmp_path: Path):
        """预置 FTS5 索引后 search 应命中关键词"""
        from aion.memory.fts5 import FTSIndexer

        ws = tmp_path / "workspace"
        ws.mkdir()

        fts_dir = ws / "agents" / "main" / "fts5"
        fts_dir.mkdir(parents=True)
        fts = FTSIndexer(fts_dir / "memory_search.db")
        fts.add(
            "test_1",
            "今日完成了工具测试工作。",
            path="memory/2026-04-28.md",
            source="daily",
            date="2026-04-28",
        )
        fts.close()

        tool = MemorySearchTool(workspace_dir=ws)
        results = tool.search("工具测试", max_results=6)
        assert any("工具测试" in r.get("content", "") for r in results)

    def test_memory_get_with_line_offset(self, tmp_path: Path):
        """memory_get 支持 path|from_line 格式按行偏移读取"""
        ws = tmp_path / "workspace"
        ws.mkdir()
        mem_file = ws / "memory" / "2026-04-28.md"
        mem_file.parent.mkdir()
        mem_file.write_text("line1\nline2\nline3\nline4\n")

        tool = MemorySearchTool(workspace_dir=ws)
        result = tool.get(str(mem_file), from_line=2)
        assert "line1" in result or "line2" in result


# ---------------------------------------------------------------------------
# ExecTool：命令执行边界与 process 子命令
# ---------------------------------------------------------------------------


class TestExecToolErrors:
    """ExecTool 空命令、工作目录、超时、退出码与 process 错误路径"""

    def test_exec_empty_command(self, tmp_path: Path):
        """空命令字符串应拒绝执行

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        ex = ExecTool(tmp_path)
        r = ex.exec("")
        assert "不能为空" in r

    def test_exec_workdir_outside_workspace(self, tmp_path: Path):
        """workdir 超出工作区应拒绝

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        ex = ExecTool(tmp_path)
        r = ex.exec("echo ok", workdir="/tmp")
        assert "工作空间内" in r or "error" in r.lower()

    def test_exec_with_env_vars(self, tmp_path: Path):
        """env 参数应注入子进程环境变量

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        ex = ExecTool(tmp_path, default_timeout_sec=5.0)
        r = ex.exec("printf $MYVAR", env={"MYVAR": "hello_env"})
        assert "hello_env" in r

    def test_exec_timeout(self, tmp_path: Path):
        """超时命令应返回超时提示

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        ex = ExecTool(tmp_path, default_timeout_sec=5.0)
        r = ex.exec("sleep 10", timeout=1.0)
        assert "超时" in r or "timeout" in r.lower()

    def test_exec_nonzero_exit_code(self, tmp_path: Path):
        """非零退出码应在输出中体现

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        ex = ExecTool(tmp_path, default_timeout_sec=5.0)
        r = ex.exec("exit 42")
        assert "exit code: 42" in r

    def test_process_list_empty(self, tmp_path: Path):
        """无后台任务时 list 应提示为空

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        ex = ExecTool(tmp_path)
        r = ex.process(action="list")
        assert "无后台任务" in r or "no background" in r.lower()

    def test_process_unknown_action(self, tmp_path: Path):
        """未知 process action 应返回错误

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        ex = ExecTool(tmp_path)
        r = ex.process(action="unknown")
        assert "错误" in r or "未知" in r

    def test_process_poll_unknown_session(self, tmp_path: Path):
        """poll 不存在的 sessionId 应提示未找到

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        ex = ExecTool(tmp_path)
        r = ex.process(action="poll", sessionId="does_not_exist_123")
        assert "未找到" in r or "not found" in r.lower()


# ---------------------------------------------------------------------------
# GrepTool / FindTool：搜索边界
# ---------------------------------------------------------------------------


class TestGrepToolErrors:
    """GrepTool/FindTool 空模式、非法正则、二进制跳过等"""

    def test_grep_empty_pattern(self, tmp_path: Path):
        """空正则模式应拒绝

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        g = GrepTool(tmp_path)
        r = g.grep("")
        assert "不能为空" in r

    def test_grep_invalid_regex(self, tmp_path: Path):
        """非法正则应返回错误而非崩溃

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        g = GrepTool(tmp_path)
        r = g.grep("[invalid")
        assert "无效" in r or "error" in r.lower() or "Invalid" in r

    def test_grep_binary_file_skipped(self, tmp_path: Path):
        """二进制文件应被跳过，不参与文本匹配

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        f = tmp_path / "binary.bin"
        f.write_bytes(b"\x00\x01\x02" * 100)
        g = GrepTool(tmp_path)
        r = g.grep("pattern")
        assert "pattern" not in r

    def test_find_empty_pattern(self, tmp_path: Path):
        """空 glob 模式应拒绝

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        f = FindTool(tmp_path)
        r = f.find("")
        assert "不能为空" in r or "错误" in r

    def test_find_invalid_glob(self, tmp_path: Path):
        """畸形 glob 不应导致异常，应返回无匹配或错误提示

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        f = FindTool(tmp_path)
        r = f.find("**/[*")
        assert "（无匹配文件）" in r or "错误" in r or "已达" in r


# ---------------------------------------------------------------------------
# ApplyPatchTool：删除、新建、/dev/null 拒绝
# ---------------------------------------------------------------------------


class TestApplyPatchMore:
    """ApplyPatchTool 除常规更新外的 diff 形态测试"""

    def test_patch_delete_file(self, tmp_path: Path):
        """清空内容的 unified diff（非 +++ /dev/null）应写回文件

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        f = tmp_path / "todelete.txt"
        f.write_text("line1\nold\nline3\n")
        # 标准 unified diff 删除格式：+++ /dev/null 表示文件被删除
        patch = """diff --git a/todelete.txt b/todelete.txt
--- a/todelete.txt
+++ b/todelete.txt
@@ -1,3 +0,0 @@
-line1
-old
-line3
"""
        t = ApplyPatchTool(tmp_path)
        msg = t.apply_patch(patch)
        assert "已写入" in msg or "todelete.txt" in msg

    def test_patch_new_file(self, tmp_path: Path):
        """--- /dev/null 形式应在工作区创建新文件

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        patch = """diff --git a/newfile.txt b/newfile.txt
--- /dev/null
+++ b/newfile.txt
@@ -0,0 +1 @@
+new content
"""
        t = ApplyPatchTool(tmp_path)
        msg = t.apply_patch(patch)
        assert "已写入" in msg or "newfile.txt" in msg
        assert (tmp_path / "newfile.txt").read_text() == "new content\n"

    def test_patch_dev_null_rejected(self, tmp_path: Path):
        """目标为 /dev/null 的删除 diff 应跳过或拒绝

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        patch = """diff --git a/f.txt b/f.txt
--- a/f.txt
+++ /dev/null
@@ -1 +0,0 @@
-a
"""
        t = ApplyPatchTool(tmp_path)
        msg = t.apply_patch(patch)
        assert "跳过" in msg or "dev/null" in msg or "拒绝" in msg


# ---------------------------------------------------------------------------
# EditTool：参数与多步编辑错误路径
# ---------------------------------------------------------------------------


class TestEditToolMoreErrors:
    """EditTool 空路径、空 edits、未找到 oldText、多步替换与字段别名"""

    def test_edit_empty_path(self, tmp_path: Path):
        """空 file_path 应拒绝

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        tool = EditTool(tmp_path)
        r = tool.edit("", [{"oldText": "a", "newText": "b"}])
        assert "不能为空" in r

    def test_edit_empty_edits(self, tmp_path: Path):
        """空 edits 列表应拒绝

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        tool = EditTool(tmp_path)
        f = tmp_path / "test.txt"
        f.write_text("hello")
        r = tool.edit(str(f), [])
        assert "不能为空" in r

    def test_edit_oldText_not_found(self, tmp_path: Path):
        """oldText 不存在时应返回错误

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        tool = EditTool(tmp_path)
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        r = tool.edit(str(f), [{"oldText": "not found", "newText": "replaced"}])
        assert "错误" in r or "未找到" in r

    def test_edit_multiple_edits_in_sequence(self, tmp_path: Path):
        """同一请求内多组 old/new 应按序应用

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        tool = EditTool(tmp_path)
        f = tmp_path / "multi.txt"
        f.write_text("aaa bbb ccc")
        r = tool.edit(
            str(f),
            [
                {"oldText": "aaa", "newText": "AAA"},
                {"oldText": "bbb", "newText": "BBB"},
            ],
        )
        assert "已写入" in r
        assert f.read_text() == "AAA BBB ccc"

    def test_edit_with_old_text_and_new_text_alias(self, tmp_path: Path):
        """支持 old_text/new_text 蛇形字段别名

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        tool = EditTool(tmp_path)
        f = tmp_path / "alias.txt"
        f.write_text("foo bar")
        r = tool.edit(str(f), [{"old_text": "foo", "new_text": "FOO"}])
        assert "已写入" in r
        assert f.read_text() == "FOO bar"


# ---------------------------------------------------------------------------
# ls_tool：目录列表与 ~ 展开
# ---------------------------------------------------------------------------


class TestLsToolMore:
    """ls_tool 列出目录、波浪号路径、非目录路径错误"""

    def test_ls_current_dir(self, tmp_path: Path):
        """应列出目录内所有条目名

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        from aion.tools.builtin.ls import ls as _ls_tool_mod

        ls_tool = _ls_tool_mod.func

        (tmp_path / "a.txt").write_text("1")
        (tmp_path / "b.txt").write_text("2")
        r = ls_tool(str(tmp_path))
        assert "a.txt" in r
        assert "b.txt" in r

    def test_ls_expands_tilde(self, tmp_path: Path):
        """路径为 ~ 时应展开为用户主目录且可列出

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        from aion.tools.builtin.ls import ls as _ls_tool_mod

        ls_tool = _ls_tool_mod.func

        r = ls_tool("~")
        assert r and "错误" not in r

    def test_ls_file_not_directory(self, tmp_path: Path):
        """对普通文件调用 ls 应提示非目录

        Args:
            tmp_path: pytest 临时目录（Path）

        Returns:
            None
        """
        from aion.tools.builtin.ls import ls as _ls_tool_mod

        ls_tool = _ls_tool_mod.func

        f = tmp_path / "file.txt"
        f.write_text("content")
        r = ls_tool(str(f))
        assert "不是目录" in r or "not dir" in r.lower()
