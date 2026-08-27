"""Channel 公共常量

所有 Channel 实现共享的常量，包括媒体文件大小限制等。
"""

from enum import Enum


class ContentBlockType(str, Enum):
    """标准多模态 content block 类型标识。

    ``MessageContext.content`` 为 ``list[dict]`` 时，每个 dict 的 ``type`` 字段
    必须是此枚举的成员。各 Channel 在将平台消息转换为统一格式时据此构建 block。
    """

    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    FILE = "file"


# ── 媒体文件大小限制（字节） ──

# 图片：LLM API 对 base64 图片的常见限制
IMAGE_MAX_BYTES = 20 * 1024 * 1024  # 20MB

# 视频/音频：LLM API 对 base64 视频/音频的请求体限制
# base64 编码约膨胀 33%，实际传输大小约为 1.33 倍
# 超过此限制的不传给 LLM。
# 8MB raw → base64 ~10.6MB + 其他内容 ~0.5MB → ~11MB，适配常见 API 限制
VIDEO_MAX_BYTES = 8 * 1024 * 1024  # 8MB

# 通用文件
FILE_MAX_BYTES = 50 * 1024 * 1024  # 50MB
