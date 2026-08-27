"""SessionBinder 会话键与 UUID 绑定单元测试

测试 session_key 到 session_id 的创建、复用、/new 刷新、解绑、
多键隔离、JSON 持久化，以及损坏文件备份重建。
"""

import json
import shutil
import tempfile
from pathlib import Path
import pytest
from aion.session.binder import SessionBinder


class TestSessionBinder:
    """SessionBinder 绑定表读写与生命周期测试"""

    @pytest.fixture
    def temp_workspace(self):
        """创建并在用例结束后清理临时工作区目录

        Yields:
            Path: 临时工作区根路径
        """
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    def test_get_or_create_session_id(self, temp_workspace):
        """同一 session_key 首次创建 UUID，再次调用应返回相同 id

        Args:
            temp_workspace: 临时工作区根路径（Path）

        Returns:
            None
        """
        binder = SessionBinder(temp_workspace)
        # 飞书 p2p 场景的典型 session_key 格式
        session_key = "agent:main:feishu:p2p:ou_123"

        session_id1 = binder.get_or_create_session_id(session_key)
        assert session_id1 is not None
        assert len(session_id1) == 36  # 标准 UUID 字符串长度

        session_id2 = binder.get_or_create_session_id(session_key)
        assert session_id2 == session_id1

    def test_refresh_binding(self, temp_workspace):
        """refresh_binding（/new）应为同一 key 分配新的 session_id

        Args:
            temp_workspace: 临时工作区根路径（Path）

        Returns:
            None
        """
        binder = SessionBinder(temp_workspace)
        session_key = "agent:main:feishu:p2p:ou_123"

        old_session_id = binder.get_or_create_session_id(session_key)
        new_session_id = binder.refresh_binding(session_key)

        assert new_session_id != old_session_id
        assert binder.get_session_id(session_key) == new_session_id

    # def test_unbind(self, temp_workspace):  # unbind 已废弃
    #     """unbind 应移除映射并返回被解绑的 session_id"""
    #     binder = SessionBinder(temp_workspace)
    #     session_key = "agent:main:feishu:p2p:ou_123"
    #     session_id = binder.get_or_create_session_id(session_key)
    #     unbound_id = binder.unbind(session_key)
    #     assert unbound_id == session_id
    #     assert binder.get_session_id(session_key) is None

    def test_persistence(self, temp_workspace):
        """新 SessionBinder 实例应从磁盘读回既有绑定

        Args:
            temp_workspace: 临时工作区根路径（Path）

        Returns:
            None
        """
        binder1 = SessionBinder(temp_workspace)
        session_key = "agent:main:feishu:p2p:ou_123"
        session_id1 = binder1.get_or_create_session_id(session_key)

        binder2 = SessionBinder(temp_workspace)
        session_id2 = binder2.get_or_create_session_id(session_key)
        assert session_id2 == session_id1

    def test_multiple_session_keys(self, temp_workspace):
        """不同 session_key 应对应独立 session_id

        Args:
            temp_workspace: 临时工作区根路径（Path）

        Returns:
            None
        """
        binder = SessionBinder(temp_workspace)

        key1 = "agent:main:feishu:p2p:ou_111"
        key2 = "agent:main:feishu:p2p:ou_222"

        id1 = binder.get_or_create_session_id(key1)
        id2 = binder.get_or_create_session_id(key2)

        assert id1 != id2
        assert binder.get_session_id(key1) == id1
        assert binder.get_session_id(key2) == id2

    def test_file_not_exists(self, temp_workspace):
        """首次写入应创建 session_bindings.json 且结构含 version/bindings

        Args:
            temp_workspace: 临时工作区根路径（Path）

        Returns:
            None
        """
        bindings_file = temp_workspace / "session_bindings.json"
        assert not bindings_file.exists()

        binder = SessionBinder(temp_workspace)
        binder.get_or_create_session_id("test_key")

        assert bindings_file.exists()
        with open(bindings_file) as f:
            data = json.load(f)
        assert "version" in data
        assert "bindings" in data

    def test_file_corrupted_backup(self, temp_workspace):
        """损坏的 JSON 应备份为 .bak 并允许继续创建新绑定

        Args:
            temp_workspace: 临时工作区根路径（Path）

        Returns:
            None
        """
        bindings_file = temp_workspace / "session_bindings.json"

        with open(bindings_file, "w") as f:
            f.write("corrupted json {")

        binder = SessionBinder(temp_workspace)
        backup_file = bindings_file.with_suffix(".bak")
        assert backup_file.exists()

        session_id = binder.get_or_create_session_id("test_key")
        assert session_id is not None
