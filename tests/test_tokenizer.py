"""src/aion/llm/tokenizer 单元测试"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from aion.llm.tokenizer import count_tokens, count_message_tokens, _estimate_fallback


class TestCountTokensBasic:
    """tiktoken 基本正确性（主路径）"""

    def test_count_tokens_english(self):
        """纯英文文本的 token 数应匹配 cl100k_base 固定 mapping"""
        assert count_tokens("Hello, world!") == 4

    def test_count_tokens_chinese(self):
        """中文文本的 token 数"""
        assert count_tokens("你好世界") == 5

    def test_count_tokens_code(self):
        """代码片段的 token 数"""
        assert count_tokens("def foo():\n    return 1") == 7

    def test_count_tokens_mixed(self):
        """中英文混合"""
        assert count_tokens("你好 world 你好") == 6

    def test_count_tokens_emoji(self):
        """emoji 在 cl100k_base 中为 3 tokens"""
        assert count_tokens("🔥") == 3

    def test_count_tokens_long_text(self):
        """长文本不崩溃，返回正整数"""
        long_text = "hello world " * 20000
        result = count_tokens(long_text)
        assert isinstance(result, int) and result > 0


class TestCountMessageTokens:
    """count_message_tokens 消息结构计数"""

    def test_empty_list(self):
        """空消息列表"""
        assert count_message_tokens([]) == 0

    def test_single_message(self):
        """单条消息 = count_tokens(content)"""
        msgs = [{"role": "user", "content": "hello world"}]
        assert count_message_tokens(msgs) == count_tokens("hello world")

    def test_multiple_messages(self):
        """多条消息累计"""
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        assert count_message_tokens(msgs) == count_tokens("hello") + count_tokens("world")

    def test_missing_content_field(self):
        """缺少 content 字段的消息应返回 0（不计入总计）"""
        msgs = [
            {"role": "user"},  # 无 content 字段
            {"role": "assistant", "content": "result"},
        ]
        assert count_message_tokens(msgs) == count_tokens("result")

    def test_multimodal_content_blocks(self):
        """多模态 list[dict] 格式：text 块计文本，image 块按粗估计入"""
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe this"},
                    {"type": "image", "image_url": {"url": "data:image/..."}},
                ],
            }
        ]
        assert count_message_tokens(msgs) == count_tokens("describe this") + 100

    def test_extra_fields(self):
        """额外字段（name / tool_call_id）不影响计数"""
        msgs = [
            {"role": "system", "content": "you are", "name": "system_name"},
            {"role": "tool", "content": "output", "tool_call_id": "c1"},
        ]
        assert count_message_tokens(msgs) == count_tokens("you are") + count_tokens("output")


class TestCountTokensEdgeCases:
    """边界与输入防御"""

    def test_empty_string(self):
        """空字符串"""
        assert count_tokens("") == 0

    def test_none_input(self):
        """None 返回 0"""
        assert count_tokens(None) == 0  # type: ignore

    def test_non_string_input(self):
        """非字符串返回 0"""
        assert count_tokens(123) == 0  # type: ignore
        assert count_tokens([]) == 0  # type: ignore
        assert count_tokens({}) == 0  # type: ignore


class TestFallback:
    """_estimate_fallback 回退路径验证（当前环境 tiktoken 可用，回退不会触发）"""

    def test_fallback_empty(self):
        """空串回退返回 0"""
        assert _estimate_fallback("") == 0

    def test_fallback_ascii(self):
        """纯 ASCII：每个字符 1 // 4 = 0.25 token"""
        assert _estimate_fallback("hello") == 1  # 5 // 4 = 1

    def test_fallback_cjk(self):
        """纯 CJK：每个字符 4 // 4 = 1 token"""
        assert _estimate_fallback("你好") == 2  # 8 // 4 = 2

    def test_fallback_mixed(self):
        """中英混合"""
        assert _estimate_fallback("a你好b") == 2  # (1+4+4+1) // 4 = 2


@pytest.mark.integration
class TestAPITokenComparison:
    """对比 count_message_tokens 与 LLM API 返回的 prompt_tokens（需 API key）"""

    async def test_count_tokens_vs_deepseek_api(self):
        from aion.config.loader import load_config
        from aion.llm.factory import create_llm
        from aion.llm.lc_bridge import dict_messages_to_lc

        cfg = load_config()
        provider_cfg = cfg.models.get("deepseek", {})
        if not provider_cfg.get("apiKey"):
            pytest.skip("no deepseek api key configured")
        llm = create_llm("deepseek", provider_cfg)

        messages = [
            {
                "role": "system",
                "content": "You are a knowledgeable assistant that helps users understand technical concepts in computer science, programming, and software engineering.",
            },
            {
                "role": "user",
                "content": "Explain the key differences between TCP and UDP protocols. Include details about connection establishment, reliability guarantees, error checking, flow control, congestion control, ordering of packets, and typical use cases for each protocol. Also discuss scenarios where you might choose one over the other.",
            },
        ]
        lc_messages = dict_messages_to_lc(messages)
        response = await llm.ainvoke(lc_messages)
        estimated = count_message_tokens(messages)
        actual = response.usage_metadata.get("input_tokens", 0) if response.usage_metadata else 0
        assert abs(estimated - actual) / actual < 0.05
