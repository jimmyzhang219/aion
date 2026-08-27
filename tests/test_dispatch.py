"""dispatch_message 集成测试

使用 ~/.aion/aion.json 真实配置 + 真实 AgentLoop + 真实 LLM 调用。
"""

import asyncio
import re as _re
import uuid
from pathlib import Path
from typing import Optional

import pytest
from click.testing import CliRunner

from aion.channels.adapters import (
    ChannelAgentPromptAdapter,
    ChannelCommandAdapter,
    ChannelPlugin,
    SendResult,
)
from aion.channels.constants import ContentBlockType
from aion.channels.types import MessageContext, DispatchResult
from aion.cli.logs import (
    logs,
)
from aion.config.schema import LangfuseConfig
from aion.gateway.dispatch import dispatch_message
from aion.log import get_trace_logger, set_traceid, reset_traceid
from aion.observability import Tracer


class CaptureChannel(ChannelPlugin):
    """测试用 Channel：捕获 Worker 处理后的 DispatchResult。"""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._result: Optional[DispatchResult] = None

    @property
    def channel_id(self) -> str:
        return "test"

    @property
    def channel_name(self) -> str:
        return "Test"

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    def is_running(self) -> bool:
        return True

    def get_agent_prompt_adapter(self) -> ChannelAgentPromptAdapter:
        return ChannelAgentPromptAdapter()

    def get_command_adapter(self) -> ChannelCommandAdapter:
        return ChannelCommandAdapter()

    async def send_message(
        self,
        chat_id: str,
        content: str,
        reply_in_thread: bool = False,
        parent_id: Optional[str] = None,
        **kwargs: object,
    ) -> SendResult:
        return SendResult(message_id="mock", chat_id=chat_id)

    def build_footer(
        self,
        workspace_name: str = "",
        model_name: str = "",
        tokens: int = 0,
        balance: Optional[str] = None,
        traceid: str = "",
    ) -> str:
        return "\n\n---\nTest footer"

    async def respond(self, ctx: MessageContext, result: DispatchResult) -> None:
        self._result = result
        self._event.set()

    async def wait_for_response(self, timeout: float = 30) -> DispatchResult:
        await asyncio.wait_for(self._event.wait(), timeout=timeout)
        result = self._result
        assert result is not None
        return result


