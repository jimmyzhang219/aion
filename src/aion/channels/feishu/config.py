"""飞书 Channel 配置 Schema

设计文档: docs/design/feishu-channel.md 第 4 节
"""

from pydantic import BaseModel, Field
from typing import Optional, Dict, Literal


class FeishuAccountConfig(BaseModel):
    """单账号配置

    所有字段都是可选的，缺失的字段会从顶层 FeishuConfig 继承。
    """

    # === 基本 ===
    enabled: bool = True  # 该账号是否启用
    name: Optional[str] = Field(None, description="账号显示名")

    # === 凭证 ===
    appId: str = Field(..., description="飞书应用 App ID")
    appSecret: str = Field(..., description="飞书应用 App Secret")
    encryptKey: Optional[str] = Field(None, description="加密密钥（Webhook 模式需要）")
    verificationToken: Optional[str] = Field(None, description="验证 Token（Webhook 模式需要）")

    # === 连接 ===
    domain: Literal["feishu", "lark"] = "feishu"  # API 域名：国内 feishu / 国际 lark
    connectionMode: Literal["websocket", "webhook"] = "websocket"  # 长连接或 Webhook
    webhookHost: str = "127.0.0.1"  # Webhook 监听地址
    webhookPort: int = 19090  # Webhook 监听端口
    webhookPath: str = "/feishu/events"  # Webhook 路径

    # === 策略 ===
    dmPolicy: Literal["open", "pairing", "allowlist"] = "pairing"  # 私聊准入策略
    groupPolicy: Literal["open", "allowlist", "disabled"] = "allowlist"  # 群聊准入策略
    requireMention: bool = True  # 群聊是否必须 @ 机器人才响应

    # === 功能开关 ===
    reactionNotifications: Literal["off", "own", "all"] = "own"  # 反应通知范围
    replyInThread: bool = False  # 默认是否在话题线程中回复
    typingIndicator: bool = True  # 是否显示「正在输入」反应
    resolveSenderNames: bool = True  # 是否解析发送者显示名

    # === 限制 ===
    historyLimit: int = 20  # 群聊历史消息条数上限
    dmHistoryLimit: int = 50  # 私聊历史消息条数上限
    httpTimeoutMs: int = 30000  # HTTP 请求超时（毫秒）


class FeishuConfig(BaseModel):
    """飞书频道主配置

    支持单账号（顶层 appId/appSecret）与多账号（accounts 字典）两种模式。
    """

    enabled: bool = Field(False, description="是否启用飞书频道")
    defaultAccount: str = "default"  # 多账号模式下默认使用的账号 ID

    # === 单账号模式凭证（也可直接写在顶层，便于配置） ===
    appId: Optional[str] = Field(None, description="飞书应用 App ID")
    appSecret: Optional[str] = Field(None, description="飞书应用 App Secret")
    encryptKey: Optional[str] = Field(None, description="加密密钥")
    verificationToken: Optional[str] = Field(None, description="验证 Token")
    domain: Literal["feishu", "lark"] = "feishu"  # API 域名
    connectionMode: Literal["websocket", "webhook"] = "websocket"  # 连接模式

    # === 多账号配置 ===
    accounts: Dict[str, FeishuAccountConfig] = Field(default_factory=dict)

    def get_active_account(self) -> FeishuAccountConfig:
        """获取活跃账号配置

        多账号模式取 defaultAccount；单账号模式从顶层 appId/appSecret 构建。

        Returns:
            FeishuAccountConfig: 当前使用的账号配置

        Raises:
            ValueError: 缺少 appId/appSecret 时抛出
        """
        if self.accounts:
            return self.accounts.get(
                self.defaultAccount,
                FeishuAccountConfig(
                    appId=self.appId or "",
                    appSecret=self.appSecret or "",
                ),  # type: ignore[call-arg]
            )

        # 单账号模式
        if self.appId and self.appSecret:
            return FeishuAccountConfig(
                appId=self.appId,
                appSecret=self.appSecret,
                encryptKey=self.encryptKey,
                verificationToken=self.verificationToken,
                domain=self.domain,
                connectionMode=self.connectionMode,
            )  # type: ignore[call-arg]

        raise ValueError("Feishu config not properly configured: missing appId/appSecret")

    def is_enabled(self) -> bool:
        """是否启用飞书频道

        Returns:
            bool: enabled 字段值
        """
        return self.enabled
