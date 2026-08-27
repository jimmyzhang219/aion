"""飞书消息发送

设计文档: docs/design/feishu-channel.md 第 7.2 节
"""

import json
import logging
from typing import Optional, Any, Dict

from .config import FeishuAccountConfig
from .types import FeishuSendResult

logger = logging.getLogger(__name__)


def _should_use_card(text: str) -> bool:
    """判断文本是否应该使用 interactive card 发送（包含代码块或表格时使用）

    Args:
        text: 待发送的 Markdown 文本

    Returns:
        bool: 含代码块或 Markdown 表格时返回 True
    """
    import re

    # 检查代码块: 包含 ```
    if "```" in text:
        return True
    # 检查 Markdown 表格: | header | 后面跟着 |---| 样式的分隔符
    lines = text.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Header 行: 以 | 开头和结尾，且至少有 2 个 |
        if stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2:
            if i + 1 < len(lines):
                next_stripped = lines[i + 1].strip()
                # 分隔符行: 每个单元格只包含 -, :, 空格
                sep_cells = [c.strip() for c in next_stripped.split("|")[1:-1] if c.strip()]
                if sep_cells and all(re.match(r"^[:\- ]+$", c) for c in sep_cells):
                    return True
    return False


def _build_markdown_card(text: str) -> dict:
    """构建 Feishu markdown interactive card（schema 2.0）

    Args:
        text: Markdown 正文

    Returns:
        dict: 飞书卡片 JSON 结构
    """
    return {
        "schema": "2.0",
        "config": {"width_mode": "fill"},
        "body": {"elements": [{"tag": "markdown", "content": text}]},
    }


class FeishuSender:
    """飞书消息发送器

    封装 lark-oapi IM API，根据内容自动选择 post 文本或 interactive 卡片。
    """

    def __init__(self, client: Any, config: FeishuAccountConfig):
        """初始化发送器

        Args:
            client: 飞书 SDK Client 实例
            config: 飞书账号配置

        Returns:
            None
        """
        self.client = client  # Lark HTTP 客户端
        self.config = config  # 账号配置（超时、域名等）

    async def send_text(
        self,
        chat_id: str,
        text: str,
        reply_in_thread: bool = False,
        parent_id: Optional[str] = None,
    ) -> FeishuSendResult:
        """发送文本消息

        - 包含代码块或表格 -> 使用 interactive card
        - 普通文本 -> 使用 post 类型

        Args:
            chat_id: 聊天 ID
            text: 文本内容
            reply_in_thread: 是否在话题中回复
            parent_id: 父消息 ID

        Returns:
            FeishuSendResult
        """
        if _should_use_card(text):
            card = _build_markdown_card(text)
            result = await self.send_card(
                chat_id=chat_id,
                card_content=card,
                reply_in_thread=reply_in_thread,
                parent_id=parent_id,
            )
            # card 发送成功（有 message_id）则直接返回
            if result.message_id:
                return result
            # card 失败（空 message_id），降级为 post 类型
            logger.warning(
                "Card message send failed, falling back to post (chat_id=%s text_len=%d)",
                chat_id,
                len(text),
            )

        # 普通文本使用 post 类型
        msg_content = json.dumps({"zh_cn": {"content": [[{"tag": "md", "text": text}]]}})

        return await self._send_message(
            receive_id=chat_id,
            msg_type="post",
            content=msg_content,
            reply_in_thread=reply_in_thread,
            parent_id=parent_id,
        )

    async def send_card(
        self,
        chat_id: str,
        card_content: Dict,
        reply_in_thread: bool = False,
        parent_id: Optional[str] = None,
    ) -> FeishuSendResult:
        """发送卡片消息

        Args:
            chat_id: 聊天 ID
            card_content: 卡片内容（JSON dict）
            reply_in_thread: 是否在话题中回复
            parent_id: 父消息 ID

        Returns:
            FeishuSendResult: 发送结果
        """
        return await self._send_message(
            receive_id=chat_id,
            msg_type="interactive",
            content=json.dumps(card_content),
            reply_in_thread=reply_in_thread,
            parent_id=parent_id,
        )

    async def _send_message(
        self,
        receive_id: str,
        msg_type: str,
        content: str,
        reply_in_thread: bool = False,
        parent_id: Optional[str] = None,
    ) -> FeishuSendResult:
        """通用发送消息接口

        Args:
            receive_id: 接收者 ID
            msg_type: 消息类型
            content: 消息内容
            reply_in_thread: 是否在话题中回复
            parent_id: 父消息 ID

        Returns:
            FeishuSendResult
        """
        # 根据 receive_id 前缀推断 ID 类型（飞书 API 要求 receive_id_type）
        if receive_id.startswith("ou_"):
            receive_id_type = "open_id"
        elif receive_id.startswith("oc_") or receive_id.startswith("p2p_"):
            receive_id_type = "chat_id"
        elif receive_id.startswith("user_"):
            receive_id_type = "user_id"
        else:
            receive_id_type = "open_id"  # 默认按 open_id 处理

        # 构建请求体
        body: Dict[str, Any] = {
            "receive_id": receive_id,
            "msg_type": msg_type,
            "content": content,
        }
        if reply_in_thread and parent_id:
            body["reply_in_thread"] = True
            body["parent_id"] = parent_id

        # 使用 SDK 的 request builder
        from lark_oapi.api.im.v1.model.create_message_request import CreateMessageRequest

        req = CreateMessageRequest.builder().receive_id_type(receive_id_type).request_body(body).build()

        try:
            result = self.client.im.v1.message.create(req)
            if hasattr(result, "__await__"):
                result = await result

            # 解析响应
            msg_id = ""
            err_code = -1
            err_msg = ""

            # 优先从顶层读 code/msg（lark-oapi SDK 标准返回结构）
            if hasattr(result, "code"):
                err_code = result.code
            if hasattr(result, "msg"):
                err_msg = result.msg or ""

            if hasattr(result, "data"):
                data = result.data
                if hasattr(data, "message_id"):
                    msg_id = data.message_id or ""
                elif isinstance(data, dict):
                    msg_id = data.get("message_id", "")
            elif isinstance(result, dict):
                msg_id = result.get("message_id", "")
                # dict 响应时顶层无 .code/.msg，从 dict 读
                if err_code == -1:
                    err_code = result.get("code", -1)
                if not err_msg:
                    err_msg = result.get("msg", "")

            # API 返回了错误（非 0 错误码）或没有 message_id，记录日志
            if err_code != 0 or not msg_id:
                # 诊断：捕获 result 实际结构
                result_type = type(result).__name__
                result_attrs = {a: getattr(result, a) for a in ["code", "msg", "data", "raw"] if hasattr(result, a)}
                logger.warning(
                    "Feishu API returned error: code=%s msg=%s msg_id=%s msg_type=%s [result=%s attrs=%s]",
                    err_code,
                    err_msg,
                    msg_id,
                    msg_type,
                    result_type,
                    result_attrs,
                )

            return FeishuSendResult(message_id=msg_id, chat_id=receive_id)
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            raise
