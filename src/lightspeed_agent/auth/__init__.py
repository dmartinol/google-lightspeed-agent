"""Authentication and authorization module.

This module validates Bearer tokens via Keycloak token introspection (RFC 7662)
and checks for the required scopes (``api.console``, ``api.ocm``).  The agent acts as a
Resource Server — it does not proxy OAuth flows.
"""

from lightspeed_agent.auth.dependencies import (
    CurrentUser,
    get_current_user,
    require_scope,
)
from lightspeed_agent.auth.introspection import (
    InsufficientScopeError,
    TokenIntrospector,
    TokenValidationError,
    get_token_introspector,
)
from lightspeed_agent.auth.middleware import AuthenticationMiddleware
from lightspeed_agent.auth.models import (
    AuthenticatedUser,
    JWTClaims,
)

__all__ = [
    # Dependencies
    "CurrentUser",
    "get_current_user",
    "require_scope",
    # Introspection
    "TokenIntrospector",
    "TokenValidationError",
    "InsufficientScopeError",
    "get_token_introspector",
    # Middleware
    "AuthenticationMiddleware",
    # Models
    "AuthenticatedUser",
    "JWTClaims",
]
