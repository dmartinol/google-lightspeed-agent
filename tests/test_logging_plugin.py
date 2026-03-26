"""Tests for agent execution logging plugin."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from lightspeed_agent.api.a2a.logging_plugin import AgentLoggingPlugin, _truncate


class TestTruncate:
    """Tests for the _truncate helper."""

    def test_short_string_unchanged(self):
        assert _truncate("hello") == "hello"

    def test_long_string_truncated(self):
        result = _truncate("x" * 600, max_length=500)
        assert len(result) == 500 + len("...(truncated)")
        assert result.endswith("...(truncated)")

    def test_exact_length_unchanged(self):
        text = "a" * 500
        assert _truncate(text, max_length=500) == text

    def test_non_string_converted(self):
        assert _truncate({"key": "value"}) == "{'key': 'value'}"


class TestAgentLoggingPluginBasic:
    """Tests for basic logging mode (default)."""

    @pytest.fixture
    def plugin(self):
        with patch(
            "lightspeed_agent.api.a2a.logging_plugin.get_settings"
        ) as mock_settings:
            settings = MagicMock()
            settings.agent_logging_detail = "basic"
            mock_settings.return_value = settings
            yield AgentLoggingPlugin()

    @pytest.mark.asyncio
    async def test_before_run_logs_info(self, plugin, caplog):
        ctx = MagicMock()
        ctx.invocation_id = "inv-123"
        ctx.agent_name = "test_agent"

        with caplog.at_level(logging.INFO):
            result = await plugin.before_run_callback(invocation_context=ctx)

        assert result is None
        assert "Agent run started" in caplog.text
        assert "inv-123" in caplog.text
        assert "test_agent" in caplog.text

    @pytest.mark.asyncio
    async def test_after_run_logs_info(self, plugin, caplog):
        ctx = MagicMock()
        ctx.invocation_id = "inv-456"

        with caplog.at_level(logging.INFO):
            result = await plugin.after_run_callback(invocation_context=ctx)

        assert result is None
        assert "Agent run completed" in caplog.text
        assert "inv-456" in caplog.text

    @pytest.mark.asyncio
    async def test_before_model_logs_info(self, plugin, caplog):
        ctx = MagicMock()
        ctx.agent_name = "test_agent"

        with caplog.at_level(logging.INFO):
            result = await plugin.before_model_callback(
                callback_context=ctx, llm_request=MagicMock()
            )

        assert result is None
        assert "LLM call started" in caplog.text
        assert "test_agent" in caplog.text

    @pytest.mark.asyncio
    async def test_after_model_logs_token_counts(self, plugin, caplog):
        ctx = MagicMock()
        llm_response = MagicMock()
        llm_response.usage_metadata = MagicMock(
            prompt_token_count=100, candidates_token_count=50
        )
        llm_response.model_version = "gemini-2.5-flash-001"
        llm_response.content = None

        with caplog.at_level(logging.INFO):
            result = await plugin.after_model_callback(
                callback_context=ctx, llm_response=llm_response
            )

        assert result is None
        assert "LLM call completed" in caplog.text
        assert "input_tokens=100" in caplog.text
        assert "output_tokens=50" in caplog.text
        assert "gemini-2.5-flash-001" in caplog.text

    @pytest.mark.asyncio
    async def test_after_model_handles_missing_usage(self, plugin, caplog):
        ctx = MagicMock()
        llm_response = MagicMock()
        llm_response.usage_metadata = None
        llm_response.model_version = None
        llm_response.content = None

        with caplog.at_level(logging.INFO):
            result = await plugin.after_model_callback(
                callback_context=ctx, llm_response=llm_response
            )

        assert result is None
        assert "input_tokens=0" in caplog.text
        assert "output_tokens=0" in caplog.text

    @pytest.mark.asyncio
    async def test_on_model_error_logs_error(self, plugin, caplog):
        ctx = MagicMock()
        error = RuntimeError("API quota exceeded")

        with caplog.at_level(logging.ERROR):
            result = await plugin.on_model_error_callback(
                callback_context=ctx, llm_request=MagicMock(), error=error
            )

        assert result is None
        assert "LLM call failed" in caplog.text
        assert "API quota exceeded" in caplog.text

    @pytest.mark.asyncio
    async def test_before_tool_basic_omits_args(self, plugin, caplog):
        tool = MagicMock()
        tool.name = "get_advisories"

        with caplog.at_level(logging.INFO):
            result = await plugin.before_tool_callback(
                tool=tool,
                tool_args={"system_id": "abc-123"},
                tool_context=MagicMock(),
            )

        assert result is None
        assert "Tool call started" in caplog.text
        assert "get_advisories" in caplog.text
        assert "abc-123" not in caplog.text

    @pytest.mark.asyncio
    async def test_after_tool_basic_omits_result(self, plugin, caplog):
        tool = MagicMock()
        tool.name = "get_advisories"

        with caplog.at_level(logging.INFO):
            result = await plugin.after_tool_callback(
                tool=tool,
                tool_args={},
                tool_context=MagicMock(),
                result={"data": "sensitive-info"},
            )

        assert result is None
        assert "Tool call completed" in caplog.text
        assert "get_advisories" in caplog.text
        assert "sensitive-info" not in caplog.text

    @pytest.mark.asyncio
    async def test_on_tool_error_logs_error(self, plugin, caplog):
        tool = MagicMock()
        tool.name = "get_advisories"
        error = ConnectionError("MCP server unreachable")

        with caplog.at_level(logging.ERROR):
            result = await plugin.on_tool_error_callback(
                tool=tool,
                tool_args={},
                tool_context=MagicMock(),
                error=error,
            )

        assert result is None
        assert "Tool call failed" in caplog.text
        assert "get_advisories" in caplog.text
        assert "MCP server unreachable" in caplog.text


class TestAgentLoggingPluginDetailed:
    """Tests for detailed logging mode."""

    @pytest.fixture
    def plugin(self):
        with patch(
            "lightspeed_agent.api.a2a.logging_plugin.get_settings"
        ) as mock_settings:
            settings = MagicMock()
            settings.agent_logging_detail = "detailed"
            mock_settings.return_value = settings
            yield AgentLoggingPlugin()

    @pytest.mark.asyncio
    async def test_before_tool_detailed_includes_args(self, plugin, caplog):
        tool = MagicMock()
        tool.name = "get_advisories"

        with caplog.at_level(logging.INFO):
            result = await plugin.before_tool_callback(
                tool=tool,
                tool_args={"system_id": "abc-123"},
                tool_context=MagicMock(),
            )

        assert result is None
        assert "Tool call started" in caplog.text
        assert "get_advisories" in caplog.text
        assert "abc-123" in caplog.text

    @pytest.mark.asyncio
    async def test_after_tool_detailed_includes_result(self, plugin, caplog):
        tool = MagicMock()
        tool.name = "get_advisories"

        with caplog.at_level(logging.INFO):
            result = await plugin.after_tool_callback(
                tool=tool,
                tool_args={},
                tool_context=MagicMock(),
                result={"advisories": ["CVE-2024-1234"]},
            )

        assert result is None
        assert "Tool call completed" in caplog.text
        assert "get_advisories" in caplog.text
        assert "CVE-2024-1234" in caplog.text

    @pytest.mark.asyncio
    async def test_after_tool_detailed_truncates_long_result(self, plugin, caplog):
        tool = MagicMock()
        tool.name = "get_systems"

        long_result = {"data": "x" * 1000}
        with caplog.at_level(logging.INFO):
            result = await plugin.after_tool_callback(
                tool=tool,
                tool_args={},
                tool_context=MagicMock(),
                result=long_result,
            )

        assert result is None
        assert "...(truncated)" in caplog.text


class TestAllCallbacksReturnNone:
    """Verify all callbacks return None (no data modification)."""

    @pytest.fixture
    def plugin(self):
        with patch(
            "lightspeed_agent.api.a2a.logging_plugin.get_settings"
        ) as mock_settings:
            settings = MagicMock()
            settings.agent_logging_detail = "basic"
            mock_settings.return_value = settings
            yield AgentLoggingPlugin()

    @pytest.mark.asyncio
    async def test_all_callbacks_return_none(self, plugin):
        ctx = MagicMock()
        ctx.invocation_id = "inv-1"
        ctx.agent_name = "agent"

        tool = MagicMock()
        tool.name = "test_tool"

        llm_response = MagicMock()
        llm_response.usage_metadata = None
        llm_response.model_version = None
        llm_response.content = None

        assert await plugin.before_run_callback(invocation_context=ctx) is None
        assert await plugin.after_run_callback(invocation_context=ctx) is None
        assert (
            await plugin.before_model_callback(
                callback_context=ctx, llm_request=MagicMock()
            )
            is None
        )
        assert (
            await plugin.after_model_callback(
                callback_context=ctx, llm_response=llm_response
            )
            is None
        )
        assert (
            await plugin.on_model_error_callback(
                callback_context=ctx, llm_request=MagicMock(), error=RuntimeError("test")
            )
            is None
        )
        assert (
            await plugin.before_tool_callback(
                tool=tool, tool_args={}, tool_context=MagicMock()
            )
            is None
        )
        assert (
            await plugin.after_tool_callback(
                tool=tool, tool_args={}, tool_context=MagicMock(), result={}
            )
            is None
        )
        assert (
            await plugin.on_tool_error_callback(
                tool=tool,
                tool_args={},
                tool_context=MagicMock(),
                error=RuntimeError("test"),
            )
            is None
        )
