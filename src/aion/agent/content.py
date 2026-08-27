"""多模态内容块处理工具

提供 `MessageContext.content` 中 ``list[dict]`` 格式内容块的通用处理函数，
将文件路径替换为 base64 数据供 LLM API 使用。
"""

import base64
import logging
import re

from aion.channels.constants import ContentBlockType

logger = logging.getLogger(__name__)


def looks_like_base64(s: str) -> bool:
    """粗略判断字符串是否已经是 base64（而非文件路径）"""
    return bool(re.match(r"^[A-Za-z0-9+/]*={0,2}$", s)) and len(s) > 100


async def resolve_content_blocks(blocks: list[dict]) -> list[dict]:
    """将 content blocks 中的文件路径替换为 base64 数据

    处理后 blocks 中的 ``data`` 从文件路径变为 base64 字符串，
    供 lc_bridge 或 provider 子类进一步转换为 API 格式。

    如果媒体文件无法读取，将该 block 替换为 ``[无法加载的媒体文件]`` 文本，
    避免 API 收到无效 base64 后静默丢弃。

    Args:
        blocks: 内容块列表，每个块含 ``type``、``data``（文件路径）、``mimeType`` 等字段

    Returns:
        处理后的内容块列表
    """
    result: list[dict] = []
    for block in blocks:
        if (
            block.get("type") in (ContentBlockType.IMAGE, ContentBlockType.VIDEO, ContentBlockType.AUDIO)
            and "data" in block
        ):
            path = block["data"]
            if not looks_like_base64(path):
                try:
                    logger.info("[Content] 转换媒体文件 %s", path)
                    with open(path, "rb") as f:
                        raw = f.read()
                    block = dict(block)
                    block["data"] = base64.b64encode(raw).decode("utf-8")
                    result.append(block)
                except (FileNotFoundError, OSError) as e:
                    logger.warning("Failed to read media file %s: %s", path, e)
                    # 无法加载 → 替换为文本提示，避免 API 收到无效 data URL 后静默丢弃
                    mime_label = block.get("mimeType", block.get("type", ContentBlockType.FILE))
                    result.append(
                        {
                            "type": ContentBlockType.TEXT,
                            "text": f"[{mime_label} 文件无法加载: {path}]",
                        }
                    )
            else:
                result.append(block)
        else:
            result.append(block)
    return result
