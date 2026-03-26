"""Authentication middleware for A2A endpoints."""

import contextvars
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from lightspeed_agent.auth.introspection import (
    DisallowedScopeError,
    InsufficientScopeError,
    TokenValidationError,
    get_token_introspector,
)
from lightspeed_agent.config import get_settings

logger = logging.getLogger(__name__)

# Request-scoped access token for forwarding to downstream services (e.g. MCP).
# Stores (token, expiry) or None.  Set by AuthenticationMiddleware, read by
# the MCP header provider in tools/mcp_headers.py.
_request_access_token: contextvars.ContextVar[tuple[str, datetime] | None] = (
    contextvars.ContextVar("_request_access_token", default=None)
)
_request_order_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_request_order_id", default=None
)


def get_request_access_token() -> tuple[str, datetime] | None:
    """Return the current request's access token and its expiry, or None."""
    return _request_access_token.get()


def get_request_order_id() -> str | None:
    """Return the current request's order_id, or None."""
    return _request_order_id.get()


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce Red Hat SSO authentication on A2A endpoints.

    This middleware validates Bearer tokens on protected endpoints using
    the Red Hat SSO JWT validator. The AgentCard endpoint is left public
    for agent discovery.
    """

    # Paths that require authentication (POST only)
    PROTECTED_PATHS = {"/"}

    # Paths that are always public (no auth required)
    PUBLIC_PATHS = {
        "/health",
        "/healthz",
        "/ready",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/.well-known/agent.json",
        "/.well-known/agent-card.json",
        "/marketplace/pubsub",  # Pub/Sub uses Google-signed tokens
    }

    # Path prefixes that are public
    PUBLIC_PREFIXES = (
        "/marketplace/",
    )

    def __init__(self, app: Any):
        super().__init__(app)
        self._settings = get_settings()

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Process request with authentication check."""
        _request_access_token.set(None)
        _request_order_id.set(None)
        path = request.url.path
        method = request.method

        # Skip authentication for public paths
        if self._is_public(path, method):
            return await call_next(request)

        # Skip authentication in development mode, but still extract the
        # Bearer token so it can be forwarded to downstream services (MCP).
        if self._settings.skip_jwt_validation:
            logger.debug("Skipping authentication (development mode)")
            self._extract_token_for_passthrough(request)
            return await call_next(request)

        # Check for Bearer token
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return self._unauthorized_response("Missing Authorization header")

        if not auth_header.startswith("Bearer "):
            return self._unauthorized_response("Invalid Authorization header format")

        token = auth_header[7:]  # Remove "Bearer " prefix

        # Validate token via introspection
        try:
            introspector = get_token_introspector()
            user = await introspector.validate_token(token)

            order_id = await self._resolve_and_validate_order(client_id=user.client_id)
            if not order_id:
                return self._forbidden_response(
                    "No active order found for this client"
                )

            # Store user in request state for access in handlers
            request.state.user = user
            request.state.access_token = token
            request.state.order_id = order_id
            # Make token available to downstream services (MCP header provider)
            _request_access_token.set((token, user.token_exp))
            _request_order_id.set(order_id)
            logger.debug("Authenticated user: %s", user.user_id)
        except DisallowedScopeError as e:
            logger.warning("Disallowed scope: %s", e)
            return self._forbidden_response(str(e))
        except InsufficientScopeError as e:
            logger.warning("Insufficient scope: %s", e)
            return self._forbidden_response(str(e))
        except TokenValidationError as e:
            logger.warning("Token validation failed: %s", e)
            return self._unauthorized_response(str(e))

        return await call_next(request)

    def _is_public(self, path: str, method: str) -> bool:
        """Check if path/method combination is public."""
        # Explicit public paths
        if path in self.PUBLIC_PATHS:
            return True

        # Public prefixes
        if path.startswith(self.PUBLIC_PREFIXES):
            return True

        # GET requests to root are public (for compatibility)
        if path == "/" and method == "GET":
            return True

        # Only POST to protected paths requires auth; everything else is public
        return not (path in self.PROTECTED_PATHS and method == "POST")

    @staticmethod
    def _extract_token_for_passthrough(request: Request) -> None:
        """Extract Bearer token from the request for downstream forwarding.

        Called when JWT validation is skipped.  The token is not validated
        but is still made available via the ContextVar so the MCP header
        provider can forward it.  A generous expiry is assumed since we
        are not introspecting.
        """
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]
            # Use a generous expiry — the MCP server will validate the token.
            far_future = datetime.now(UTC) + timedelta(hours=1)
            _request_access_token.set((token, far_future))
            order_id = request.headers.get("X-Order-Id")
            if order_id:
                _request_order_id.set(order_id)
            logger.debug("Extracted Bearer token for pass-through (validation skipped)")

    async def _resolve_and_validate_order(self, *, client_id: str) -> str | None:
        """Resolve and validate the active order for a given client_id.

        Looks up the DCR client by client_id to obtain the order_id, then
        verifies the corresponding entitlement exists and is active.

        Returns the resolved order_id on success, or None on any failure.
        """
        if not client_id:
            logger.warning("Token missing client_id during order resolution")
            return None

        try:
            # Deferred imports to avoid circular dependencies at module load time.
            from lightspeed_agent.dcr.repository import get_dcr_client_repository
            from lightspeed_agent.marketplace.models import EntitlementState
            from lightspeed_agent.marketplace.repository import get_entitlement_repository

            dcr_repo = get_dcr_client_repository()
            entitlement_repo = get_entitlement_repository()

            registered_client = await dcr_repo.get_by_client_id(client_id)
            if not registered_client:
                logger.warning("No DCR client found for client_id: %s", client_id)
                return None

            order_id = registered_client.order_id

            entitlement = await entitlement_repo.get(order_id)
            if not entitlement:
                logger.warning(
                    "Order not found during auth validation: %s (client_id=%s)",
                    order_id,
                    client_id,
                )
                return None
            if entitlement.state != EntitlementState.ACTIVE:
                logger.warning(
                    "Order is not active during auth validation: %s (state=%s, client_id=%s)",
                    order_id,
                    entitlement.state,
                    client_id,
                )
                return None

            return order_id
        except Exception as e:
            logger.exception(
                "Failed to resolve order_id for client_id=%s: %s",
                client_id,
                e,
            )
            return None

    def _unauthorized_response(self, detail: str) -> JSONResponse:
        """Build 401 Unauthorized response."""
        return JSONResponse(
            status_code=401,
            content={
                "jsonrpc": "2.0",
                "error": {
                    "code": -32001,
                    "message": "Unauthorized",
                    "data": {"detail": detail},
                },
                "id": None,
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    def _forbidden_response(self, detail: str) -> JSONResponse:
        """Build 403 Forbidden response (valid token, insufficient scope)."""
        return JSONResponse(
            status_code=403,
            content={
                "jsonrpc": "2.0",
                "error": {
                    "code": -32003,
                    "message": "Forbidden",
                    "data": {"detail": detail},
                },
                "id": None,
            },
        )
