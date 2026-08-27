"""工作区 Bootstrap 引导文件加载与状态协调测试

覆盖引导加载顺序与截断、系统提示词拼装、
完成状态与磁盘上 BOOTSTRAP 文件的一致性协调等逻辑。
"""

from pathlib import Path
import sys

# 将项目 src 加入导入路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aion.agent.bootstrap import (
    BOOTSTRAP_FILE_ORDER,
    SYSTEM_PROMPT_CACHE_BOUNDARY,
    build_bootstrap_markdown_for_system_prompt,
    get_bootstrap_file_status,
)


class TestBootstrapState:
    """引导状态相关用例"""

    def test_bootstrap_basename_order(self):
        """BOOTSTRAP_FILE_ORDER 应与设计文档中的加载顺序一致"""
        assert BOOTSTRAP_FILE_ORDER == [
            "workspace.md",
            "config.md",
            "user.md",
            "memory.md",
            "workspace_bootstrap.md",
            "agent_bootstrap.md",
        ]

    def test_config_md_has_three_sections(self):
        """CONFIG_MD 含 ## Identity / ## Rules / ## Soul 三段，段顺序与 agent_id 插值正确"""
        from aion.agent.bootstrap.templates import CONFIG_MD

        content = CONFIG_MD("main")
        assert "## Identity" in content
        assert "## Rules" in content
        assert "## Soul" in content
        # agent_id 正确插值（防止 f-string 误丢 f 前缀）
        assert "agents/main/" in content
        # 三段顺序：Identity 在 Rules 前，Rules 在 Soul 前
        assert content.index("## Identity") < content.index("## Rules") < content.index("## Soul")
        # 标题降级：原 AGENTS.md 的 ## 红线 已降为 ###
        assert "### 红线" in content

    def test_heartbeat_not_in_dynamic_section(self, tmp_path):
        """HEARTBEAT 已移除，不应出现在 Project Context 中。"""
        (tmp_path / "USER.md").write_text("u", encoding="utf-8")
        agent = tmp_path / "agents" / "main"
        agent.mkdir(parents=True)
        (agent / "CONFIG.md").write_text("# CONFIG.md\n## Identity\n## Rules\n## Soul\n", encoding="utf-8")

        text = build_bootstrap_markdown_for_system_prompt(tmp_path, "main")
        assert SYSTEM_PROMPT_CACHE_BOUNDARY.strip() in text
        assert "HEARTBEAT" not in text

    def test_pending_follows_files_on_disk(self, tmp_path):
        """WORKSPACE_BOOTSTRAP.md 存在时 pending=True，删除后 pending=False"""
        (tmp_path / "WORKSPACE_BOOTSTRAP.md").write_text("boot", encoding="utf-8")
        status = get_bootstrap_file_status(tmp_path)
        assert status["workspace_pending"] is True

        (tmp_path / "WORKSPACE_BOOTSTRAP.md").unlink()
        status = get_bootstrap_file_status(tmp_path)
        assert status["workspace_pending"] is False

    def test_bootstrap_file_in_prompt_when_exists(self, tmp_path):
        """CONFIG.md 存在时应在 prompt 中出现"""
        (tmp_path / "USER.md").write_text("u", encoding="utf-8")
        (tmp_path / "WORKSPACE_BOOTSTRAP.md").write_text("BOOT_INJECT", encoding="utf-8")
        ag = tmp_path / "agents" / "main"
        ag.mkdir(parents=True)
        (ag / "CONFIG.md").write_text("# CONFIG.md\n## Identity\nCFG_BODY\n", encoding="utf-8")
        text = build_bootstrap_markdown_for_system_prompt(tmp_path, "main")
        assert "BOOT_INJECT" in text
        assert "CFG_BODY" in text

    def test_soul_hint_refs_config_md(self, tmp_path):
        """加载 CONFIG.md 时人格提示文案引用 CONFIG.md 而非 SOUL.md"""
        (tmp_path / "USER.md").write_text("u", encoding="utf-8")
        ag = tmp_path / "agents" / "main"
        ag.mkdir(parents=True)
        (ag / "CONFIG.md").write_text("# CONFIG.md\n## Soul\n", encoding="utf-8")
        text = build_bootstrap_markdown_for_system_prompt(tmp_path, "main")
        assert "若 CONFIG.md 含 ## Soul 段" in text
        assert "SOUL.md" not in text

    def test_create_workspace_writes_single_config_md(self, tmp_path, monkeypatch):
        """create_workspace 生成单一 CONFIG.md，不再生成 AGENTS/SOUL/IDENTITY 三文件"""
        from aion.cli._common import create_workspace

        monkeypatch.setattr("aion.cli._common.WORKSPACES_DIR", tmp_path)
        create_workspace("demo", "main")
        ag = tmp_path / "demo" / "agents" / "main"
        assert (ag / "CONFIG.md").is_file()
        assert not (ag / "AGENTS.md").exists()
        assert not (ag / "SOUL.md").exists()
        assert not (ag / "IDENTITY.md").exists()
        content = (ag / "CONFIG.md").read_text(encoding="utf-8")
        assert "## Identity" in content and "## Rules" in content and "## Soul" in content

    def test_member_agent_system_prompt_no_duplicate_soul(self, tmp_path):
        """member agent system prompt 中 Soul 内容不重复（bootstrap 已加载 CONFIG.md）"""
        from aion.agent.prompt import build_system_prompt

        ag = tmp_path / "agents" / "main"
        ag.mkdir(parents=True, exist_ok=True)
        (ag / "memory").mkdir(parents=True)
        (tmp_path / "USER.md").write_text("u", encoding="utf-8")
        soul_marker = "唯一人格标记__SOUL_UNIQUE__"
        (ag / "CONFIG.md").write_text(f"# CONFIG.md\n## Soul\n{soul_marker}\n", encoding="utf-8")
        sections = build_system_prompt(
            workspace_dir=tmp_path,
            agent_id="main",
            memory_config={},
            is_subagent=False,
            is_leader=False,
        )
        joined = "\n\n".join(sections)
        # bootstrap 段加载 CONFIG.md 含 Soul 段一次；不应出现第二个副本
        assert joined.count(soul_marker) == 1


