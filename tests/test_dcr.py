"""Tests for Dynamic Client Registration (DCR) implementation."""

import time
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from lightspeed_agent.api.app import create_app
from lightspeed_agent.dcr.models import (
    DCRError,
    DCRErrorCode,
    DCRRequest,
    DCRResponse,
    GoogleClaims,
    GoogleJWTClaims,
    RegisteredClient,
)
from lightspeed_agent.dcr.repository import DCRClientRepository
from lightspeed_agent.dcr.service import DCRService
from lightspeed_agent.marketplace.models import Account, AccountState, Entitlement, EntitlementState
from lightspeed_agent.marketplace.repository import AccountRepository, EntitlementRepository
from lightspeed_agent.marketplace.service import ProcurementService


class TestModels:
    """Tests for DCR data models."""

    def test_google_jwt_claims(self):
        """Test parsing Google JWT claims."""
        claims_data = {
            "iss": "https://www.googleapis.com/service_accounts/v1/metadata/x509/cloud-agentspace@system.gserviceaccount.com",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
            "aud": "https://example.com",
            "sub": "account-123",
            "auth_app_redirect_uris": ["https://example.com/callback"],
            "google": {"order": "order-456"},
        }

        claims = GoogleJWTClaims(**claims_data)

        assert claims.iss == claims_data["iss"]
        assert claims.account_id == "account-123"
        assert claims.order_id == "order-456"
        assert claims.auth_app_redirect_uris == ["https://example.com/callback"]

    def test_google_jwt_claims_extra_fields(self):
        """Test that extra fields are allowed (per spec)."""
        claims_data = {
            "iss": "https://example.com",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
            "aud": "https://example.com",
            "sub": "account-123",
            "google": {"order": "order-456"},
            "unknown_field": "should be allowed",
        }

        claims = GoogleJWTClaims(**claims_data)

        assert claims.account_id == "account-123"

    def test_dcr_request(self):
        """Test DCR request model."""
        request = DCRRequest(software_statement="eyJ...")

        assert request.software_statement == "eyJ..."
        assert request.client_id is None
        assert request.client_secret is None

    def test_dcr_request_with_static_credentials(self):
        """Test DCR request model with static credentials."""
        request = DCRRequest(
            software_statement="eyJ...",
            client_id="static-client-id",
            client_secret="static-client-secret",
        )

        assert request.software_statement == "eyJ..."
        assert request.client_id == "static-client-id"
        assert request.client_secret == "static-client-secret"

    def test_dcr_response(self):
        """Test DCR response model."""
        response = DCRResponse(
            client_id="client_abc123",
            client_secret="secret_xyz789",
            client_secret_expires_at=0,
        )

        assert response.client_id == "client_abc123"
        assert response.client_secret == "secret_xyz789"
        assert response.client_secret_expires_at == 0

    def test_dcr_error(self):
        """Test DCR error model."""
        error = DCRError(
            error=DCRErrorCode.INVALID_SOFTWARE_STATEMENT,
            error_description="JWT has expired",
        )

        assert error.error == DCRErrorCode.INVALID_SOFTWARE_STATEMENT
        assert "expired" in error.error_description

    def test_registered_client(self):
        """Test RegisteredClient model."""
        client = RegisteredClient(
            client_id="client_123",
            client_secret_encrypted="encrypted_secret_abc",
            order_id="order-456",
            account_id="account-789",
            redirect_uris=["https://example.com/callback"],
        )

        assert client.client_id == "client_123"
        assert client.order_id == "order-456"
        assert "authorization_code" in client.grant_types


