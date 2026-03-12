"""Token validation via Keycloak token introspection (RFC 7662).

Instead of verifying JWT signatures locally with JWKS, this module POSTs
the Bearer token to the Keycloak introspection endpoint and checks the
``active`` flag and required scope.  The agent authenticates to the
introspection endpoint using its own client credentials (Resource Server
pattern), so tokens issued to *any* client in the realm can be validated.

Reference: https://github.com/ljogeiger/GE-A2A-Marketplace-Agent/tree/main/2_oauth
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from lightspeed_agent.auth.models import AuthenticatedUser
from lightspeed_agent.config import Settings, get_settings

logger = logging.getLogger(__name__)


class TokenValidationError(Exception):
    """Raised when a token is invalid or inactive (HTTP 401)."""


class InsufficientScopeError(Exception):
    """Raised when a token is valid but lacks the required scope (HTTP 403)."""


class TokenIntrospector:
    """Validate Bearer tokens via the Keycloak introspection endpoint.

    The agent authenticates to the introspection endpoint with its own
    ``RED_HAT_SSO_CLIENT_ID`` / ``RED_HAT_SSO_CLIENT_SECRET`` (HTTP Basic
    Auth).  Keycloak returns ``{"active": true/false, …}``; we then check
    that the required scope is present.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._introspection_url = self._settings.keycloak_introspection_endpoint
        self._client_id = self._settings.red_hat_sso_client_id
        self._client_secret = self._settings.red_hat_sso_client_secret
        self._required_scope = self._settings.agent_required_scope

    async def validate_token(self, token: str) -> AuthenticatedUser:
        """Validate a Bearer token via introspection.

        Args:
            token: Raw Bearer token string.

        Returns:
            AuthenticatedUser with claims from the introspection response.

        Raises:
            TokenValidationError: Token is inactive or introspection failed.
            InsufficientScopeError: Token is active but missing the required scope.
        """
        if self._settings.skip_jwt_validation:
            logger.warning("Token validation skipped — development mode")
            return self._create_dev_user()

        data = await self._introspect(token)

        if not data.get("active"):
            raise TokenValidationError("Token is not active")

        # Check required scope
        scopes = self._parse_scopes(data)
        if self._required_scope and self._required_scope not in scopes:
            raise InsufficientScopeError(
                f"Token is missing required scope: {self._required_scope}"
            )

        return self._to_user(data, scopes)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _introspect(self, token: str) -> dict[str, Any]:
        """POST to the introspection endpoint."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self._introspection_url,
                    data={"token": token, "token_type_hint": "access_token"},
                    auth=(self._client_id, self._client_secret),
                    timeout=30.0,
                )

            if response.status_code != 200:
                logger.error(
                    "Introspection endpoint returned %d: %s",
                    response.status_code,
                    response.text,
                )
                raise TokenValidationError(
                    f"Introspection request failed (HTTP {response.status_code})"
                )

            result: dict[str, Any] = response.json()
            return result

        except httpx.RequestError as exc:
            logger.exception("HTTP error calling introspection endpoint: %s", exc)
            raise TokenValidationError(
                f"HTTP error calling introspection endpoint: {exc}"
            ) from exc

    @staticmethod
    def _parse_scopes(data: dict[str, Any]) -> list[str]:
        scope_str = data.get("scope", "")
        return scope_str.split() if scope_str else []

    def _to_user(self, data: dict[str, Any], scopes: list[str]) -> AuthenticatedUser:
        """Map an introspection response to an AuthenticatedUser."""
        # client_id: azp (authorized party) or client_id field
        client_id = data.get("azp") or data.get("client_id", "")

        # Token expiry
        exp = data.get("exp")
        token_exp = (
            datetime.fromtimestamp(exp, tz=UTC)
            if exp
            else datetime.now(UTC).replace(year=2099)
        )

        metadata: dict[str, str] = {}
        if data.get("order_id"):
            metadata["order_id"] = data["order_id"]
        elif data.get("org_id"):
            metadata["order_id"] = data["org_id"]

        return AuthenticatedUser(
            user_id=data.get("sub", ""),
            client_id=client_id,
            username=data.get("preferred_username"),
            email=data.get("email"),
            name=data.get("name"),
            org_id=data.get("org_id"),
            scopes=scopes,
            token_exp=token_exp,
            metadata=metadata,
        )

    def _create_dev_user(self) -> AuthenticatedUser:
        """Return a default user when validation is skipped."""
        return AuthenticatedUser(
            user_id="dev-user",
            client_id="dev-client",
            username="developer",
            email="dev@example.com",
            name="Development User",
            org_id="dev-org",
            scopes=["openid", "profile", "email", "agent:insights"],
            token_exp=datetime.now(UTC).replace(year=2099),
            metadata={"order_id": "dev-order"},
        )


# ------------------------------------------------------------------
# Global singleton
# ------------------------------------------------------------------

_introspector: TokenIntrospector | None = None


def get_token_introspector() -> TokenIntrospector:
    """Get the global TokenIntrospector instance."""
    global _introspector
    if _introspector is None:
        _introspector = TokenIntrospector()
    return _introspector
