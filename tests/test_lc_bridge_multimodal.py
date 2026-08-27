"""lc_bridge 多模态内容块转换单元测试"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class TestContentToLcFormat:
    """_content_to_lc_format 转换测试"""

    def test_str_passthrough(self):
        """str 类型原样返回"""
        from aion.llm.lc_bridge import _content_to_lc_format

        result = _content_to_lc_format("hello world")
        assert result == "hello world"

    def test_text_block(self):
        """text content block → {"type": "text", "text": "..."}"""
        from aion.llm.lc_bridge import _content_to_lc_format

        result = _content_to_lc_format([{"type": "text", "text": "hello"}])
        assert result == [{"type": "text", "text": "hello"}]

    def test_image_block(self):
        """image content block → image_url 格式"""
        from aion.llm.lc_bridge import _content_to_lc_format

        result = _content_to_lc_format(
            [
                {"type": "image", "data": "/9j/4AAQ", "mimeType": "image/jpeg"},
            ]
        )
        assert result == [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQ"}},
        ]

    def test_image_default_mime(self):
        """image block 无 mimeType 时默认为 image/jpeg"""
        from aion.llm.lc_bridge import _content_to_lc_format

        result = _content_to_lc_format(
            [
                {"type": "image", "data": "base64data"},
            ]
        )
        assert result == [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,base64data"}},
        ]

    def test_video_block_preserved(self):
        """video content block 保持内部格式，不做 OpenAI 标准转换"""
        from aion.llm.lc_bridge import _content_to_lc_format

        block = {"type": "video", "data": "base64video", "mimeType": "video/mp4"}
        result = _content_to_lc_format([block])
        assert result == [block]

    def test_audio_block_preserved(self):
        """audio content block 保持内部格式"""
        from aion.llm.lc_bridge import _content_to_lc_format

        block = {"type": "audio", "data": "base64audio", "mimeType": "audio/mp4"}
        result = _content_to_lc_format([block])
        assert result == [block]

    def test_mixed_content(self):
        """混合 text + image + video 按顺序转换"""
        from aion.llm.lc_bridge import _content_to_lc_format

        result = _content_to_lc_format(
            [
                {"type": "text", "text": "看图"},
                {"type": "image", "data": "img1", "mimeType": "image/png"},
                {"type": "text", "text": "还有这个"},
                {"type": "video", "data": "vid1", "mimeType": "video/mp4"},
            ]
        )
        assert result == [
            {"type": "text", "text": "看图"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,img1"}},
            {"type": "text", "text": "还有这个"},
            {"type": "video", "data": "vid1", "mimeType": "video/mp4"},
        ]


class TestDictMessagesToLcMultimodal:
    """dict_messages_to_lc 多模态 user 消息测试"""

    def test_text_user_message(self):
        """纯文本 user 消息仍生成正确的 HumanMessage"""
        from aion.llm.lc_bridge import dict_messages_to_lc
        from langchain_core.messages import HumanMessage

        result = dict_messages_to_lc([{"role": "user", "content": "hello"}])
        assert len(result) == 1
        assert isinstance(result[0], HumanMessage)
        assert result[0].content == "hello"

    def test_multimodal_user_message(self):
        """多模态 user 消息生成带 content list 的 HumanMessage"""
        from aion.llm.lc_bridge import dict_messages_to_lc
        from langchain_core.messages import HumanMessage

        result = dict_messages_to_lc(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "这是什么"},
                        {"type": "image", "data": "imgdata", "mimeType": "image/jpeg"},
                    ],
                }
            ]
        )
        assert len(result) == 1
        assert isinstance(result[0], HumanMessage)
        content = result[0].content
        assert isinstance(content, list)
        assert len(content) == 2
        assert content[0] == {"type": "text", "text": "这是什么"}
        assert content[1] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,imgdata"}}
