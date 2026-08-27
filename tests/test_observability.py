"""Tracer 与 LangfuseClient 测试套。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from aion.config.schema import LangfuseConfig
from aion.observability import Tracer


@pytest.fixture(autouse=True)
def reset_tracer():
    """每个测试前重置 Tracer 状态。"""
    Tracer._initialized = False
    Tracer._lf = None
    Tracer._trace_level = "full"
    yield


def _make_enabled_config() -> LangfuseConfig:
    return LangfuseConfig(
        enabled=True,
        secret_key="sk-test",
        public_key="pk-test",
        host="http://localhost:3000",
        trace_level="full",
    )


def _make_disabled_config() -> LangfuseConfig:
    return LangfuseConfig(enabled=False)


@pytest.fixture
def mock_lf_sdk():
    """Mock Langfuse SDK 实例。"""
    with patch("aion.observability.tracer.LangfuseClient.get") as mock_get:
        lf = MagicMock()
        mock_get.return_value = lf
        yield lf


class TestTracerInit:
    def test_init_enabled(self):
        config = _make_enabled_config()
        with patch("aion.observability.langfuse_client.LangfuseClient.init") as mock_init:
            with patch("aion.observability.langfuse_client.LangfuseClient.get") as mock_get:
                mock_get.return_value = MagicMock()
                Tracer.init(config)
                mock_init.assert_called_once_with(config)
                assert Tracer.available is True
                assert Tracer.trace_level == "full"

    def test_init_disabled(self):
        config = _make_disabled_config()
        with patch("aion.observability.langfuse_client.LangfuseClient.init") as mock_init:
            with patch("aion.observability.langfuse_client.LangfuseClient.get") as mock_get:
                mock_get.return_value = None
                Tracer.init(config)
                mock_init.assert_called_once_with(config)
                assert Tracer.available is False

    def test_init_trace_level_stored(self):
        config = _make_enabled_config()
        config.trace_level = "llm_only"
        with patch("aion.observability.langfuse_client.LangfuseClient.init"):
            with patch("aion.observability.langfuse_client.LangfuseClient.get") as mock_get:
                mock_get.return_value = MagicMock()
                Tracer.init(config)
                assert Tracer.trace_level == "llm_only"

    def test_available_false_when_not_initialized(self):
        assert Tracer.available is False


class TestTracerStartObservation:
    def test_start_observation_passes_session_id(self, mock_lf_sdk):
        Tracer._initialized = True
        Tracer._lf = mock_lf_sdk
        obs_mock = MagicMock()
        mock_lf_sdk.start_observation.return_value = obs_mock

        result = Tracer.start_observation(
            trace_id="a" * 32,
            name="dispatch",
            input="hello",
            as_type="agent",
            session_id="sess-123",
        )
        mock_lf_sdk.start_observation.assert_called_once()
        ctx = mock_lf_sdk.start_observation.call_args[1]["trace_context"]
        assert ctx["trace_id"] == "a" * 32
        assert result is not None

    def test_start_observation_no_session_id(self, mock_lf_sdk):
        Tracer._initialized = True
        Tracer._lf = mock_lf_sdk
        mock_lf_sdk.start_observation.return_value = MagicMock()
        Tracer.start_observation(trace_id="a" * 32, name="test", as_type="agent")
        mock_lf_sdk.start_observation.assert_called_once()

    def test_start_observation_not_available(self):
        result = Tracer.start_observation(trace_id="a" * 32, name="t", as_type="agent")
        assert result.span_type == "noop"


class TestSpanObservation:
    def test_span_context_manager_ends_on_exit(self, mock_lf_sdk):
        span_mock = MagicMock()
        Tracer._lf = mock_lf_sdk
        Tracer._initialized = True
        # Simulate start_observation returning a mock span
        mock_lf_sdk.start_observation.return_value = span_mock
        import asyncio

        async def _run():
            async with Tracer.start_observation(
                trace_id="a" * 32,
                name="test_span",
                as_type="span",
            ) as obs:
                obs.set_usage(input_tokens=10, output_tokens=5, total_tokens=15)
            return obs

        asyncio.run(_run())
        span_mock.update.assert_called_once()
        _update_kwargs = span_mock.update.call_args[1]
        assert "usage_details" in _update_kwargs
        assert _update_kwargs["usage_details"]["input"] == 10
        assert _update_kwargs["usage_details"]["output"] == 5
        span_mock.end.assert_called_once()

    def test_span_set_output(self, mock_lf_sdk):
        span_mock = MagicMock()
        Tracer._lf = mock_lf_sdk
        Tracer._initialized = True
        mock_lf_sdk.start_observation.return_value = span_mock
        import asyncio

        async def _run():
            async with Tracer.start_observation(
                trace_id="a" * 32,
                name="test",
                as_type="span",
            ) as obs:
                obs.set_output("hello world")

        asyncio.run(_run())
        span_mock.update.assert_any_call(output="hello world")

    def test_span_returns_noop_when_not_available(self):
        result = Tracer.start_observation(trace_id="a" * 32, name="t", as_type="span")
        assert result is not None
        assert result.span_type == "noop"

    def test_noop_span_supports_async_with(self):
        import asyncio

        async def _run():
            async with Tracer.start_observation(trace_id="a" * 32, name="t") as span:
                span.set_output("test")
                span.set_usage(input_tokens=1, output_tokens=2, total_tokens=3)

        asyncio.run(_run())  # should not raise


class TestTraceLevel:
    def test_llm_only_skips_span(self):
        Tracer._trace_level = "llm_only"
        assert Tracer.should_span("context_prepare") is False
        assert Tracer.should_span("post_process") is False

    def test_full_creates_span(self):
        Tracer._trace_level = "full"
        assert Tracer.should_span("anything") is True


class TestGeneration:
    def test_generation_called(self, mock_lf_sdk):
        Tracer._lf = mock_lf_sdk
        Tracer._initialized = True
        gen_mock = MagicMock()
        mock_lf_sdk.start_observation.return_value = gen_mock
        Tracer.generation(
            trace_id="a" * 32,
            name="daily_summary",
            model="deepseek-chat",
            input="summarize this",
            output="summary text",
            usage={"input": 100, "output": 50},
        )
        mock_lf_sdk.start_observation.assert_called_once()
        args = mock_lf_sdk.start_observation.call_args[1]
        assert args["name"] == "daily_summary"
        assert args["model"] == "deepseek-chat"
        assert args["as_type"] == "generation"
        assert args["usage_details"] == {"input": 100, "output": 50}
        gen_mock.end.assert_called_once()


class TestFlush:
    def test_flush_delegates(self):
        with patch("aion.observability.langfuse_client.LangfuseClient.flush") as mock_flush:
            Tracer.flush()
            mock_flush.assert_called_once()


class TestPropagateAttributes:
    def test_propagate_attributes_available(self, mock_lf_sdk):
        Tracer._initialized = True
        Tracer._lf = mock_lf_sdk
        with patch("langfuse.propagate_attributes") as mock_prop:
            with Tracer.propagate_attributes(session_id="sess-123"):
                mock_prop.assert_called_once_with(session_id="sess-123")

    def test_propagate_attributes_not_available(self):
        Tracer._initialized = False
        Tracer._lf = None
        # 不应抛出异常
        with Tracer.propagate_attributes(session_id="sess-123"):
            pass

    def test_propagate_attributes_multiple_kwargs(self, mock_lf_sdk):
        Tracer._initialized = True
        Tracer._lf = mock_lf_sdk
        with patch("langfuse.propagate_attributes") as mock_prop:
            with Tracer.propagate_attributes(
                user_id="user-1",
                session_id="sess-456",
                tags=["test"],
            ):
                mock_prop.assert_called_once_with(
                    user_id="user-1",
                    session_id="sess-456",
                    tags=["test"],
                )
