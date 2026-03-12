"""Pytest configuration and fixtures."""

import os

import pytest
import pytest_asyncio

# Set test environment variables before importing application modules
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "FALSE"
os.environ["GOOGLE_API_KEY"] = "test-api-key"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["DEBUG"] = "true"
os.environ["SKIP_JWT_VALIDATION"] = "true"
os.environ["DCR_ENABLED"] = "false"  # Use pre-seeded credentials for tests
os.environ["RED_HAT_SSO_CLIENT_ID"] = "test-static-client-id"
os.environ["RED_HAT_SSO_CLIENT_SECRET"] = "test-static-client-secret"


@pytest.fixture
def test_settings():
    """Provide test settings."""
    from lightspeed_agent.config import Settings

    return Settings(
        google_api_key="test-api-key",
        database_url="sqlite+aiosqlite:///:memory:",
        debug=True,
        skip_jwt_validation=True,
        dcr_enabled=False,
        red_hat_sso_client_id="test-static-client-id",
        red_hat_sso_client_secret="test-static-client-secret",
    )


@pytest_asyncio.fixture
async def db_session():
    """Initialize database for tests.

    Creates all tables and yields, then cleans up after.
    """
    from lightspeed_agent.db import close_database, init_database

    await init_database()
    yield
    await close_database()
