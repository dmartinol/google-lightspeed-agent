"""Agent execution logging plugin.

Logs agent lifecycle events (tool calls, LLM invocations, run start/end)
at INFO level for operational visibility. Controlled by the
AGENT_LOGGING_DETAIL setting:
  - "basic": logs tool names, token counts, and lifecycle events
  - "detailed": also logs tool arguments and truncated results
"""

import logging
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from lightspeed_agent.config import get_settings

logger = logging.getLogger(__name__)

_MAX_RESULT_LENGTH = 500


def _truncate(value: Any, max_length: int = _MAX_RESULT_LENGTH) -> str:
    """Return a string representation truncated to *max_length* characters."""
    text = str(value)
    if len(text) <= max_length:
        return text
    return text[:max_length] + "...(truncated)"


class AgentLoggingPlugin(BasePlugin):
    """ADK plugin that logs agent execution events at INFO level."""

    def __init__(self) -> None:
        super().__init__(name="agent_logging")

    def _is_detailed(self) -> bool:
        return get_settings().agent_logging_detail == "detailed"

    # -- run lifecycle --------------------------------------------------------

    async def before_run_callback(
        self, *, invocation_context: InvocationContext
    ) -> None:
        logger.info(
            "Agent run started (invocation_id=%s, agent=%s)",
            invocation_context.invocation_id,
            invocation_context.agent.name,
        )
        return None

    async def after_run_callback(
        self, *, invocation_context: InvocationContext
    ) -> None:
        logger.info(
            "Agent run completed (invocation_id=%s)",
            invocation_context.invocation_id,
        )
        return None

    # -- model callbacks ------------------------------------------------------

    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> Any | None:
        logger.info("LLM call started (agent=%s)", callback_context.agent_name)
        return None

    async def after_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_response: LlmResponse,
    ) -> LlmResponse | None:
        input_tokens = 0
        output_tokens = 0
        if llm_response and llm_response.usage_metadata:
            usage = llm_response.usage_metadata
            input_tokens = getattr(usage, "prompt_token_count", 0) or 0
            output_tokens = getattr(usage, "candidates_token_count", 0) or 0

        model_version = llm_response.model_version if llm_response else None

        logger.info(
            "LLM call completed (input_tokens=%d, output_tokens=%d, model=%s)",
            input_tokens,
            output_tokens,
            model_version,
        )
        return None

    async def on_model_error_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest, error: Exception
    ) -> LlmResponse | None:
        logger.error("LLM call failed: %s", error)
        return None

    # -- tool callbacks -------------------------------------------------------

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> Any | None:
        tool_name = getattr(tool, "name", type(tool).__name__)
        if self._is_detailed():
            logger.info(
                "Tool call started (tool=%s, args=%s)",
                tool_name,
                _truncate(tool_args),
            )
        else:
            logger.info("Tool call started (tool=%s)", tool_name)
        return None

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        tool_name = getattr(tool, "name", type(tool).__name__)
        if self._is_detailed():
            logger.info(
                "Tool call completed (tool=%s, result=%s)",
                tool_name,
                _truncate(result),
            )
        else:
            logger.info("Tool call completed (tool=%s)", tool_name)
        return None

    async def on_tool_error_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        error: Exception,
    ) -> dict[str, Any] | None:
        tool_name = getattr(tool, "name", type(tool).__name__)
        logger.error("Tool call failed (tool=%s): %s", tool_name, error)
        return None
