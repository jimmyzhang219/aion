"""chromadb 无操作 telemetry client — 避免 Python 3.14 下 posthog 模块导入异常。

chromadb 1.5.9 在 Python 3.14 上偶尔触发
  No module named 'chromadb.telemetry.product.posthog'
该模块提供一个不依赖 posthog/overrides 的轻量替代实现，
通过 ChromaSettings(chroma_product_telemetry_impl=...) 注入。
"""

from overrides import override

from chromadb.telemetry.product import (
    ProductTelemetryClient,
    ProductTelemetryEvent,
)


class NoopTelemetryClient(ProductTelemetryClient):
    """不执行任何操作的 telemetry client，替代 chromadb.telemetry.product.posthog.Posthog。"""

    @staticmethod
    def fqn() -> str:
        """返回全限定类名，供 ChromaSettings 注入。"""
        return "aion.memory._chroma_telemetry.NoopTelemetryClient"

    @override
    def capture(self, event: ProductTelemetryEvent) -> None:
        pass