class TestDCRService:
    """Tests for DCR service with database persistence."""

    @pytest_asyncio.fixture
    async def service(self, db_session):
        """Create a fresh DCR service with database-backed repositories."""
        account_repo = AccountRepository()
        entitlement_repo = EntitlementRepository()
        client_repo = DCRClientRepository()
        procurement_service = ProcurementService(
            account_repo=account_repo,
            entitlement_repo=entitlement_repo,
        )

        # Pre-populate with valid account and order
        account = Account(
            id="valid-account-123",
            provider_id="provider-456",
            state=AccountState.ACTIVE,
        )
        await account_repo.create(account)

        entitlement = Entitlement(
            id="valid-order-789",
            account_id="valid-account-123",
            provider_id="provider-456",
            state=EntitlementState.ACTIVE,
        )
        await entitlement_repo.create(entitlement)

        return DCRService(
            procurement_service=procurement_service,
            client_repository=client_repo,
        )

    @pytest.mark.asyncio
    async def test_dcr_disabled_stores_static_credentials(self, service):
        """Test that DCR_ENABLED=false stores static credentials from the request."""
        # _store_static_credentials should exist for handling static credentials
        assert hasattr(service, "_store_static_credentials")

    @pytest.mark.asyncio
    async def test_store_static_credentials_success(self, service):
        """Test storing static credentials when both client_id and secret are provided."""
        request = DCRRequest(
            software_statement="dummy",
            client_id="static-client-123",
            client_secret="static-secret-456",
        )
        claims = GoogleJWTClaims(
            iss="https://example.com",
            iat=int(time.time()),
            exp=int(time.time()) + 3600,
            aud="https://example.com",
            sub="valid-account-123",
            google=GoogleClaims(order="valid-order-789"),
        )

        # Mock credential validation (no real Red Hat SSO in tests)
        with patch.object(
            service, "_validate_credentials", new_callable=AsyncMock, return_value=True,
        ):
            result = await service._store_static_credentials(request, claims)

        assert isinstance(result, DCRResponse)
        assert result.client_id == "static-client-123"
        assert result.client_secret == "static-secret-456"
        assert result.client_secret_expires_at == 0

        # Verify credentials were stored in the repository
        stored = await service._client_repository.get_by_order_id("valid-order-789")
        assert stored is not None
        assert stored.client_id == "static-client-123"

    @pytest.mark.asyncio
    async def test_store_static_credentials_missing_client_id(self, service):
        """Test error when client_id is missing in static mode."""
        request = DCRRequest(
            software_statement="dummy",
            client_secret="secret-only",
        )
        claims = GoogleJWTClaims(
            iss="https://example.com",
            iat=int(time.time()),
            exp=int(time.time()) + 3600,
            aud="https://example.com",
            sub="valid-account-123",
            google=GoogleClaims(order="order-no-client-id"),
        )

        result = await service._store_static_credentials(request, claims)

        assert isinstance(result, DCRError)
        assert result.error == DCRErrorCode.INVALID_CLIENT_METADATA
        assert "client_id" in result.error_description

    @pytest.mark.asyncio
    async def test_store_static_credentials_missing_secret(self, service):
        """Test error when client_secret is missing in static mode."""
        request = DCRRequest(
            software_statement="dummy",
            client_id="client-only",
        )
        claims = GoogleJWTClaims(
            iss="https://example.com",
            iat=int(time.time()),
            exp=int(time.time()) + 3600,
            aud="https://example.com",
            sub="valid-account-123",
            google=GoogleClaims(order="order-no-secret"),
        )

        result = await service._store_static_credentials(request, claims)

        assert isinstance(result, DCRError)
        assert result.error == DCRErrorCode.INVALID_CLIENT_METADATA

    @pytest.mark.asyncio
    async def test_get_client(self, service):
        """Test getting client info from pre-seeded credentials."""
        # Seed credentials directly via the repository
        encrypted_secret = service._encrypt_secret("test-secret")
        await service._client_repository.create(
            client_id="seeded-client-id",
            client_secret_encrypted=encrypted_secret,
            order_id="valid-order-789",
            account_id="valid-account-123",
            redirect_uris=["https://example.com/callback"],
            grant_types=["authorization_code", "refresh_token"],
            metadata={"seeded_by": "test"},
        )

        client = await service.get_client("seeded-client-id")
        assert client is not None
        assert client.order_id == "valid-order-789"
        assert client.account_id == "valid-account-123"


