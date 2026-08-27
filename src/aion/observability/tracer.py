"""统一观测入口 — Tracer + SpanObservation。

Tracer 是唯一的观测入口，替代 ``__import__("aion.observability")`` 分散调用模式。
所有方法为类方法，无实例化。

适配 LangFuse SDK v4.x（无 ``trace()`` / ``generation()`` 方法，
改用 ``start_observation(as_type=...)`` 统一 API）。
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Optional

from ..config.schema import LangfuseConfig
from .langfuse_client import LangfuseClient


@dataclass
class SpanObservation:
    """LangFuse 观测（span/generation/agent）包装。

    作为 ``async with`` 上下文管理器使用，在 ``__aexit__`` 时自动推送
    ``usage_details`` 并调用 ``end()``，确保 span 一定被正确关闭。
    """

    _lf: Any  # Langfuse SDK instance
    _span: Any  # Langfuse observation 对象（LangfuseSpan / LangfuseAgent / LangfuseGeneration）
    trace_id: str
    span_type: str  # "agent" | "span" | "generation"
    _usage: Optional[dict] = None

    def set_output(self, output: str) -> None:
        """更新 span 的输出内容。"""
        if self._span is not None and output:
            self._span.update(output=output[:1000])

    def set_usage(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
    ) -> None:
        """记录 Token 用量，在 ``__aexit__`` 时推送到 LangFuse（v4 ``usage_details`` 格式）。

        SDK v4 使用 ``usage_details: Dict[str, int]``，不包含 ``unit``。
        """
        _ = total_tokens  # 保留入参，v4 以 input+output 为准
        self._usage = {
            "input": input_tokens,
            "output": output_tokens,
        }

    async def __aenter__(self) -> SpanObservation:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._span is None:
            return
        try:
            kwargs: dict = {}
            if self._usage is not None:
                kwargs["usage_details"] = self._usage
            if kwargs:
                self._span.update(**kwargs)
        except Exception:
            pass
        try:
            self._span.end()
        except Exception:
            pass


class _NoopSpan:
    """No-op 上下文管理器 — Tracer 不可用时替代 ``None`` 避免崩溃。

    所有方法静默无操作，支持 ``async with`` 协议和 ``set_output``/``set_usage`` 调用。
    """

    trace_id: str = ""
    span_type: str = "noop"

    async def __aenter__(self) -> _NoopSpan:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass

    def set_output(self, output: str) -> None:
        pass

    def set_usage(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
    ) -> None:
        pass


class _TracerMeta(type):
    """元类 — 实现类级别的 ``available`` 和 ``trace_level`` 属性。"""

    def __new__(mcs, name, bases, namespace):
        cls = super().__new__(mcs, name, bases, namespace)
        cls._initialized = False
        cls._lf = None
        cls._trace_level = "default"
        return cls

    @property
    def available(cls) -> bool:
        """Tracer 是否已初始化并启用。"""
        return cls._initialized and cls._lf is not None  # type: ignore[attr-defined]

    @property
    def trace_level(cls) -> str:
        return cls._trace_level  # type: ignore[attr-defined]


class Tracer(metaclass=_TracerMeta):
    """统一观测入口 — 所有方法均为类方法。

    使用方式::

        if Tracer.available:
            async with Tracer.start_observation(
                trace_id="...", name="my_span", as_type="span",
            ) as span:
                span.set_usage(input_tokens=10, output_tokens=20)
    """

    _initialized: bool = False
    _trace_level: str = "full"
    _lf: Any = None  # Langfuse SDK instance

    @classmethod
    def init(cls, config: LangfuseConfig) -> None:
        """初始化 LangFuse 并记录配置。"""
        LangfuseClient.init(config)
        cls._lf = LangfuseClient.get()
        cls._trace_level = config.trace_level or "full"
        cls._initialized = True

    @classmethod
    def should_span(cls, span_name: str) -> bool:
        """根据 trace_level 判断是否应创建非 generation 类型的 span。

        ``llm_only`` 模式跳过所有 span/agent 类型，只保留 trace 和 generation。
        """
        _ = span_name  # 保留入参以备未来扩展
        if cls._trace_level == "llm_only":
            return False
        return True

    @classmethod
    def start_observation(
        cls,
        trace_id: str,
        name: str,
        input: str = "",
        as_type: str = "span",
        parent_span_id: Optional[str] = None,
        session_id: str = "",
        metadata: Optional[dict] = None,
    ) -> SpanObservation | _NoopSpan:
        """启动一个观测（span / agent / generation）。

        上下文管理器用法::

            async with Tracer.start_observation(trace_id="...", name="...") as span:
                span.set_output("...")

        Args:
            trace_id: 所属 trace ID
            name: 观测名称
            input: 输入摘要
            as_type: 观测类型 ("span" | "agent" | "generation")
            parent_span_id: 父 span ID（维护层次结构）
            session_id: Session ID（通过 OTel context 关联到 trace）
            metadata: 附加元数据

        Returns:
            SpanObservation 或 _NoopSpan（Tracer 不可用或 llm_only 模式时）。
            **永远不返回 None**，可直接用于 ``async with``。
        """
        if not cls.available:
            return _NoopSpan()
        if as_type in ("span", "agent", "tool") and not cls.should_span(name):
            return _NoopSpan()
        _trace_id = trace_id
        ctx: dict = {"trace_id": _trace_id}
        if parent_span_id:
            ctx["parent_span_id"] = parent_span_id
        _obs = cls._lf.start_observation(
            trace_context=ctx,
            name=name,
            input=input[:1000] if input else "",
            as_type=as_type,
            metadata=metadata or None,
        )
        # session_id 由调用方通过 propagate_attributes 传播
        _ = session_id  # 保留入参，API 兼容
        return SpanObservation(cls._lf, _obs, _trace_id, as_type)

    @classmethod
    def generation(
        cls,
        trace_id: str,
        name: str,
        model: str,
        input: str = "",
        output: str = "",
        usage: Optional[dict] = None,
        parent_span_id: Optional[str] = None,
    ) -> Any:
        """记录一次非 LangGraph 的 LLM Generation。

        SDK v4 无 ``generation()`` 方法，改用 ``start_observation(as_type="generation")``。

        用于 Daily Summary、Bootstrap Audit 等直接 ``ainvoke`` 调用点。

        Args:
            trace_id: 所属 trace ID（应为 32 位小写 hex）
            name: Generation 名称
            model: 模型名（如 "deepseek-chat"）
            input: 输入 prompt
            output: 模型输出
            usage: Token 用量 {"input": N, "output": N}
            parent_span_id: 父 span ID

        Returns:
            LangFuse generation 对象；不可用时返回 None。
        """
        if not cls.available:
            return None
        _trace_id = trace_id
        ctx: dict = {"trace_id": _trace_id}
        if parent_span_id:
            ctx["parent_span_id"] = parent_span_id
        try:
            _gen = cls._lf.start_observation(
                trace_context=ctx,
                name=name,
                as_type="generation",
                model=model or "unknown",
                input=input[:1000] if input else "",
                output=output[:1000] if output else "",
                usage_details=usage,
            )
            _gen.end()
            return _gen
        except Exception:
            return None

    @classmethod
    def create_callback(
        cls,
        trace_id: str,
        parent_span_id: Optional[str] = None,
        session_id: str = "",
    ) -> Any:
        """创建 LangChain CallbackHandler。

        Args:
            trace_id: 所属 trace ID（应为 32 位小写 hex）
            parent_span_id: 父 span ID
            session_id: Session ID（写入 trace_context，关联 LangFuse trace）

        Returns:
            langfuse.langchain.CallbackHandler 实例；不可用时返回 None。
        """
        if not cls.available:
            return None
        from langfuse.langchain import CallbackHandler

        _trace_id = trace_id
        ctx: dict = {"trace_id": _trace_id}
        if parent_span_id:
            ctx["parent_span_id"] = parent_span_id
        if session_id:
            ctx["session_id"] = session_id
        return CallbackHandler(trace_context=ctx)  # type: ignore[arg-type]

    @classmethod
    @contextmanager
    def propagate_attributes(cls, **kwargs: Any) -> Iterator[None]:
        """包装 SDK v4 propagate_attributes，不可用时无操作。

        propagate_attributes 是 langfuse 顶层函数，不是 Langfuse 类的方法。

        用法::

            with Tracer.propagate_attributes(session_id="sess-123"):
                async with Tracer.start_observation(...) as span:
                    ...
        """
        if cls._lf is None:
            yield
        else:
            from langfuse import propagate_attributes as _lf_propagate

            with _lf_propagate(**kwargs):  # type: ignore[arg-type]
                yield

    @classmethod
    def flush(cls) -> None:
        """强制刷新所有待发送事件。"""
        LangfuseClient.flush()
