"""Usage tracking plugin with per-order metrics."""

import logging
from dataclasses import dataclass
from typing import Any, Optional

from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool

from lightspeed_agent.auth.middleware import get_request_order_id

logger = logging.getLogger(__name__)


@dataclass
class OrderUsage:
    """Usage statistics for a specific order."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_requests: int = 0
    total_tool_calls: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "total_requests": self.total_requests,
            "total_tool_calls": self.total_tool_calls,
        }


# Per-order usage tracker (source of truth)
_usage_by_order: dict[str, OrderUsage] = {}


def get_usage_by_order() -> dict[str, dict]:
    """Get usage statistics grouped by order_id."""
    return {order_id: usage.to_dict() for order_id, usage in _usage_by_order.items()}


def get_order_usage(order_id: str) -> OrderUsage:
    """Get usage statistics for a specific order_id."""
    if not order_id:
        return OrderUsage()
    return _usage_by_order.get(order_id, OrderUsage())


def _resolve_order_id() -> str | None:
    """Resolve the current request order_id from request context."""
    return get_request_order_id()


class UsageTrackingPlugin(BasePlugin):
    """ADK Plugin for tracking per-order usage.

    This plugin tracks:
    - Per-order input/output tokens across all LLM calls
    - Per-order number of requests
    - Per-order number of tool/MCP calls

    """

    def __init__(self):
        super().__init__(name="usage_tracking")

    async def before_run_callback(self, *, invocation_context) -> None:
        """Track request count at start of each run."""
        order_id = _resolve_order_id()
        if not order_id:
            logger.error("Missing order_id in request context; skipping request metering")
            return None
        order_usage = _usage_by_order.setdefault(order_id, OrderUsage())
        order_usage.total_requests += 1
        logger.debug(
            "Request #%d started for order %s",
            order_usage.total_requests,
            order_id,
        )
        return None

    async def after_model_callback(
        self,
        *,
        callback_context,
        llm_response: LlmResponse,
    ) -> Optional[LlmResponse]:
        """Track token usage from LLM responses."""
        if llm_response.usage_metadata:
            order_id = _resolve_order_id()
            if not order_id:
                logger.error("Missing order_id in request context; skipping token metering")
                return None
            usage = llm_response.usage_metadata
            input_tokens = getattr(usage, "prompt_token_count", 0) or 0
            output_tokens = getattr(usage, "candidates_token_count", 0) or 0

            order_usage = _usage_by_order.setdefault(order_id, OrderUsage())
            order_usage.total_input_tokens += input_tokens
            order_usage.total_output_tokens += output_tokens

            logger.debug(
                f"Order {order_id}: "
                f"Tokens: in={input_tokens}, out={output_tokens}, "
                f"totals: in={order_usage.total_input_tokens}, out={order_usage.total_output_tokens}"
            )

        return None  # Don't modify the response

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context,
        result: dict,
    ) -> Optional[dict]:
        """Track tool/MCP calls."""
        order_id = _resolve_order_id()
        if not order_id:
            logger.error("Missing order_id in request context; skipping tool metering")
            return None
        order_usage = _usage_by_order.setdefault(order_id, OrderUsage())
        order_usage.total_tool_calls += 1
        tool_name = getattr(tool, "name", type(tool).__name__)
        logger.debug(
            f"Order {order_id}: "
            f"Tool call: {tool_name}, total calls: {order_usage.total_tool_calls}"
        )
        return None  # Don't modify the result
