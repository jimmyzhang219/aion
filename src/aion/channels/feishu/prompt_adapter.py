"""飞书 Agent Prompt 适配器

提供飞书特定的格式提示，用于构建 LLM System Prompt。
"""

from ..adapters import ChannelAgentPromptAdapter


class FeishuAgentPromptAdapter(ChannelAgentPromptAdapter):
    """飞书 Agent Prompt 适配器

    飞书支持 Markdown 格式，可以发送富文本消息（卡片）。
    """

    def get_inbound_formatting_hints(self) -> dict:
        """返回飞书的入站格式提示

        Returns:
            dict: 格式提示
                - text_markup: markdown（飞书支持 Markdown）
                - rules: 格式规则
        """
        return {
            "text_markup": "markdown",
            "rules": [
                "使用 Markdown 格式化响应内容",
                "代码块使用 ```python, ```javascript 等标记",
                "支持有序列表和无序列表",
                "支持加粗、斜体文本",
            ],
        }

    def get_message_tool_hints(self) -> list[str]:
        """返回消息工具提示

        Returns:
            list[str]: 工具提示列表
        """
        return [
            "feishu_text: 发送文本消息到飞书",
            "feishu_card: 发送卡片消息到飞书（用于代码块、表格等复杂格式）",
        ]

    def get_message_tool_capabilities(self) -> list[str]:
        """返回消息工具能力

        Returns:
            list[str]: 工具能力列表
        """
        return [
            "支持发送文本消息",
            "支持发送 Markdown 格式消息",
            "支持发送卡片消息（代码块、表格）",
            "支持回复线程",
            "支持 @ 提及",
        ]

    def get_reaction_guidance(self) -> dict:
        """返回反应指导

        飞书支持消息 reaction（表情回复）。

        Returns:
            dict: 包含 level (minimal/extensive) 和 channel_label
        """
        return {"level": "minimal", "channel_label": "飞书"}
