"""Tests for Dynamic Client Registration (DCR) implementation."""

import base64
import json
import time
from unittest.mock import AsyncMock, patch

import httpx
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


class TestGoogleJWTValidator:
    """Tests for GoogleJWTValidator."""

    def test_audience_uses_organization_url_from_settings(self):
        """Test that the validator uses agent_provider_organization_url as audience."""
        from lightspeed_agent.config import get_settings
        from lightspeed_agent.dcr.google_jwt import GoogleJWTValidator

        settings = get_settings()
        original = settings.agent_provider_organization_url
        settings.agent_provider_organization_url = "https://custom-org.example.com"
        try:
            validator = GoogleJWTValidator()
            assert validator._expected_audience == "https://custom-org.example.com"
        finally:
            settings.agent_provider_organization_url = original


class TestDCRService:
    """Tests for DCR service with database persistence."""

    @pytest_asyncio.fixture
    async def service(self, db_session):
        """Create a fresh DCR service with database-backed repositories."""
        account_repo = AccountRepository()
        entitlement_repo = EntitlementRepository()
        client_repo = DCRClientRepository()
        procurement_service = ProcurementService(
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


class TestDCRServiceEncryptionValidation:
    """Tests for DCR service encryption key validation."""

    def test_dcr_service_missing_key_on_cloud_run(self, monkeypatch, db_session):
        """Test DCRService raises ValueError when DCR_ENCRYPTION_KEY is missing on Cloud Run."""
        from lightspeed_agent.config import get_settings

        settings = get_settings()
        monkeypatch.setenv("K_SERVICE", "test-marketplace-handler")
        monkeypatch.setattr(settings, "dcr_encryption_key", "")

        with pytest.raises(ValueError, match="DCR_ENCRYPTION_KEY is required in production"):
            DCRService()

    def test_dcr_service_invalid_encryption_key(self, monkeypatch, db_session):
        """Test that DCRService raises ValueError for invalid DCR_ENCRYPTION_KEY."""
        from lightspeed_agent.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "dcr_encryption_key", "not-a-valid-fernet-key")

        with pytest.raises(ValueError, match="Invalid DCR_ENCRYPTION_KEY"):
            DCRService()

    def test_encrypt_secret_without_key_raises(self, db_session):
        """Test that _encrypt_secret raises RuntimeError when _fernet is None."""
        service = DCRService()
        service._fernet = None

        with pytest.raises(RuntimeError, match="Cannot encrypt client secret"):
            service._encrypt_secret("test-secret")


class TestDCRServiceDelete:
    """Tests for DCR service client deletion."""

    @pytest_asyncio.fixture
    async def service(self, db_session):
        """Create a DCR service with a real repository."""
        from lightspeed_agent.dcr.service import DCRService

        return DCRService()

    @pytest.mark.asyncio
    async def test_delete_client_gma_mode(self, service):
        """Test deleting a GMA-created client calls delete_tenant."""
        encrypted_secret = service._encrypt_secret("test-secret")
        await service._client_repository.create(
            client_id="gma-client-123",
            client_secret_encrypted=encrypted_secret,
            order_id="order-gma-del",
            account_id="account-1",
            metadata={"registration_mode": "gma"},
        )

        mock_gma = AsyncMock()
        service._gma_client = mock_gma

        await service.delete_client("order-gma-del")

        mock_gma.delete_tenant.assert_awaited_once_with("gma-client-123")
        assert await service._client_repository.get_by_order_id("order-gma-del") is None

    @pytest.mark.asyncio
    async def test_delete_client_static_mode(self, service):
        """Test deleting a static-credentials client skips GMA API."""
        encrypted_secret = service._encrypt_secret("test-secret")
        await service._client_repository.create(
            client_id="static-client-123",
            client_secret_encrypted=encrypted_secret,
            order_id="order-static-del",
            account_id="account-1",
            metadata={"registration_mode": "static"},
        )

        mock_gma = AsyncMock()
        service._gma_client = mock_gma

        await service.delete_client("order-static-del")

        mock_gma.delete_tenant.assert_not_awaited()
        assert await service._client_repository.get_by_order_id("order-static-del") is None

    @pytest.mark.asyncio
    async def test_delete_client_not_found(self, service):
        """Test deleting a non-existent client does nothing."""
        await service.delete_client("order-nonexistent")
        # No error raised


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

    @pytest.mark.asyncio
    async def test_delete_by_order_id_success(self, repo):
        """Test deleting a client by order ID."""
        await repo.create(
            client_id="test-client-del",
            client_secret_encrypted="encrypted-secret",
            order_id="order-to-delete",
            account_id="account-789",
        )

        result = await repo.delete_by_order_id("order-to-delete")
        assert result is True
        assert await repo.get_by_order_id("order-to-delete") is None

    @pytest.mark.asyncio
    async def test_delete_by_order_id_not_found(self, repo):
        """Test deleting a non-existent order returns False."""
        result = await repo.delete_by_order_id("order-nonexistent")
        assert result is False


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


