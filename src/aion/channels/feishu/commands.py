"""飞书命令处理适配器（仅接口方法，执行逻辑在 dispatch.py）"""

import logging
from ..adapters import ChannelCommandAdapter

logger = logging.getLogger(__name__)


class FeishuCommandAdapter(ChannelCommandAdapter):
    """飞书命令处理适配器 — 命令识别与解析"""

    pass
