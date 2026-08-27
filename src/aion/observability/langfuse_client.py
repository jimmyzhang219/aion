"""Langfuse 客户端单例管理。"""

from __future__ import annotations

from typing import Optional

from ..config.schema import LangfuseConfig


def _init_local_mode(cls: type, Langfuse) -> None:
    """使用 SDK v4 debug 模式初始化，trace 事件写入 stderr。

    host 为空时自动启用，不需要远端 Langfuse 服务。
    """
    LangfuseClient._langfuse = Langfuse(
        secret_key="local",
        public_key="local",
        host="",
        debug=True,
    )


class LangfuseClient:
    """Langfuse 客户端单例。

    应用启动时调用 init()，全局通过 get() 获取实例。
    enabled=False 时 get() 返回 None，零开销。
    """

    _instance: Optional["LangfuseClient"] = None
    _langfuse: Optional[object] = None  # Langfuse SDK instance

    @classmethod
    def init(cls, config: LangfuseConfig) -> None:
        """初始化 Langfuse SDK 客户端。

        Args:
            config: Langfuse 配置（enabled=False 时不初始化）
        """
        if not config.enabled:
            cls._instance = None
            cls._langfuse = None
            return
        from langfuse import Langfuse

        # 模式 2：host 为空时使用 ConsoleSpanExporter 本地输出，不推送远端
        if not config.host:
            _init_local_mode(cls, Langfuse)
        else:
            cls._langfuse = Langfuse(
                secret_key=config.secret_key,
                public_key=config.public_key,
                host=config.host,
                flush_interval=config.flush_interval,
                debug=config.debug,
            )
        cls._instance = cls()

    @classmethod
    def get(cls) -> Optional[object]:
        """获取 Langfuse SDK 实例。

        Returns:
            Langfuse SDK 实例；未启用时返回 None。
        """
        return cls._langfuse

    @classmethod
    def get_instance(cls) -> Optional[LangfuseClient]:
        """获取 LangfuseClient 单例（用于扩展调用）。

        Returns:
            LangfuseClient 实例；未启用时返回 None。
        """
        return cls._instance

    @classmethod
    def flush(cls) -> None:
        """强制刷新所有待发送事件。"""
        lf = cls._langfuse
        if lf:
            try:
                lf.flush()  # type: ignore[attr-defined]
            except Exception:
                import logging

                logging.getLogger(__name__).warning("Langfuse flush failed", exc_info=True)

    @classmethod
    def create_callback_handler(cls, trace_id: str, session_id: str = "") -> Optional[object]:
        """创建 LangChain CallbackHandler，自动关联到指定 trace。

        Args:
            trace_id: Langfuse trace ID（32 位小写十六进制）
            session_id: Session ID，关联到 trace 层面

        Returns:
            CallbackHandler 实例；未初始化时返回 None。
        """
        if cls._langfuse is None:
            return None
        from langfuse.langchain import CallbackHandler

        ctx: dict = {"trace_id": trace_id}
        if session_id:
            ctx["session_id"] = session_id
        return CallbackHandler(trace_context=ctx)  # type: ignore[arg-type]