@pytest.mark.asyncio
async def test_dispatch_message_requires_workspace_dir():
    """dispatch_message 在缺少 workspace_dir 时抛出 ValueError"""
    ctx = MessageContext(
        channel_id="test",
        chat_id="chat",
        message_id="msg1",
        sender_id="user",
        workspace_dir=None,
    )
    channel = None
    with pytest.raises(ValueError, match="workspace_dir is required"):
        await dispatch_message(ctx, channel)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_dispatch_message_text():
    """正常消息应走通 dispatch → AgentLoop → 真实 LLM 调用"""

    aion_dir = Path.home() / ".aion"

    # 初始化 Langfuse local mode（ConsoleSpanExporter → stderr）
    lf_cfg = LangfuseConfig()
    lf_cfg.enabled = True
    lf_cfg.secret_key = "sk-lf-8f1918418d3d612beb39a925ede313b2"
    lf_cfg.public_key = "pk-lf-e5147ebdcf3d398f55b0cc41bc19ca32"
    lf_cfg.host = "http://120.55.244.209:13000"
    lf_cfg.flush_interval = 30
    lf_cfg.trace_level = "full"
    try:
        Tracer.init(lf_cfg)
    except Exception:
        print("  [warn] Tracer.init failed, continuing without observability")

    channel = CaptureChannel()

    ctx = MessageContext(
        channel_id="test",
        chat_id=str(uuid.uuid4()),  # 每次测试用独立 chat_id → 全新 session
        message_id=str(uuid.uuid4()),
        sender_id="user_1",
        content="请用一句话介绍自己",
        workspace_dir=aion_dir / "workspaces" / "default",
    )

    # dispatch 返回轻量 ack，Worker 后台处理
    await dispatch_message(ctx=ctx, channel=channel)

    # 等待 Worker 完成并捕获结果（多模态 base64 较大，给充足超时）
    result = await channel.wait_for_response(timeout=120)

    print(f"\n  thinking: {result.thinking_parts!r}")
    print(f"  response: {result.response!r}")
    print(f"  trace_id: {result.traceid!r}")
    print(f"  session_id: {result.session_id!r}")
    print(f"  footer: {result.footer!r}")
    print(f"  thinking_parts: {len(result.thinking_parts)} parts")
    print(f"  error: {result.error!r}")

    assert isinstance(result, DispatchResult)
    assert result.response, "LLM 应返回非空响应"
    assert result.session_id != ""
    assert len(result.session_id) == 36
    assert result.error is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_dispatch_message_multimodal():
    """正常消息应走通 dispatch → AgentLoop → 真实 LLM 调用"""

    aion_dir = Path.home() / ".aion"

    # 初始化 Langfuse local mode（ConsoleSpanExporter → stderr）
    lf_cfg = LangfuseConfig()
    lf_cfg.enabled = True
    lf_cfg.secret_key = "sk-lf-8f1918418d3d612beb39a925ede313b2"
    lf_cfg.public_key = "pk-lf-e5147ebdcf3d398f55b0cc41bc19ca32"
    lf_cfg.host = "http://120.55.244.209:13000"
    lf_cfg.flush_interval = 30
    lf_cfg.trace_level = "full"
    try:
        Tracer.init(lf_cfg)
    except Exception:
        print("  [warn] Tracer.init failed, continuing without observability")

    channel = CaptureChannel()

    ctx = MessageContext(
        channel_id="test",
        chat_id=str(uuid.uuid4()),  # 每次测试用独立 chat_id → 全新 session
        message_id=str(uuid.uuid4()),
        sender_id="user_1",
        content=[
            {"type": ContentBlockType.TEXT, "text": "请分析主要内容"},
            {
                "type": ContentBlockType.IMAGE,
                "data": "/tmp/test_image_small.jpg",
                "mimeType": "image/jpeg",
            },
            # {
            #     "type": ContentBlockType.VIDEO,
            #     "data": "/tmp/test_video_small.mp4",
            #     "mimeType": "video/mp4",
            # },
        ],
        workspace_dir=aion_dir / "workspaces" / "default",
    )

    # dispatch 返回轻量 ack，Worker 后台处理
    await dispatch_message(ctx=ctx, channel=channel)

    # 等待 Worker 完成并捕获结果（多模态 base64 较大，给充足超时）
    result = await channel.wait_for_response(timeout=120)

    print(f"\n  thinking: {result.thinking_parts!r}")
    print(f"  response: {result.response!r}")
    print(f"  trace_id: {result.traceid!r}")
    print(f"  session_id: {result.session_id!r}")
    print(f"  footer: {result.footer!r}")
    print(f"  thinking_parts: {len(result.thinking_parts)} parts")
    print(f"  error: {result.error!r}")

    assert isinstance(result, DispatchResult)
    assert result.response, "LLM 应返回非空响应"
    assert result.session_id != ""
    assert len(result.session_id) == 36
    assert result.error is None


# ────────────────────────────────────────────────
# aion logs 命令测试（CliRunner）
# ────────────────────────────────────────────────


class TestLogsCommand:
    """`aion logs --traceid xxx` 单元测试（CliRunner + 临时日志文件）"""

    @staticmethod
    def _strip_ansi(text: str) -> str:
        return _re.sub(r"\x1b\[[0-9;]*m", "", text)

    def test_logs_traceid(self, tmp_path):
        """aion logs --traceid xxx → 过滤 → 树状展示

        使用唯一 traceid 避免跨测试运行时日志残留导致断言不可靠。
        """
        known_traceid = f"test-{uuid.uuid4().hex[:12]}"

        # ── 通过真实 logging 管道写入已知 traceid 的日志行 ──
        token = set_traceid(known_traceid)
        get_trace_logger("aion.agent.loop").info("Round 1")
        get_trace_logger("aion.agent.loop").info("LLM → web_search(")
        get_trace_logger("aion.agent.loop").info("web_search → 2048 chars")
        get_trace_logger("aion.agent.loop").info("LLM → text(123 chars)")
        get_trace_logger("aion.gateway.dispatch").info("dispatch_message start")
        get_trace_logger("aion.channels.feishu.client").info("feishu heartbeat ok")
        reset_traceid(token)

        # ── 写入不同 traceid 的日志（应被过滤掉） ──
        token2 = set_traceid("other-999999")
        get_trace_logger("aion.gateway.dispatch").info("this should not appear")
        reset_traceid(token2)

        # ── 查询 ──
        result = CliRunner().invoke(
            logs,
            [
                "--traceid",
                known_traceid,
            ],
        )

        assert result.exit_code == 0
        out = self._strip_ansi(result.output)

        # 匹配 traceid 的行应出现
        assert "Round 1" in out
        assert "web_search" in out
        assert "dispatch_message start" in out
        # 不匹配的行应过滤
        assert "this should not appear" not in out, "其它 traceid 应被过滤"
        # 应有统计行（不依赖精确数字，文件可能残留同名 traceid）
        assert "共 " in out and " 条日志" in out
        # ReAct 树状展示应有「Round」标题
        assert "[Round 1]" in out
