"""Usage tracking plugin with per-order metrics."""

import logging
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from lightspeed_agent.auth.middleware import get_request_order_id
from lightspeed_agent.metering import get_usage_repository

logger = logging.getLogger(__name__)


def _resolve_order_id() -> str | None:
    """Resolve the current request order_id from request context."""
    return get_request_order_id()


class UsageTrackingPlugin(BasePlugin):
    """ADK Plugin for tracking per-order usage."""

    def __init__(self) -> None:
        super().__init__(name="usage_tracking")
        self._usage_repo = get_usage_repository()

    async def before_run_callback(self, *, invocation_context: InvocationContext) -> None:
        """Track request count at start of each run."""
        order_id = _resolve_order_id()
        if not order_id:
            logger.error("Missing order_id in request context; skipping request metering")
            return None
        await self._persist_increment(order_id=order_id, request_count=1)
        logger.debug("Request metering increment persisted for order %s", order_id)
        return None

    async def after_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_response: LlmResponse,
    ) -> LlmResponse | None:
        """Track token usage from LLM responses."""
        if llm_response.usage_metadata:
            order_id = _resolve_order_id()
            if not order_id:
                logger.error("Missing order_id in request context; skipping token metering")
                return None
            usage = llm_response.usage_metadata
            input_tokens = getattr(usage, "prompt_token_count", 0) or 0
            output_tokens = getattr(usage, "candidates_token_count", 0) or 0

            await self._persist_increment(
                order_id=order_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

            logger.debug(
                "Token metering increment persisted for order %s (in=%d out=%d)",
                order_id,
                input_tokens,
                output_tokens,
            )

        return None  # Don't modify the response

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Track tool/MCP calls."""
        order_id = _resolve_order_id()
        if not order_id:
            logger.error("Missing order_id in request context; skipping tool metering")
            return None
        await self._persist_increment(order_id=order_id, tool_calls=1)
        tool_name = getattr(tool, "name", type(tool).__name__)
        logger.debug(
            "Tool metering increment persisted for order %s (tool=%s)",
            order_id,
            tool_name,
        )
        return None  # Don't modify the result

    async def _persist_increment(
        self,
        *,
        order_id: str,
        request_count: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        tool_calls: int = 0,
    ) -> None:
        """Best-effort persistence for metering increments."""
        try:
            await self._usage_repo.increment_usage(
                order_id=order_id,
                request_count=request_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                tool_calls=tool_calls,
            )
        except Exception as e:
            logger.error("Failed to persist usage increment for order %s: %s", order_id, e)
