"""MCP output size guard plugin.

Replaces oversized MCP tool results with an actionable error message
so the LLM can inform the user to narrow down their query or use
pagination, rather than silently sending huge payloads that exhaust
Vertex AI token-per-minute quotas.

Controlled by the ``TOOL_RESULT_MAX_CHARS`` setting (default 51200).
Set to 0 to disable the guard entirely.
"""

import json
import logging
from typing import Any

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from lightspeed_agent.config import get_settings

logger = logging.getLogger(__name__)


class MCPOutputSizeGuardPlugin(BasePlugin):
    """ADK plugin that replaces oversized tool results with guidance."""

    def __init__(self) -> None:
        super().__init__(name="mcp_output_size_guard")

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Replace tool results that exceed the configured character limit."""
        max_chars = get_settings().tool_result_max_chars
        if max_chars == 0:
            return None

        try:
            serialized = json.dumps(result, default=str)
        except (TypeError, ValueError):
            serialized = str(result)

        size = len(serialized)
        if size <= max_chars:
            return None

        tool_name = getattr(tool, "name", type(tool).__name__)
        logger.warning(
            "Tool result too large, replacing with guidance "
            "(tool=%s, size=%d, limit=%d)",
            tool_name,
            size,
            max_chars,
        )

        return {
            "error": "tool_result_too_large",
            "message": (
                f"The tool '{tool_name}' returned a result that is too large "
                f"to process ({size:,} characters, limit is "
                f"{max_chars:,}). Please ask the user to narrow down their "
                f"query or use pagination/filtering parameters to reduce the "
                f"result size."
            ),
            "original_size_chars": size,
            "limit_chars": max_chars,
        }
