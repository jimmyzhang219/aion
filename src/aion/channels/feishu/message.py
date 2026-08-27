"""飞书消息解析

设计文档: docs/design/feishu-channel.md 第 5.3 节
"""

import json
from typing import Optional, Any, Dict
from ..constants import ContentBlockType
from .types import FeishuMessageContext, MentionTarget, FeishuChatType


def parse_message_content(content: str, msg_type: str) -> str:
    """解析飞书消息内容

    Args:
        content: 消息内容（JSON 字符串）
        msg_type: 消息类型

    Returns:
        解析后的纯文本内容
    """
    if msg_type == "text":
        try:
            data = json.loads(content)
            return data.get("text", "")
        except (json.JSONDecodeError, TypeError):
            return content

    elif msg_type == "post":
        try:
            data = json.loads(content)
            return extract_text_from_post(data)
        except (json.JSONDecodeError, TypeError):
            return content

    elif msg_type == "image":
        return "[图片]"
    elif msg_type == "audio":
        return "[语音]"
    elif msg_type in ("video", "media"):
        return "[视频]"
    elif msg_type == "file":
        return "[文件]"
    elif msg_type == "sticker":
        return "[表情包]"
    elif msg_type == "card":
        return "[卡片消息]"
    elif msg_type == "share_chat":
        return "[分享群聊]"
    elif msg_type == "share_user":
        return "[分享用户]"
    else:
        return content


def _walk_post_texts(post_data: dict) -> tuple[list[str], list[dict]]:
    """遍历 post content 段落，返回 (texts, media_elements) 保持顺序

    优先读取 locale 包裹（zh_cn → en_us → ja_jp），
    无 locale 内容时回退到顶层 ``content``。
    适用于飞书 API 返回的两种结构：
    - 有 locale: ``{"zh_cn": {"content": [[...]]}}``
    - 无 locale: ``{"content": [[...]]}``
    """
    texts: list[str] = []
    media_elements: list[dict] = []

    content = None
    for locale_key in ("zh_cn", "en_us", "ja_jp"):
        locale = post_data.get(locale_key)
        if isinstance(locale, dict):
            content = locale.get("content")
            if isinstance(content, list):
                break
            content = None

    if content is None and isinstance(post_data.get("content"), list):
        content = post_data["content"]

    if content is None:
        return texts, media_elements

    for paragraph in content:
        if not isinstance(paragraph, list):
            continue
        for item in paragraph:
            if not isinstance(item, dict):
                continue
            tag = item.get("tag", "")
            if tag == "text":
                t = (item.get("text") or "").strip()
                if t:
                    texts.append(t)
            elif tag == "img":
                fk = item.get("image_key", "")
                if fk:
                    media_elements.append({"type": ContentBlockType.IMAGE, "file_key": fk, "mimeType": "image/jpeg"})
            elif tag == "video":
                fk = item.get("file_key", "") or item.get("image_key", "")
                if fk:
                    media_elements.append({"type": ContentBlockType.VIDEO, "file_key": fk, "mimeType": "video/mp4"})
            elif tag == "media":
                fk = item.get("file_key", "")
                if fk:
                    media_elements.append(
                        {
                            "type": "media",
                            "file_key": fk,
                            "file_name": item.get("file_name", "") or "",
                            "mimeType": "application/octet-stream",
                        }
                    )

    return texts, media_elements


def extract_text_from_post(post_data: Dict) -> str:
    """从飞书富文本帖子中提取纯文本"""
    raw = post_data.get("post", post_data)
    texts, _ = _walk_post_texts(raw)
    return "\n".join(texts)


def parse_post_elements(post_data: dict) -> list[dict]:
    """按原文顺序解析 post 内容为元素列表

    保持段落内元素的原始顺序，每项含 ``type``：
    - text 类型： ``{"type": "text", "text": "..."}``
    - 媒体类型： ``{"type": "image|video", "file_key": "...", "mimeType": "..."}``
    - media 标签： ``{"type": "media", "file_key": "...", "file_name": "...", "mimeType": "..."}``（由 channel 层下载后检测实际类型）
    """
    raw = post_data.get("post", post_data)
    content = None
    for locale_key in ("zh_cn", "en_us", "ja_jp"):
        locale = raw.get(locale_key)
        if isinstance(locale, dict):
            content = locale.get("content")
            if isinstance(content, list):
                break
            content = None
    if content is None and isinstance(raw.get("content"), list):
        content = raw["content"]
    if content is None:
        return []
    elements: list[dict] = []
    for paragraph in content:
        if not isinstance(paragraph, list):
            continue
        for item in paragraph:
            if not isinstance(item, dict):
                continue
            tag = item.get("tag", "")
            if tag == "text":
                t = (item.get("text") or "").strip()
                if t:
                    elements.append({"type": ContentBlockType.TEXT, "text": t})
            elif tag == "img":
                fk = item.get("image_key", "")
                if fk:
                    elements.append({"type": ContentBlockType.IMAGE, "file_key": fk, "mimeType": "image/jpeg"})
            elif tag == "video":
                fk = item.get("file_key", "") or item.get("image_key", "")
                if fk:
                    elements.append({"type": ContentBlockType.VIDEO, "file_key": fk, "mimeType": "video/mp4"})
            elif tag == "media":
                fk = item.get("file_key", "")
                if fk:
                    elements.append(
                        {
                            "type": "media",
                            "file_key": fk,
                            "file_name": item.get("file_name", "") or "",
                            "mimeType": "application/octet-stream",
                        }
                    )
    return elements


