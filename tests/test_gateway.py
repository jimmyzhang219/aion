"""Gateway 内置 config.* 工具单元测试

测试 config.get、config.patch、config.apply、config.schema_lookup
及 gateway_tool 对 config 动作的分发；使用 monkeypatch 指向临时 aion.json。
"""

import json


class TestGatewayTool:
    """Gateway 内置 config.* 与 gateway_tool 分发测试"""

    def test_config_get_returns_config(self, tmp_path, monkeypatch):
        """config.get 应返回完整配置、hash 且 ok=True

        Args:
            tmp_path: pytest 临时目录（Path）
            monkeypatch: pytest monkeypatch 夹具，用于替换模块属性

        Returns:
            None
        """
        # 创建临时配置文件
        config_file = tmp_path / "aion.json"
        config_file.write_text(
            json.dumps({"workspace": "test", "llm": {"providers": {"test": {"model": "test", "apiKey": "test"}}}})
        )

        # 临时修改默认配置路径
        from aion.tools.builtin import config

        monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", config_file)

        result = config.config_get()

        assert result["ok"] is True
        assert "config" in result
        assert result["config"]["workspace"] == "test"
        assert "hash" in result

    def test_config_patch_updates_config(self, tmp_path, monkeypatch):
        """测试 config.patch 部分更新配置

        Args:
            tmp_path: pytest 临时目录（Path）
            monkeypatch: pytest monkeypatch 夹具，用于替换模块属性

        Returns:
            None
        """
        # 创建临时配置文件
        config_file = tmp_path / "aion.json"
        original = {
            "workspace": "test",
            "llm": {"providers": {"test": {"model": "test", "apiKey": "test"}}},
            "memory": {"daily_memory_days": 2},
        }
        config_file.write_text(json.dumps(original))

        from aion.tools.builtin import config

        monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", config_file)

        # 获取当前 hash
        get_result = config.config_get()
        base_hash = get_result["hash"]

        # 执行 patch
        patch = json.dumps({"memory": {"daily_memory_days": 5}})
        result = config.config_patch(patch, base_hash)

        assert result["ok"] is True

        # 验证更新
        updated = json.loads(config_file.read_text())
        assert updated["memory"]["daily_memory_days"] == 5
        # 未更新的字段保持不变
        assert updated["workspace"] == "test"

    def test_config_patch_rejects_protected_paths(self, tmp_path, monkeypatch):
        """测试 config.patch 拒绝修改保护路径

        Args:
            tmp_path: pytest 临时目录（Path）
            monkeypatch: pytest monkeypatch 夹具，用于替换模块属性

        Returns:
            None
        """
        config_file = tmp_path / "aion.json"
        config_file.write_text(json.dumps({"workspace": "test", "tools": {"exec": {"ask": True}}}))

        from aion.tools.builtin import config

        monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", config_file)

        # 尝试修改保护路径
        patch = json.dumps({"tools": {"exec": {"ask": False}}})
        result = config.config_patch(patch)

        assert result["ok"] is False
        assert "protected" in result["error"].lower()

    def test_config_patch_base_hash_mismatch(self, tmp_path, monkeypatch):
        """测试 config.patch base_hash 不匹配时拒绝

        Args:
            tmp_path: pytest 临时目录（Path）
            monkeypatch: pytest monkeypatch 夹具，用于替换模块属性

        Returns:
            None
        """
        config_file = tmp_path / "aion.json"
        config_file.write_text(json.dumps({"workspace": "test"}))

        from aion.tools.builtin import config

        monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", config_file)

        # 使用错误的 base_hash
        result = config.config_patch("{}", base_hash="wrong_hash")

        assert result["ok"] is False
        assert "mismatch" in result["error"].lower()

    def test_config_apply_replaces_config(self, tmp_path, monkeypatch):
        """测试 config.apply 完整替换配置

        Args:
            tmp_path: pytest 临时目录（Path）
            monkeypatch: pytest monkeypatch 夹具，用于替换模块属性

        Returns:
            None
        """
        config_file = tmp_path / "aion.json"
        config_file.write_text(json.dumps({"workspace": "old"}))

        from aion.tools.builtin import config

        monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", config_file)

        new_config = json.dumps({"workspace": "new", "llm": {"providers": {}}})
        result = config.config_apply(new_config)

        assert result["ok"] is True

        updated = json.loads(config_file.read_text())
        assert updated["workspace"] == "new"
        assert "llm" in updated

    def test_config_schema_lookup_known_path(self, tmp_path, monkeypatch):
        """测试 config.schema_lookup 查询已知路径

        Args:
            tmp_path: pytest 临时目录（Path）
            monkeypatch: pytest monkeypatch 夹具，用于替换模块属性

        Returns:
            None
        """
        from aion.tools.builtin import config

        result = config.config_schema_lookup("workspace")

        assert result["ok"] is True
        assert "schema" in result

    def test_config_schema_lookup_unknown_path(self, tmp_path, monkeypatch):
        """测试 config.schema_lookup 查询未知路径

        Args:
            tmp_path: pytest 临时目录（Path）
            monkeypatch: pytest monkeypatch 夹具，用于替换模块属性

        Returns:
            None
        """
        from aion.tools.builtin import config

        result = config.config_schema_lookup("nonexistent.path")

        assert result["ok"] is False

    def test_gateway_tool_dispatches_config_get(self, tmp_path, monkeypatch):
        """测试 gateway_tool 分发 config.get

        Args:
            tmp_path: pytest 临时目录（Path）
            monkeypatch: pytest monkeypatch 夹具，用于替换模块属性

        Returns:
            None
        """
        config_file = tmp_path / "aion.json"
        config_file.write_text(json.dumps({"workspace": "test"}))

        from aion.tools.builtin import config

        monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", config_file)

        result = config.gateway_tool.func("config.get")

        assert result["ok"] is True

    def test_gateway_tool_unknown_action(self, tmp_path, monkeypatch):
        """测试 gateway_tool 未知 action

        Args:
            tmp_path: pytest 临时目录（Path）
            monkeypatch: pytest monkeypatch 夹具，用于替换模块属性

        Returns:
            None
        """
        from aion.tools.builtin import config

        result = config.gateway_tool.func("unknown.action")

        assert result["ok"] is False
        assert "Unknown action" in result["error"]
