"""飞书媒体文件下载 — 图片/视频/文档 → 系统临时目录

流程：
1. 调飞书 API 下载媒体二进制
2. 写入系统临时目录（``tempfile.NamedTemporaryFile``）
3. 返回绝对路径，供下游 AgentLoop 做 base64 编码
"""

import logging
import tempfile
from pathlib import Path
from typing import Any, Optional

from ..constants import ContentBlockType

logger = logging.getLogger(__name__)


# MIME → 文件扩展名映射（用于给临时文件重命名正确后缀）
_MIME_EXT_MAP: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/bmp": ".bmp",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/avi": ".avi",
    "video/webm": ".webm",
    "video/x-matroska": ".mkv",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/flac": ".flac",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/x-caf": ".caf",
}


_MIME_MAP: dict[str, str] = {
    ContentBlockType.IMAGE.value: "image/jpeg",
    ContentBlockType.VIDEO.value: "video/mp4",
    ContentBlockType.AUDIO.value: "audio/mp4",
    ContentBlockType.FILE.value: "application/octet-stream",
}

_EXT_MAP: dict[str, str] = {
    ContentBlockType.IMAGE.value: ".jpg",
    ContentBlockType.VIDEO.value: ".mp4",
    ContentBlockType.AUDIO.value: ".mp4",
    ContentBlockType.FILE.value: ".bin",
}


def _get_mime(file_type: str, raw_content: Optional[dict] = None) -> str:
    """获取媒体文件的 MIME 类型"""
    mime = _MIME_MAP.get(file_type, "application/octet-stream")
    # 如果飞书返回了文件扩展名，可以据此精确判断（预留）
    return mime


def _get_suffix(file_type: str) -> str:
    """获取临时文件后缀"""
    return _EXT_MAP.get(file_type, ".bin")


def detect_mime_from_bytes(data: bytes) -> tuple[str, str]:
    """用 puremagic 从文件内容检测 MIME 类型和内部类型。

    Returns:
        (mime_type, internal_type) — 如 (\"video/mp4\", \"video\")。
        无法识别时返回 (\"application/octet-stream\", \"file\")。
    """
    try:
        import puremagic

        results = puremagic.magic_string(data)
        mime = results[0].mime_type if results else "application/octet-stream"
    except Exception:
        mime = "application/octet-stream"

    if mime.startswith("image/"):
        return mime, ContentBlockType.IMAGE.value
    if mime.startswith("video/"):
        return mime, ContentBlockType.VIDEO.value
    if mime.startswith("audio/"):
        return mime, ContentBlockType.AUDIO.value
    return "application/octet-stream", ContentBlockType.FILE.value


def rename_with_mime_ext(file_path: str, mime: str) -> str:
    """将临时文件重命名为正确的扩展名。

    Args:
        file_path: 当前文件路径（如 .../tmpxxx.bin）
        mime: 检测到的 MIME 类型（如 "video/mp4"）

    Returns:
        重命名后的文件路径；如果扩展名已正确或重命名失败，返回原路径。
    """
    ext = _MIME_EXT_MAP.get(mime)
    if not ext:
        return file_path
    old = Path(file_path)
    if old.suffix == ext:
        return file_path
    new_path = old.with_suffix(ext)
    try:
        old.rename(new_path)
        return str(new_path)
    except OSError:
        return file_path


async def download_feishu_media(
    file_key: str,
    file_type: str,
    client: Any,  # Lark Client 实例
    raw_content: Optional[dict] = None,
) -> Optional[str]:
    """下载飞书媒体文件到系统临时目录

    Args:
        file_key: 飞书内部资源 ID（image_key / file_key）
        file_type: 媒体类型（``"image"`` / ``"video"`` / ``"file"``）
        client: Lark Client 实例（由 ``create_feishu_client`` 创建）
        raw_content: 原始消息内容（预留，后续可用来获取更精确的文件信息）

    Returns:
        临时文件绝对路径，下载失败时返回 ``None``
    """
    try:
        if file_type == "image":
            from lark_oapi.api.im.v1.model import GetImageRequest

            req = GetImageRequest.builder().image_key(file_key).build()
            resp = client.im.v1.image.get(req)
        else:
            # file / video / audio → 使用飞书文件 API
            from lark_oapi.api.im.v1.model import GetFileRequest

            req = GetFileRequest.builder().file_key(file_key).build()
            resp = client.im.v1.file.get(req)

        # lark_oapi 的响应结构：resp.data 可能为 bytes 或 StreamingBody
        data = getattr(resp, "data", None)
        if data is None:
            logger.warning("Feishu media download returned empty data for %s (%s)", file_key, file_type)
            return None

        # 处理不同返回格式
        raw_bytes: Optional[bytes] = None
        if isinstance(data, bytes):
            raw_bytes = data
        elif isinstance(data, memoryview):
            raw_bytes = bytes(data)
        elif hasattr(data, "read"):  # file-like object
            raw_bytes = data.read()
        elif isinstance(data, dict):
            # 某些 SDK 版本返回 dict
            raw_bytes = data.get("data") or data.get("content")

        if raw_bytes is None:
            logger.warning("Unrecognized response format from Feishu media API for %s", file_key)
            return None

        # 写入临时文件：优先使用飞书返回的原始文件名后缀
        file_name = (raw_content or {}).get("file_name", "")
        orig_ext = Path(file_name).suffix
        suffix = orig_ext if orig_ext else _get_suffix(file_type)
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
            f.write(raw_bytes)
            file_path = f.name

        logger.info(
            "Downloaded feishu media: type=%s key=%s size=%d bytes -> %s",
            file_type,
            file_key,
            len(raw_bytes),
            file_path,
        )
        return file_path

    except Exception as e:
        logger.error("Failed to download feishu media %s (%s): %s", file_key, file_type, e)
        return None


async def download_feishu_message_resource(
    message_id: str,
    file_key: str,
    resource_type: str,
    client: Any,
) -> Optional[str]:
    """下载飞书消息资源（富文本内联图片/视频/文件）

    使用 ``message_resource.get()`` API，适用于 post 富文本中的 ``img``/``media`` 元素。
    与 ``download_feishu_media`` 的区别：需要 ``message_id`` + ``file_key``。

    Args:
        message_id: 飞书消息 ID
        file_key: 资源 file_key（post img 的 image_key / media 的 file_key）
        resource_type: ``"image"`` 或 ``"file"``
        client: Lark Client 实例

    Returns:
        临时文件绝对路径，下载失败时返回 ``None``
    """
    from lark_oapi.api.im.v1.model import GetMessageResourceRequest

    try:
        req = GetMessageResourceRequest.builder().message_id(message_id).file_key(file_key).type(resource_type).build()
        resp = client.im.v1.message_resource.get(req)

        if not resp.success():
            logger.warning("Feishu message resource download failed: %s msg=%s", file_key, resp.msg)
            return None

        data_io = resp.file
        if data_io is None:
            logger.warning("Feishu message resource download returned empty: %s", file_key)
            return None

        raw_bytes = data_io.read()

        suffix = _get_suffix(resource_type)
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
            f.write(raw_bytes)
            file_path = f.name

        logger.info(
            "Downloaded feishu message resource: key=%s size=%d -> %s",
            file_key,
            len(raw_bytes),
            file_path,
        )
        return file_path

    except Exception as e:
        logger.error("Failed to download feishu message resource %s: %s", file_key, e)
        return None