class TestConfigMdValidation:
    """validate_bootstrap_delete_allowed 从 CONFIG.md ## Identity 段抽字段"""

    def test_reject_when_identity_section_missing(self, tmp_path):
        """CONFIG.md 存在但缺 ## Identity 段 → 拒绝删除 AGENT_BOOTSTRAP.md"""
        from aion.agent.bootstrap.validation import validate_bootstrap_delete_allowed

        ag = tmp_path / "agents" / "main"
        ag.mkdir(parents=True)
        (tmp_path / "USER.md").write_text("- **名字：**张三\n- **称呼：**老张\n- **时区：**Asia/Shanghai\n", encoding="utf-8")
        (tmp_path / "WORKSPACE.md").write_text("## 项目 / 领域\nreal\n## 当前目标\nreal\n## 通用约束\nreal\n", encoding="utf-8")
        # CONFIG.md 存在但无 ## Identity 段
        (ag / "CONFIG.md").write_text("# CONFIG.md\n## Rules\nonly rules\n", encoding="utf-8")
        ritual = ag / "AGENT_BOOTSTRAP.md"
        ritual.write_text("boot", encoding="utf-8")
        ok, reason = validate_bootstrap_delete_allowed(ritual)
        assert ok is False
        assert "## Identity" in reason

    def test_pass_when_identity_fields_filled(self, tmp_path):
        """CONFIG.md ## Identity 段字段齐全 → 允许删除"""
        from aion.agent.bootstrap.validation import validate_bootstrap_delete_allowed

        ag = tmp_path / "agents" / "main"
        ag.mkdir(parents=True)
        (tmp_path / "USER.md").write_text("- **名字：**张三\n- **称呼：**老张\n- **时区：**Asia/Shanghai\n", encoding="utf-8")
        (tmp_path / "WORKSPACE.md").write_text("## 项目 / 领域\nreal\n## 当前目标\nreal\n## 通用约束\nreal\n", encoding="utf-8")
        (ag / "CONFIG.md").write_text(
            "# CONFIG.md\n## Identity\n- **名字：**Aion\n- **风格：**温和\n- **Emoji：**🤖\n## Rules\n## Soul\n",
            encoding="utf-8",
        )
        ritual = ag / "AGENT_BOOTSTRAP.md"
        ritual.write_text("boot", encoding="utf-8")
        ok, _ = validate_bootstrap_delete_allowed(ritual)
        assert ok is True
