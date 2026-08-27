"""Bootstrap 运行时监控 — BootstrapMonitor

包括 misclaim 审计 + tool output 检测。
从 AgentLoop._append_bootstrap_misclaim_if_audit_yes() / _parse_audit_yes_line() 提取。
"""

from pathlib import Path
from typing import Optional, Any

from .bootstrap.files import get_bootstrap_file_status


class BootstrapMonitor:
    """运行时 bootstrap 钩子 — misclaim 审计 + tool output 检测。"""

    def __init__(
        self,
        workspace_dir: Path,
        agent_id: Optional[str],
        llm: Any,
        trace_id: str = "",
    ):
        self.workspace_dir = workspace_dir
        self.agent_id = agent_id or "main"
        self.llm = llm
        self._trace_id = trace_id

    async def audit_misclaim(self, response: str) -> str:
        t = (response or "").strip()
        if not t:
            return t
        pending = get_bootstrap_file_status(self.workspace_dir, self.agent_id)
        if not pending.get("workspace_pending") and not pending.get("agent_pending"):
            return response

        fact_lines: list[str] = []
        if pending.get("workspace_pending"):
            fact_lines.append(
                "工作空间级：根目录的引导文件 WORKSPACE_BOOTSTRAP.md 在磁盘上仍存在，按约定该级引导尚未结束。"
            )
        if pending.get("agent_pending"):
            fact_lines.append(
                f"Agent 级：agents/{self.agent_id}/ 下引导文件 AGENT_BOOTSTRAP.md 在磁盘上仍存在，该级引导尚未结束。"
            )

        system = (
            "你是审核器。根据下方「事实」判断：在事实表明仍有引导未完成时，"
            "助手的「回复」是否会使终端用户**误以为**可以不再按项目引导补信息/删引导文件、"
            "或误以为已「全部初始化/引导收工」、可与普通已完成配置的助手一样纯闲聊。\n"
            "只考虑语义，不要对固定措辞做模式匹配。只输出一行，且必须以 YES 或 NO 开头。"
        )
        user_block = "事实：\n" + "\n".join(fact_lines) + "\n\n助手回复：\n" + t[:4000]
        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            msg = await self.llm.ainvoke(
                [SystemMessage(content=system), HumanMessage(content=user_block)],
                max_tokens=64,
                temperature=0.0,
            )
        except Exception:
            return response
        audit = (msg.content or "").strip()

        # 记录 LLM Generation（非阻塞）
        try:
            from ..observability import Tracer

            if Tracer.available and self._trace_id:
                usage_meta = getattr(msg, "usage_metadata", None) or {}
                Tracer.generation(
                    trace_id=self._trace_id,
                    name="bootstrap_audit",
                    model=getattr(self.llm, "model", "unknown"),
                    input=system[:200] if system else "",
                    output=audit[:200] if audit else "",
                    usage={
                        "input": usage_meta.get("input_tokens", 0),
                        "output": usage_meta.get("output_tokens", 0),
                        "unit": "TOKENS",
                    },
                )
        except Exception:
            pass

        if not self._parse_audit_yes_line(audit):
            return response
        bits: list[str] = []
        if pending.get("workspace_pending"):
            bits.append("工作区级引导文件尚未清理")
        if pending.get("agent_pending"):
            bits.append("Agent 级引导文件尚未清理")
        note = "；".join(bits) + "。系统正在完成初始化。"
        return f"{response.rstrip()}\n\n（轻提示：{note}）"

    def check_output_for_refresh(self, tool_name: str, content: str) -> bool:
        from ..core.constants import is_bootstrap_ritual_filename

        if tool_name == "delete" and content.startswith("已删除: "):
            deleted_name = content[len("已删除: ") :].strip()
            return is_bootstrap_ritual_filename(deleted_name)
        return False

    @staticmethod
    def _parse_audit_yes_line(text: str) -> bool:
        if not (text or "").strip():
            return False
        line = text.strip().splitlines()[0].strip().upper()
        return line.startswith("YES")
