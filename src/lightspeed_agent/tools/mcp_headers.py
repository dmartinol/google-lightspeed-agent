"""Header provider for MCP toolset to inject authentication credentials."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from lightspeed_agent.auth.middleware import get_request_access_token

if TYPE_CHECKING:
    from google.adk.agents.readonly_context import ReadonlyContext

logger = logging.getLogger(__name__)


def create_mcp_header_provider() -> Callable[["ReadonlyContext"], dict[str, str]]:
    """Create a header provider function for McpToolset.

    The returned function forwards the incoming request's JWT token as an
    ``Authorization: Bearer <token>`` header so the MCP server can
    authenticate on behalf of the calling user.

    Returns:
        A callable that takes ReadonlyContext and returns headers dict.
    """

    def header_provider(context: "ReadonlyContext") -> dict[str, str]:
        """Provide headers for MCP requests.

        Forwards the caller's JWT token to the MCP server.

        Args:
            context: The readonly context (unused, but required by interface).

        Returns:
            Dictionary of headers to include in MCP requests.
        """
        token_info = get_request_access_token()
        if token_info is not None:
            token, token_exp = token_info
            now = datetime.now(UTC)
            if now >= token_exp:
                logger.warning(
                    "Access token expired at %s (now %s); "
                    "forwarding anyway — MCP server will reject it",
                    token_exp.isoformat(),
                    now.isoformat(),
                )
            return {"Authorization": f"Bearer {token}"}

        logger.warning(
            "No MCP credentials available: no access token in request context"
        )
        return {}

    return header_provider
