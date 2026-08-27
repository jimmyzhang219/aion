"""飞书 SDK 客户端封装

设计文档: docs/design/feishu-channel.md 第 10.2 节

Python SDK (lark-oapi) 文档: https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/sdk/python-sdk
"""

import logging
from typing import Optional, Dict, Any

# 飞书 SDK 可用性标志（import 失败时为 False）
LARK_SDK_AVAILABLE = False
Lark: Any = None
Client: Any = None
AppType: Any = None
# WSClient = None  # 未使用

try:
    import lark_oapi as Lark  # type: ignore[no-redef]
    from lark_oapi import Client, AppType  # type: ignore[no-redef]

    LARK_SDK_AVAILABLE = True
except ImportError:
    LARK_SDK_AVAILABLE = False

from .config import FeishuAccountConfig


logger = logging.getLogger(__name__)

# HTTP 超时常量（毫秒）
FEISHU_HTTP_TIMEOUT_MS = 30_000  # 默认 30 秒


class FeishuClientCache:
    """飞书客户端缓存（单例模式）

    同一 account_id 复用同一个 Lark Client，避免重复创建连接。
    """

    _instance: Optional["FeishuClientCache"] = None  # 单例
    _clients: Dict[str, Any] = {}  # account_id -> Client 实例

    def __new__(cls):
        """确保全局仅存在一个缓存实例

        Returns:
            FeishuClientCache: 单例对象
        """
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def get(self, account_id: str) -> Optional[Any]:
        """获取已缓存的客户端

        Args:
            account_id: 账号 ID

        Returns:
            Optional[Any]: Lark Client 实例，未缓存时返回 None
        """
        return self._clients.get(account_id)

    def set(self, account_id: str, client: Any) -> None:
        """缓存客户端实例

        Args:
            account_id: 账号 ID
            client: Lark Client 实例

        Returns:
            None
        """
        self._clients[account_id] = client

    def clear(self, account_id: Optional[str] = None) -> None:
        """清除客户端缓存

        Args:
            account_id: 指定账号 ID 时仅清除该账号；为 None 时清空全部

        Returns:
            None
        """
        if account_id:
            self._clients.pop(account_id, None)
        else:
            self._clients.clear()


def resolve_domain(domain: str) -> str:
    """解析飞书 API 域名

    Args:
        domain: 配置中的域名标识，"feishu" 或 "lark"

    Returns:
        str: 完整的 API 域名 URL
    """
    if domain == "lark":
        return Lark.LARK_DOMAIN if LARK_SDK_AVAILABLE else "https://open.larksuite.com"
    return Lark.FEISHU_DOMAIN if LARK_SDK_AVAILABLE else "https://open.feishu.cn"


def create_feishu_client(config: FeishuAccountConfig, account_id: str = "default") -> Any:
    """创建飞书 HTTP 客户端

    使用缓存确保同一账号只创建一个客户端实例。

    Args:
        config: 飞书账号配置
        account_id: 账号 ID

    Returns:
        Lark Client 实例
    """
    if not LARK_SDK_AVAILABLE:
        raise ImportError("lark-oapi not installed. Run: pip install lark-oapi")

    cache = FeishuClientCache()

    # 检查缓存
    cached = cache.get(account_id)
    if cached:
        return cached

    # 创建新客户端（使用 Builder 模式）
    # AppType.SELF = 1 (自建应用), AppType.ISV = 2 (商店应用)
    client = (
        Client.builder()
        .app_id(config.appId)
        .app_secret(config.appSecret)
        .app_type(AppType.SELF)
        .domain(resolve_domain(config.domain))
        .timeout(FEISHU_HTTP_TIMEOUT_MS / 1000)
        .build()
    )

    # 缓存
    cache.set(account_id, client)
    return client


def get_feishu_client(account_id: str = "default") -> Optional[Any]:
    """获取已缓存的飞书客户端

    Args:
        account_id: 账号 ID

    Returns:
        客户端实例，如果不存在则返回 None
    """
    return FeishuClientCache().get(account_id)


def add_typing_indicator(message_id: str, chat_id: str, account_id: str = "default") -> Optional[str]:
    """为消息添加 Typing Indicator

    Args:
        message_id: 消息 ID
        chat_id: 聊天 ID
        account_id: 账号 ID

    Returns:
        reaction_id 如果成功，否则 None
    """
    from .typing_indicator import get_typing_store

    # 防止重复添加（消息重试场景）
    store = get_typing_store()
    if store.get(message_id):
        return None  # 已存在则跳过

    client = get_feishu_client(account_id)
    if not client:
        logger.warning(f"[feishu] add_typing_indicator: no client for {account_id}")
        return None

    try:
        from lark_oapi.api.im.v1.model import CreateMessageReactionRequest

        req = (
            CreateMessageReactionRequest.builder()
            .message_id(message_id)
            .request_body({"reaction_type": {"emoji_type": "Typing"}})
            .build()
        )
        response = client.im.v1.message_reaction.create(req)
        # lark_oapi 响应是对象，不是 dict
        reaction_id = ""
        if hasattr(response, "data"):
            reaction_id = getattr(response.data, "reaction_id", "") or (
                response.data.get("reaction_id", "") if isinstance(response.data, dict) else ""
            )
        if reaction_id:
            store.add(message_id, reaction_id, chat_id)
            logger.info(f"[feishu] add_typing_indicator: added for message_id={message_id}")
        return reaction_id
    except Exception as e:
        logger.warning(f"[feishu] add_typing_indicator failed: {e}")
        return None


def remove_typing_indicator(message_id: str, account_id: str = "default") -> None:
    """移除 Typing Indicator

    调用飞书 API 删除 reaction，并从本地存储中清除记录。

    Args:
        message_id: 消息 ID
        account_id: 账号 ID

    Returns:
        None
    """
    from .typing_indicator import get_typing_store

    store = get_typing_store()
    entry = store.get(message_id)
    if not entry:
        return

    client = get_feishu_client(account_id)
    if not client:
        store.remove(message_id)
        return

    try:
        from lark_oapi.api.im.v1.model import DeleteMessageReactionRequest

        req = DeleteMessageReactionRequest.builder().message_id(message_id).reaction_id(entry["reaction_id"]).build()
        client.im.v1.message_reaction.delete(req)
        logger.info(f"[feishu] remove_typing_indicator: removed for message_id={message_id}")
    except Exception as e:
        logger.warning(f"[feishu] remove_typing_indicator failed: {e}")
    finally:
        store.remove(message_id)