class TestPubSubHandler:
    """Tests for Pub/Sub event handling via the /dcr endpoint."""

    @pytest_asyncio.fixture
    async def client(self, db_session):
        """Create test client with marketplace handler app."""
        from lightspeed_agent.marketplace.app import create_app as create_marketplace_app

        app = create_marketplace_app()
        return TestClient(app)

    def _make_pubsub_body(self, event_data: dict, message_id: str = "msg-001") -> dict:
        """Build a Pub/Sub push message body."""
        encoded = base64.b64encode(json.dumps(event_data).encode()).decode()
        return {
            "message": {
                "messageId": message_id,
                "data": encoded,
            }
        }

    @pytest.mark.asyncio
    async def test_entitlement_active_returns_success_with_order_id(self, client):
        """Test that ENTITLEMENT_ACTIVE returns status=success and orderId."""
        event_data = {
            "eventType": "ENTITLEMENT_ACTIVE",
            "eventId": "evt-001",
            "providerId": "test-provider",
            "entitlement": {
                "id": "order-abc-123",
                "product": "products/test-product",
            },
        }

        response = client.post("/dcr", json=self._make_pubsub_body(event_data))

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["orderId"] == "order-abc-123"

    @pytest.mark.asyncio
    async def test_account_creation_event_processed(self, client):
        """Test that account creation events are processed (not skipped)."""
        event_data = {
            "eventType": "ACCOUNT_CREATION_REQUESTED",
            "eventId": "evt-002",
            "providerId": "test-provider",
            "account": {"id": "account-xyz"},
        }

        mock_response = httpx.Response(
            status_code=200, request=httpx.Request("POST", "https://fake")
        )
        with patch(
            "httpx.AsyncClient.post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_post:
            response = client.post("/dcr", json=self._make_pubsub_body(event_data))

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["orderId"] is None
        # Safety net: when google_cloud_project is unset the approval is skipped,
        # so no Procurement API call should be made.
        mock_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_entitlement_creation_requested_returns_order_id(self, client):
        """Test that ENTITLEMENT_CREATION_REQUESTED returns the order ID."""
        event_data = {
            "eventType": "ENTITLEMENT_CREATION_REQUESTED",
            "eventId": "evt-003",
            "providerId": "test-provider",
            "entitlement": {
                "id": "order-def-456",
                "product": "products/test-product",
            },
        }

        mock_post = httpx.Response(status_code=200, request=httpx.Request("POST", "https://fake"))
        # Mock _resolve_account_id response: "providers/{provider}/accounts/{account_id}"
        mock_get = httpx.Response(
            status_code=200,
            json={"account": "providers/test-provider/accounts/acct-1"},
            request=httpx.Request("GET", "https://fake"),
        )
        with (
            patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_post),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_get),
        ):
            response = client.post("/dcr", json=self._make_pubsub_body(event_data))

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["orderId"] == "order-def-456"

    @pytest.mark.asyncio
    async def test_empty_message_data(self, client):
        """Test that empty Pub/Sub message data returns ok."""
        body = {
            "message": {
                "messageId": "msg-empty",
                "data": "",
            }
        }

        response = client.post("/dcr", json=body)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_unknown_event_type(self, client):
        """Test that unknown event types with product pass filtering but fail parsing."""
        event_data = {
            "eventType": "SOME_UNKNOWN_EVENT",
            "eventId": "evt-unknown",
            "providerId": "test-provider",
            "entitlement": {"id": "order-1", "product": "products/test-product"},
        }

        response = client.post("/dcr", json=self._make_pubsub_body(event_data))

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_unknown_event_type_no_product(self, client):
        """Test that unknown event types without product return unknown event."""
        event_data = {
            "eventType": "SOME_UNKNOWN_EVENT",
            "eventId": "evt-unknown",
            "providerId": "test-provider",
        }

        response = client.post("/dcr", json=self._make_pubsub_body(event_data))

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "Unknown event" in data["message"]

    @pytest.mark.asyncio
    async def test_invalid_message_encoding(self, client):
        """Test that invalid base64 data returns 400."""
        body = {
            "message": {
                "messageId": "msg-bad",
                "data": "not-valid-base64!!!",
            }
        }

        response = client.post("/dcr", json=body)

        assert response.status_code == 400
        data = response.json()
        assert "error" in data

    @pytest.mark.asyncio
    async def test_unknown_request_format(self, client):
        """Test that requests without software_statement or message return 400."""
        response = client.post("/dcr", json={"foo": "bar"})

        assert response.status_code == 400

    # Product filtering tests

    @pytest.mark.asyncio
    async def test_matching_product_processed(self, client):
        """Test that entitlement with matching product is processed."""
        from lightspeed_agent.config import get_settings

        settings = get_settings()
        original = settings.service_control_service_name
        settings.service_control_service_name = "my-agent.endpoints.project.cloud.goog"
        try:
            event_data = {
                "eventType": "ENTITLEMENT_ACTIVE",
                "eventId": "evt-match",
                "providerId": "test-provider",
                "entitlement": {
                    "id": "order-match",
                    "product": "products/my-agent.endpoints.project.cloud.goog",
                },
            }

            response = client.post("/dcr", json=self._make_pubsub_body(event_data))

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert data["orderId"] == "order-match"
        finally:
            settings.service_control_service_name = original

    @pytest.mark.asyncio
    async def test_non_matching_product_skipped(self, client):
        """Test that entitlement with non-matching product is skipped."""
        from lightspeed_agent.config import get_settings

        settings = get_settings()
        original = settings.service_control_service_name
        settings.service_control_service_name = "my-agent.endpoints.project.cloud.goog"
        try:
            event_data = {
                "eventType": "ENTITLEMENT_ACTIVE",
                "eventId": "evt-other",
                "providerId": "test-provider",
                "entitlement": {
                    "id": "order-other",
                    "product": "products/other-agent.endpoints.project.cloud.goog",
                },
            }

            response = client.post("/dcr", json=self._make_pubsub_body(event_data))

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert "not for this product" in data["message"]
        finally:
            settings.service_control_service_name = original

    @pytest.mark.asyncio
    async def test_product_prefix_stripped(self, client):
        """Test that products/ prefix is stripped before comparison."""
        from lightspeed_agent.config import get_settings

        settings = get_settings()
        original = settings.service_control_service_name
        settings.service_control_service_name = "my-agent"
        try:
            event_data = {
                "eventType": "ENTITLEMENT_ACTIVE",
                "eventId": "evt-prefix",
                "providerId": "test-provider",
                "entitlement": {
                    "id": "order-prefix",
                    "product": "products/my-agent",
                },
            }

            response = client.post("/dcr", json=self._make_pubsub_body(event_data))

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
        finally:
            settings.service_control_service_name = original

    @pytest.mark.asyncio
    async def test_no_service_name_skips_filtering(self, client):
        """Test that events pass without filtering when SERVICE_CONTROL_SERVICE_NAME is empty."""
        event_data = {
            "eventType": "ENTITLEMENT_ACTIVE",
            "eventId": "evt-nofilter",
            "providerId": "test-provider",
            "entitlement": {
                "id": "order-nofilter",
                "product": "products/any-agent",
            },
        }

        response = client.post("/dcr", json=self._make_pubsub_body(event_data))

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"