def _walk_post_paragraphs(
    post_data: dict,
    seen: set[str],
    elements: list[dict],
) -> None:
    """遍历 post content 段落，提取 img/video/media 元素"""
    content = post_data.get("content")
    if not isinstance(content, list):
        return
    for paragraph in content:
        if not isinstance(paragraph, list):
            continue
        for item in paragraph:
            if not isinstance(item, dict):
                continue
            tag = item.get("tag", "")
            if tag == "img":
                fk = item.get("image_key", "")
                if fk and fk not in seen:
                    seen.add(fk)
                    elements.append({"type": ContentBlockType.IMAGE, "file_key": fk, "mimeType": "image/jpeg"})
            elif tag == "video":
                fk = item.get("file_key", "") or item.get("image_key", "")
                if fk and fk not in seen:
                    seen.add(fk)
                    elements.append({"type": ContentBlockType.VIDEO, "file_key": fk, "mimeType": "video/mp4"})
            elif tag == "media":
                fk = item.get("file_key", "")
                if fk and fk not in seen:
                    seen.add(fk)
                    elements.append(
                        {
                            "type": "media",
                            "file_key": fk,
                            "mimeType": "application/octet-stream",
                            "file_name": item.get("file_name", "") or "",
                        }
                    )


def _resolve_locale_content(post_data: dict) -> list[list[dict]] | None:
    """从 locale 包裹的 post 数据中提取 content 段落"""
    for locale_key in ("zh_cn", "en_us", "ja_jp"):
        locale = post_data.get(locale_key)
        if isinstance(locale, dict):
            content = locale.get("content")
            if isinstance(content, list):
                return content
    return None


def extract_post_media_elements(post_data: dict) -> list[dict]:
    """从飞书 post 富文本中提取媒体元素（image/video/media）

    返回的媒体元素顺序按原文出现位置排列。
    """
    elements: list[dict] = []
    seen: set[str] = set()

    if isinstance(post_data.get("content"), list):
        _walk_post_paragraphs(post_data, seen, elements)
        if elements:
            return elements

    wrapped = post_data.get("post")
    if isinstance(wrapped, dict):
        if content_list := _resolve_locale_content(wrapped):
            for paragraph in content_list:
                _walk_post_paragraphs({"content": paragraph}, seen, elements)
            if elements:
                return elements

    if content_list := _resolve_locale_content(post_data):
        for paragraph in content_list:
            _walk_post_paragraphs({"content": paragraph}, seen, elements)

    return elements


def parse_feishu_message_context(event_data: Dict[str, Any], bot_open_id: Optional[str] = None) -> FeishuMessageContext:
    """解析飞书消息事件为消息上下文"""
    sender = event_data.get("sender", {})
    sender_id_obj = sender.get("sender_id", {})
    sender_open_id = sender_id_obj.get("open_id", "")
    sender_user_id = sender_id_obj.get("user_id", "")

    message = event_data.get("message", {})
    chat = event_data.get("chat", {})

    raw_content = message.get("content", "{}")
    msg_type = message.get("message_type", "text")
    content = parse_message_content(raw_content, msg_type)

    raw_content_dict: dict | None = None
    if msg_type in ("image", "video", "audio", "file", "post", "media"):
        try:
            raw_content_dict = json.loads(raw_content)
        except (json.JSONDecodeError, TypeError):
            pass

    mentions = message.get("mentions", [])
    mentioned_bot = False
    has_any_mention = len(mentions) > 0

    if bot_open_id and mentions:
        for m in mentions:
            m_id = m.get("id", {})
            if m_id.get("open_id") == bot_open_id:
                mentioned_bot = True
                break

    mention_targets = None
    if mentions:
        mention_targets = []
        for m in mentions:
            m_id = m.get("id", {})
            mention_targets.append(
                MentionTarget(
                    open_id=m_id.get("open_id"),
                    user_id=m_id.get("user_id"),
                    union_id=m_id.get("union_id"),
                    name=m.get("name"),
                    key=m.get("key"),
                )
            )

    root_id = message.get("root_id") or None
    parent_id = message.get("parent_id") or None
    thread_id = message.get("thread_id") or None

    chat_type_str = chat.get("chat_type", "p2p")
    chat_type: FeishuChatType = (
        "p2p" if chat_type_str == "p2p" else ("group" if chat_type_str == "group" else "private")
    )

    return FeishuMessageContext(
        chat_id=message.get("chat_id", ""),
        message_id=message.get("message_id", ""),
        sender_id=sender_user_id or sender_open_id,
        sender_open_id=sender_open_id,
        sender_name=sender.get("sender_nickname"),
        chat_type=chat_type,
        mentioned_bot=mentioned_bot,
        has_any_mention=has_any_mention,
        content=content,
        content_type=msg_type,
        raw_content=raw_content_dict,
        root_id=root_id,
        parent_id=parent_id,
        thread_id=thread_id,
        mentions=mention_targets,
    )


def extract_message_id(payload: dict) -> str:
    event = payload.get("event", payload)
    message = event.get("message", {}) if isinstance(event, dict) else {}
    return message.get("message_id", "")


def extract_chat_id(payload: dict) -> str:
    event = payload.get("event", payload)
    message = event.get("message", {}) if isinstance(event, dict) else {}
    return message.get("chat_id", "")
