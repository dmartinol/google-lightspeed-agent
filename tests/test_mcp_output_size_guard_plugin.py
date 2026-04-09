"""Tests for the MCP output size guard plugin."""

import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from lightspeed_agent.api.a2a.mcp_output_size_guard_plugin import MCPOutputSizeGuardPlugin


def _make_tool(name: str = "test_tool") -> MagicMock:
    tool = MagicMock()
    tool.name = name
    return tool


class TestMCPOutputSizeGuardPlugin:
    """Tests for MCPOutputSizeGuardPlugin after_tool_callback."""

    @pytest.mark.asyncio
    async def test_result_under_limit_returns_none(self):
        """Small results should pass through unmodified."""
        plugin = MCPOutputSizeGuardPlugin()
        small_result = {"data": "short value"}

        with patch(
            "lightspeed_agent.api.a2a.mcp_output_size_guard_plugin.get_settings"
        ) as mock_settings:
            mock_settings.return_value.tool_result_max_chars = 51200
            got = await plugin.after_tool_callback(
                tool=_make_tool(),
                tool_args={},
                tool_context=MagicMock(),
                result=small_result,
            )

        assert got is None

    @pytest.mark.asyncio
    async def test_result_over_limit_returns_error_dict(self):
        """Oversized results should be replaced with an error message."""
        plugin = MCPOutputSizeGuardPlugin()
        large_result = {"data": "x" * 60000}

        with patch(
            "lightspeed_agent.api.a2a.mcp_output_size_guard_plugin.get_settings"
        ) as mock_settings:
            mock_settings.return_value.tool_result_max_chars = 51200
            got = await plugin.after_tool_callback(
                tool=_make_tool("big_tool"),
                tool_args={},
                tool_context=MagicMock(),
                result=large_result,
            )

        assert got is not None
        assert got["error"] == "tool_result_too_large"
        assert "big_tool" in got["message"]
        assert "narrow down" in got["message"]
        assert got["limit_chars"] == 51200
        assert got["original_size_chars"] > 51200

    @pytest.mark.asyncio
    async def test_disabled_when_max_chars_is_zero(self):
        """Setting tool_result_max_chars=0 should disable truncation."""
        plugin = MCPOutputSizeGuardPlugin()
        large_result = {"data": "x" * 100000}

        with patch(
            "lightspeed_agent.api.a2a.mcp_output_size_guard_plugin.get_settings"
        ) as mock_settings:
            mock_settings.return_value.tool_result_max_chars = 0
            got = await plugin.after_tool_callback(
                tool=_make_tool(),
                tool_args={},
                tool_context=MagicMock(),
                result=large_result,
            )

        assert got is None

    @pytest.mark.asyncio
    async def test_warning_logged_on_oversized_result(self, caplog):
        """A warning should be logged when a result is replaced."""
        plugin = MCPOutputSizeGuardPlugin()
        large_result = {"data": "x" * 60000}

        with (
            patch(
                "lightspeed_agent.api.a2a.mcp_output_size_guard_plugin.get_settings"
            ) as mock_settings,
            caplog.at_level(logging.WARNING),
        ):
            mock_settings.return_value.tool_result_max_chars = 51200
            await plugin.after_tool_callback(
                tool=_make_tool("inventory_tool"),
                tool_args={},
                tool_context=MagicMock(),
                result=large_result,
            )

        assert any("inventory_tool" in record.message for record in caplog.records)
        assert any("too large" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_result_at_exact_limit_passes_through(self):
        """A result exactly at the limit should not be replaced."""
        plugin = MCPOutputSizeGuardPlugin()
        result = {"a": "b"}
        serialized = json.dumps(result, default=str)

        with patch(
            "lightspeed_agent.api.a2a.mcp_output_size_guard_plugin.get_settings"
        ) as mock_settings:
            mock_settings.return_value.tool_result_max_chars = len(serialized)
            got = await plugin.after_tool_callback(
                tool=_make_tool(),
                tool_args={},
                tool_context=MagicMock(),
                result=result,
            )

        assert got is None

    @pytest.mark.asyncio
    async def test_non_serializable_result_uses_str_fallback(self):
        """Non-JSON-serializable results should fall back to str() for sizing."""
        plugin = MCPOutputSizeGuardPlugin()
        # Create a result with a non-serializable value that str() can handle
        obj = MagicMock()
        obj.__str__ = lambda self: "x" * 60000
        result = {"data": obj}

        with patch(
            "lightspeed_agent.api.a2a.mcp_output_size_guard_plugin.get_settings"
        ) as mock_settings:
            mock_settings.return_value.tool_result_max_chars = 51200
            # json.dumps will use default=str, so this should work
            got = await plugin.after_tool_callback(
                tool=_make_tool(),
                tool_args={},
                tool_context=MagicMock(),
                result=result,
            )

        assert got is not None
        assert got["error"] == "tool_result_too_large"