class TestAgentCardDCRExtension:
    """Tests for DCR extension in AgentCard."""

    def test_agent_card_has_dcr_extension(self):
        """Test that AgentCard includes DCR extension."""
        from lightspeed_agent.api.a2a.agent_card import build_agent_card

        card = build_agent_card()

        assert card.capabilities.extensions is not None
        dcr_exts = [ext for ext in card.capabilities.extensions if "dcr" in ext.uri]
        assert len(dcr_exts) == 1
        dcr_ext = dcr_exts[0]
        assert dcr_ext.params is not None
        assert "target_url" in dcr_ext.params
        assert "/dcr" in dcr_ext.params["target_url"]

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
        assert "target_url" in dcr_ext["params"]


class TestGMAClient:
    """Tests for GMA SSO API client."""

    def test_gma_client_response_model(self):
        """Test GMAClientResponse dataclass."""
        from lightspeed_agent.dcr.gma_client import GMAClientResponse

        response = GMAClientResponse(
            client_id="gma-client-123",
            client_secret="gma-secret-xyz",
            name="gemini-order-456",
            created_at=1774421600,
        )

        assert response.client_id == "gma-client-123"
        assert response.client_secret == "gma-secret-xyz"
        assert response.name == "gemini-order-456"
        assert response.created_at == 1774421600

    def test_gma_client_error(self):
        """Test GMAClientError exception."""
        from lightspeed_agent.dcr.gma_client import GMAClientError

        error = GMAClientError(
            "Failed to create tenant",
            status_code=401,
            details={"error": "unauthorized"},
        )

        assert str(error) == "Failed to create tenant"
        assert error.status_code == 401
        assert error.details["error"] == "unauthorized"

    @pytest.mark.asyncio
    async def test_gma_client_get_token_success(self):
        """Test successful token acquisition."""
        from lightspeed_agent.dcr.gma_client import GMAClient

        mock_response = httpx.Response(
            status_code=200,
            json={"access_token": "test-token-abc", "expires_in": 300},
            request=httpx.Request("POST", "https://sso.example.com/token"),
        )
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(return_value=mock_response)

        client = GMAClient(
            api_base_url="https://sso.example.com/apis/beta/acs/v1/",
            client_id="test-gma-id",
            client_secret="test-gma-secret",
            token_endpoint="https://sso.example.com/token",
            http_client=mock_http,
        )

        token = await client.get_token()
        assert token == "test-token-abc"

    @pytest.mark.asyncio
    async def test_gma_client_get_token_cached(self):
        """Test that token is cached on second call."""
        from lightspeed_agent.dcr.gma_client import GMAClient

        mock_response = httpx.Response(
            status_code=200,
            json={"access_token": "cached-token", "expires_in": 300},
            request=httpx.Request("POST", "https://sso.example.com/token"),
        )
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(return_value=mock_response)

        client = GMAClient(
            api_base_url="https://sso.example.com/apis/beta/acs/v1/",
            client_id="test-gma-id",
            client_secret="test-gma-secret",
            token_endpoint="https://sso.example.com/token",
            http_client=mock_http,
        )

        token1 = await client.get_token()
        token2 = await client.get_token()
        assert token1 == token2 == "cached-token"
        # Only one HTTP call should have been made
        assert mock_http.post.call_count == 1

    def test_gma_client_get_token_missing_credentials(self):
        """Test error when GMA credentials are not configured."""
        from lightspeed_agent.dcr.gma_client import GMAClient

        with pytest.raises(ValueError, match="GMA_CLIENT_ID"):
            GMAClient(
                api_base_url="https://sso.example.com/apis/beta/acs/v1/",
                client_id="",
                client_secret="",
                token_endpoint="https://sso.example.com/token",
            )

    @pytest.mark.asyncio
    async def test_gma_client_create_tenant_success(self):
        """Test successful tenant creation."""
        from lightspeed_agent.dcr.gma_client import GMAClient

        token_response = httpx.Response(
            status_code=200,
            json={"access_token": "test-token", "expires_in": 300},
            request=httpx.Request("POST", "https://sso.example.com/token"),
        )
        tenant_response = httpx.Response(
            status_code=201,
            json={
                "clientId": "new-client-id",
                "secret": "new-client-secret",
                "name": "gemini-order-123",
                "createdAt": 1774421600,
            },
            request=httpx.Request("POST", "https://sso.example.com/apis/beta/acs/v1/"),
        )
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(side_effect=[token_response, tenant_response])

        client = GMAClient(
            api_base_url="https://sso.example.com/apis/beta/acs/v1/",
            client_id="test-gma-id",
            client_secret="test-gma-secret",
            token_endpoint="https://sso.example.com/token",
            http_client=mock_http,
        )

        result = await client.create_tenant(
            order_id="123",
            redirect_uris=["https://example.com/callback"],
        )

        assert result.client_id == "new-client-id"
        assert result.client_secret == "new-client-secret"
        assert result.name == "gemini-order-123"
        assert result.created_at == 1774421600

    @pytest.mark.asyncio
    async def test_gma_client_create_tenant_failure(self):
        """Test tenant creation failure."""
        from lightspeed_agent.dcr.gma_client import GMAClient, GMAClientError

        token_response = httpx.Response(
            status_code=200,
            json={"access_token": "test-token", "expires_in": 300},
            request=httpx.Request("POST", "https://sso.example.com/token"),
        )
        error_response = httpx.Response(
            status_code=400,
            json={"error": "invalid_request"},
            request=httpx.Request("POST", "https://sso.example.com/apis/beta/acs/v1/"),
        )
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(side_effect=[token_response, error_response])

        client = GMAClient(
            api_base_url="https://sso.example.com/apis/beta/acs/v1/",
            client_id="test-gma-id",
            client_secret="test-gma-secret",
            token_endpoint="https://sso.example.com/token",
            http_client=mock_http,
        )

        with pytest.raises(GMAClientError) as exc_info:
            await client.create_tenant(order_id="123")

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_gma_client_create_tenant_invalid_redirect_uri(self):
        """Test that invalid redirect URIs are rejected before calling the API."""
        from lightspeed_agent.dcr.gma_client import GMAClient, GMAClientError

        client = GMAClient(
            api_base_url="https://sso.example.com/apis/beta/acs/v1/",
            client_id="test-gma-id",
            client_secret="test-gma-secret",
            token_endpoint="https://sso.example.com/token",
        )

        with pytest.raises(GMAClientError) as exc_info:
            await client.create_tenant(
                order_id="123",
                redirect_uris=["http://evil.com/callback"],
            )

        assert exc_info.value.status_code == 400
        assert "Invalid redirect URI" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_gma_client_create_tenant_localhost_redirect_allowed(self):
        """Test that http://localhost redirect URIs are allowed."""
        from lightspeed_agent.dcr.gma_client import GMAClient

        token_response = httpx.Response(
            status_code=200,
            json={"access_token": "test-token", "expires_in": 300},
            request=httpx.Request("POST", "https://sso.example.com/token"),
        )
        tenant_response = httpx.Response(
            status_code=201,
            json={
                "clientId": "new-client-id",
                "secret": "new-client-secret",
                "name": "gemini-order-123",
                "createdAt": 1774421600,
            },
            request=httpx.Request("POST", "https://sso.example.com/apis/beta/acs/v1/"),
        )
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(side_effect=[token_response, tenant_response])

        client = GMAClient(
            api_base_url="https://sso.example.com/apis/beta/acs/v1/",
            client_id="test-gma-id",
            client_secret="test-gma-secret",
            token_endpoint="https://sso.example.com/token",
            http_client=mock_http,
        )

        result = await client.create_tenant(
            order_id="123",
            redirect_uris=["http://localhost:8080/callback"],
        )

        assert result.client_id == "new-client-id"

    @pytest.mark.asyncio
    async def test_gma_client_delete_tenant_404_is_idempotent(self):
        """Test that deleting a non-existent tenant (404) succeeds silently."""
        from lightspeed_agent.dcr.gma_client import GMAClient

        token_response = httpx.Response(
            status_code=200,
            json={"access_token": "test-token", "expires_in": 300},
            request=httpx.Request("POST", "https://sso.example.com/token"),
        )
        not_found_response = httpx.Response(
            status_code=404,
            json={"error": "not_found"},
            request=httpx.Request(
                "DELETE", "https://sso.example.com/apis/beta/acs/v1/client-123"
            ),
        )
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(return_value=token_response)
        mock_http.delete = AsyncMock(return_value=not_found_response)

        client = GMAClient(
            api_base_url="https://sso.example.com/apis/beta/acs/v1/",
            client_id="test-gma-id",
            client_secret="test-gma-secret",
            token_endpoint="https://sso.example.com/token",
            http_client=mock_http,
        )

        # Should NOT raise — 404 means already deleted
        await client.delete_tenant("client-123")