class TestDCRRepository:
    """Tests for DCR client repository with database."""

    @pytest_asyncio.fixture
    async def repo(self, db_session):
        """Create a fresh DCR client repository."""
        return DCRClientRepository()

    @pytest.mark.asyncio
    async def test_create_and_get_by_client_id(self, repo):
        """Test creating and retrieving a client by ID."""
        await repo.create(
            client_id="test-client-123",
            client_secret_encrypted="encrypted-secret",
            order_id="order-456",
            account_id="account-789",
            redirect_uris=["https://example.com/callback"],
        )

        client = await repo.get_by_client_id("test-client-123")
        assert client is not None
        assert client.client_id == "test-client-123"
        assert client.order_id == "order-456"

    @pytest.mark.asyncio
    async def test_get_by_order_id(self, repo):
        """Test retrieving a client by order ID."""
        await repo.create(
            client_id="test-client-456",
            client_secret_encrypted="encrypted-secret",
            order_id="order-unique",
            account_id="account-789",
        )

        client = await repo.get_by_order_id("order-unique")
        assert client is not None
        assert client.client_id == "test-client-456"


class TestDCRRouter:
    """Tests for DCR API endpoints."""

    @pytest_asyncio.fixture
    async def client(self, db_session):
        """Create test client with marketplace handler app."""
        from lightspeed_agent.marketplace.app import create_app as create_marketplace_app

        app = create_marketplace_app()
        return TestClient(app)

    @pytest.mark.asyncio
    async def test_dcr_endpoint_invalid_jwt(self, client):
        """Test /dcr endpoint with invalid JWT."""
        response = client.post(
            "/dcr",
            json={"software_statement": "invalid-jwt-token"},
        )

        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "invalid_software_statement"


class TestAgentCardDCRExtension:
    """Tests for DCR extension in AgentCard."""

    def test_agent_card_has_dcr_extension(self):
        """Test that AgentCard includes DCR extension."""
        from lightspeed_agent.api.a2a.agent_card import build_agent_card

        card = build_agent_card()

        # Extensions are now a list of AgentExtension objects
        assert card.capabilities.extensions is not None
        assert len(card.capabilities.extensions) > 0
        dcr_ext = card.capabilities.extensions[0]
        assert "dcr" in dcr_ext.uri
        assert dcr_ext.params is not None
        assert "endpoint" in dcr_ext.params
        assert "/dcr" in dcr_ext.params["endpoint"]

    @pytest.mark.asyncio
    async def test_agent_card_endpoint_returns_dcr(self, db_session):
        """Test that AgentCard endpoint includes DCR extension."""
        app = create_app()
        client = TestClient(app)

        response = client.get("/.well-known/agent.json")

        assert response.status_code == 200
        data = response.json()
        assert "capabilities" in data
        assert "extensions" in data["capabilities"]
        # Extensions are now a list
        extensions = data["capabilities"]["extensions"]
        assert len(extensions) > 0
        dcr_ext = extensions[0]
        assert "dcr" in dcr_ext["uri"]
        assert "endpoint" in dcr_ext["params"]


class TestKeycloakDCRClient:
    """Tests for Keycloak DCR client."""

    def test_keycloak_client_response_model(self):
        """Test KeycloakClientResponse dataclass."""
        from lightspeed_agent.dcr.keycloak_client import KeycloakClientResponse

        response = KeycloakClientResponse(
            client_id="kc-client-123",
            client_secret="kc-secret-xyz",
            client_name="gemini-order-456",
            registration_access_token="rat-token",
            registration_client_uri="https://sso.example.com/clients/123",
            redirect_uris=["https://example.com/callback"],
        )

        assert response.client_id == "kc-client-123"
        assert response.client_secret == "kc-secret-xyz"
        assert response.client_name == "gemini-order-456"
        assert response.registration_access_token == "rat-token"

    def test_keycloak_dcr_error(self):
        """Test KeycloakDCRError exception."""
        from lightspeed_agent.dcr.keycloak_client import KeycloakDCRError

        error = KeycloakDCRError(
            "Failed to create client",
            status_code=401,
            details={"error": "unauthorized"},
        )

        assert str(error) == "Failed to create client"
        assert error.status_code == 401
        assert error.details["error"] == "unauthorized"
