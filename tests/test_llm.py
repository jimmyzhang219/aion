"""LLM 工厂与 Provider 单元测试"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from langchain_openai import ChatOpenAI

from aion.llm.factory import create_llm
from aion.llm.providers import ChatDeepSeekV4
from aion.llm.providers.maas import ChatMaaS


class TestLLMFactory:
    """create_llm 工厂函数测试"""

    def test_create_deepseek_v4(self):
        """model=deepseek-v4-* 时应返回 ChatDeepSeekV4 实例"""
        config = {"model": "deepseek-v4-flash", "apiKey": "sk-test"}
        llm = create_llm("deepseek", config)
        assert isinstance(llm, ChatDeepSeekV4)

    def test_create_openai(self):
        """非 deepseek-v4 模型应返回 ChatOpenAI 实例"""
        config = {"model": "gpt-4o", "apiKey": "sk-test"}
        llm = create_llm("openai", config)
        assert isinstance(llm, ChatOpenAI)

    def test_unknown_provider_defaults_to_chatopenai(self):
        """未知 provider 名称不应抛异常，应默认返回 ChatOpenAI"""
        config = {"model": "unknown-model", "apiKey": "sk-test"}
        llm = create_llm("my-custom-provider", config)
        assert isinstance(llm, ChatOpenAI)

    def test_deepseek_v4_uses_chatdeepseekv4(self):
        """deepseek-v4 模型应使用 ChatDeepSeekV4 类"""
        config = {"model": "deepseek-v4-flash", "apiKey": "sk-test"}
        llm = create_llm("deepseek", config)
        assert isinstance(llm, ChatDeepSeekV4)

    def test_alicloud_provider_uses_chatmaas(self):
        """provider 名为 alicloud 时应返回 ChatMaaS 实例"""
        config = {
            "model": "glm-5.1",
            "apiKey": "sk-test",
            "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        }
        llm = create_llm("alicloud", config)
        assert isinstance(llm, ChatMaaS)

    def test_alicloud_provider_case_insensitive(self):
        """provider 名称大小写不敏感"""
        config = {
            "model": "glm-5.1",
            "apiKey": "sk-test",
            "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        }
        llm = create_llm("ALICLOUD", config)
        assert isinstance(llm, ChatMaaS)

    def test_case_insensitive_provider(self):
        """provider 名称应大小写不敏感"""
        config = {"model": "gpt-4o", "apiKey": "sk-test"}
        llm = create_llm("OPENAI", config)
        assert isinstance(llm, ChatOpenAI)
