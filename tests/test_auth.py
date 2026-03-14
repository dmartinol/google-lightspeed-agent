"""Tests for authentication and authorization module."""

import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from lightspeed_agent.auth import AuthenticatedUser
from lightspeed_agent.auth.introspection import (
    InsufficientScopeError,
    TokenIntrospector,
    TokenValidationError,
)
from lightspeed_agent.config import Settings


@pytest.fixture
def mock_settings():
    """Create mock settings for testing."""
    return Settings(
        red_hat_sso_issuer="https://sso.redhat.com/auth/realms/redhat-external",
        red_hat_sso_client_id="test-client-id",
        red_hat_sso_client_secret="test-client-secret",
        agent_required_scope="api.console,api.ocm",
        skip_jwt_validation=False,
        debug=True,
    )


@pytest.fixture
def dev_settings():
    """Create settings with skip_jwt_validation=True."""
    return Settings(
        red_hat_sso_issuer="https://sso.redhat.com/auth/realms/redhat-external",
        red_hat_sso_client_id="test-client-id",
        red_hat_sso_client_secret="test-client-secret",
        agent_required_scope="api.console,api.ocm",
        skip_jwt_validation=True,
        debug=True,
    )


@pytest.fixture
def introspector(mock_settings):
    """Create token introspector for testing."""
    return TokenIntrospector(settings=mock_settings)


@pytest.fixture
def dev_introspector(dev_settings):
    """Create token introspector in dev mode."""
    return TokenIntrospector(settings=dev_settings)


class TestTokenIntrospector:
    """Tests for TokenIntrospector (token introspection)."""

    @pytest.mark.asyncio
    async def test_active_token_with_scope(self, introspector):
        """Test validating an active token with the required scope."""
        introspection_response = {
            "active": True,
            "sub": "user-123",
            "client_id": "gemini-order-abc",
            "scope": "openid api.console api.ocm",
            "preferred_username": "testuser",
            "email": "test@example.com",
            "name": "Test User",
            "exp": int(time.time()) + 3600,
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = introspection_response

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            user = await introspector.validate_token("some-token")

            assert isinstance(user, AuthenticatedUser)
            assert user.user_id == "user-123"
            assert user.client_id == "gemini-order-abc"
            assert user.email == "test@example.com"
            assert "api.console" in user.scopes
            assert "api.ocm" in user.scopes
            assert "openid" in user.scopes

    @pytest.mark.asyncio
    async def test_inactive_token(self, introspector):
        """Test that an inactive token raises TokenValidationError."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"active": False}

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            with pytest.raises(TokenValidationError, match="not active"):
                await introspector.validate_token("expired-token")

    @pytest.mark.asyncio
    async def test_missing_scope(self, introspector):
        """Test that a token without the required scope raises InsufficientScopeError."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "active": True,
            "sub": "user-123",
            "scope": "openid profile",
            "exp": int(time.time()) + 3600,
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            with pytest.raises(InsufficientScopeError, match="api.console"):
                await introspector.validate_token("no-scope-token")

    @pytest.mark.asyncio
    async def test_partial_scope_match(self, introspector):
        """Test that a token with only one of two required scopes raises InsufficientScopeError."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "active": True,
            "sub": "user-123",
            "scope": "openid api.console",
            "exp": int(time.time()) + 3600,
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            with pytest.raises(InsufficientScopeError, match="api.ocm"):
                await introspector.validate_token("partial-scope-token")

    @pytest.mark.asyncio
    async def test_introspection_http_error(self, introspector):
        """Test that HTTP errors from introspection raise TokenValidationError."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            with pytest.raises(TokenValidationError, match="HTTP 500"):
                await introspector.validate_token("some-token")

    @pytest.mark.asyncio
    async def test_introspection_network_error(self, introspector):
        """Test that network errors raise TokenValidationError."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post.side_effect = httpx.ConnectError("Connection refused")
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            with pytest.raises(TokenValidationError, match="HTTP error"):
                await introspector.validate_token("some-token")

    @pytest.mark.asyncio
    async def test_dev_mode_returns_dev_user(self, dev_introspector):
        """Test that dev mode returns a default user with required scopes."""
        user = await dev_introspector.validate_token("any-token")

        assert isinstance(user, AuthenticatedUser)
        assert user.user_id == "dev-user"
        assert user.client_id == "dev-client"
        assert "api.console" in user.scopes
        assert "api.ocm" in user.scopes

    @pytest.mark.asyncio
    async def test_azp_preferred_over_client_id(self, introspector):
        """Test that azp is used over client_id when both are present."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "active": True,
            "sub": "user-123",
            "azp": "azp-client",
            "client_id": "other-client",
            "scope": "openid api.console api.ocm",
            "exp": int(time.time()) + 3600,
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            user = await introspector.validate_token("some-token")
            assert user.client_id == "azp-client"


class TestAuthenticatedUser:
    """Tests for AuthenticatedUser model."""

    def test_authenticated_user_creation(self):
        """Test AuthenticatedUser model creation."""
        user = AuthenticatedUser(
            user_id="user-123",
            client_id="client-456",
            username="testuser",
            email="test@example.com",
            name="Test User",
            org_id="org-789",
            scopes=["openid", "profile", "email"],
            token_exp=datetime.now(UTC),
        )

        assert user.user_id == "user-123"
        assert user.client_id == "client-456"
        assert "openid" in user.scopes
