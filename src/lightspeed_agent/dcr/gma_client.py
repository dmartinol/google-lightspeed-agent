"""GMA SSO API client for creating OAuth tenant clients in Red Hat SSO."""

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from lightspeed_agent.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class GMAClientResponse:
    """Response from GMA tenant creation API."""

    client_id: str
    client_secret: str
    name: str
    created_at: int | None = None


class GMAClientError(Exception):
    """Error from GMA API operation."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.details = details or {}


class GMAClient:
    """Client for the GMA SSO API.

    Creates OAuth tenant clients in Red Hat SSO via the GMA API:
    POST {sso_issuer}/apis/beta/acs/v1/

    Authentication: client_credentials grant with scope=api.iam.clients.gma
    using dedicated GMA_CLIENT_ID / GMA_CLIENT_SECRET credentials.
    """

    def __init__(
        self,
        api_base_url: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        token_endpoint: str | None = None,
        client_name_prefix: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialize the GMA client.

        Args:
            api_base_url: GMA API base URL. Defaults to settings.
            client_id: GMA client ID for authentication. Defaults to settings.
            client_secret: GMA client secret for authentication. Defaults to settings.
            token_endpoint: Token endpoint URL. Defaults to settings.
            client_name_prefix: Prefix for created tenant names. Defaults to settings.
            http_client: Optional HTTP client for testing.
        """
        settings = get_settings()
        self._api_base_url = api_base_url or settings.gma_api_base_url
        self._client_id = client_id or settings.gma_client_id
        self._client_secret = client_secret or settings.gma_client_secret
        self._token_endpoint = token_endpoint or settings.keycloak_token_endpoint
        self._client_name_prefix = client_name_prefix or settings.dcr_client_name_prefix
        self._http_client = http_client

        # Token cache
        self._access_token: str | None = None
        self._token_expires_at: float = 0

    async def get_token(self) -> str:
        """Obtain an access token via client_credentials grant.

        Caches the token and reuses it until expiry (minus 30s safety margin).

        Returns:
            Access token string.

        Raises:
            GMAClientError: If token acquisition fails.
        """
        now = time.monotonic()
        if self._access_token and now < self._token_expires_at:
            return self._access_token

        if not self._client_id or not self._client_secret:
            raise GMAClientError(
                "GMA_CLIENT_ID and GMA_CLIENT_SECRET must be configured",
                status_code=500,
            )

        logger.info("Requesting GMA access token for client_id=%s", self._client_id)

        try:
            if self._http_client:
                response = await self._http_client.post(
                    self._token_endpoint,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "scope": "api.iam.clients.gma",
                    },
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        self._token_endpoint,
                        data={
                            "grant_type": "client_credentials",
                            "client_id": self._client_id,
                            "client_secret": self._client_secret,
                            "scope": "api.iam.clients.gma",
                        },
                        timeout=30.0,
                    )

            if response.status_code == 200:
                data = response.json()
                self._access_token = data["access_token"]
                expires_in = data.get("expires_in", 300)
                self._token_expires_at = now + expires_in - 30
                logger.info("GMA access token acquired (expires_in=%d)", expires_in)
                return self._access_token

            error_data = {}
            try:
                error_data = response.json()
            except Exception:
                error_data = {"error": response.text}

            logger.error(
                "Failed to get GMA token: status=%d, error=%s",
                response.status_code,
                error_data,
            )
            raise GMAClientError(
                f"Failed to get GMA token: {error_data.get('error', 'Unknown error')}",
                status_code=response.status_code,
                details=error_data,
            )

        except httpx.RequestError as e:
            logger.exception("HTTP error getting GMA token: %s", e)
            raise GMAClientError(
                f"HTTP error getting GMA token: {e}",
                status_code=500,
            ) from e

    async def create_tenant(
        self,
        order_id: str,
        redirect_uris: list[str] | None = None,
    ) -> GMAClientResponse:
        """Create a new OAuth tenant client via the GMA API.

        Args:
            order_id: The marketplace order ID (used as orgId and in tenant name).
            redirect_uris: OAuth redirect URIs for the tenant.

        Returns:
            GMAClientResponse with tenant credentials.

        Raises:
            GMAClientError: If tenant creation fails.
        """
        token = await self.get_token()
        tenant_name = f"{self._client_name_prefix}{order_id}"

        request_body = {
            "name": tenant_name,
            "redirectUris": redirect_uris or [],
            "orgId": order_id,
        }

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        logger.info("Creating GMA tenant: %s (orgId=%s)", tenant_name, order_id)

        try:
            if self._http_client:
                response = await self._http_client.post(
                    self._api_base_url,
                    json=request_body,
                    headers=headers,
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        self._api_base_url,
                        json=request_body,
                        headers=headers,
                        timeout=30.0,
                    )

            if response.status_code == 201:
                data = response.json()
                logger.info(
                    "Successfully created GMA tenant: %s (clientId=%s)",
                    tenant_name,
                    data.get("clientId"),
                )
                return GMAClientResponse(
                    client_id=data["clientId"],
                    client_secret=data["secret"],
                    name=data.get("name", tenant_name),
                    created_at=data.get("createdAt"),
                )

            error_data = {}
            try:
                error_data = response.json()
            except Exception:
                error_data = {"error": response.text}

            logger.error(
                "Failed to create GMA tenant: status=%d, error=%s",
                response.status_code,
                error_data,
            )
            raise GMAClientError(
                f"Failed to create GMA tenant: "
                f"{error_data.get('error', 'Unknown error')}",
                status_code=response.status_code,
                details=error_data,
            )

        except httpx.RequestError as e:
            logger.exception("HTTP error calling GMA API: %s", e)
            raise GMAClientError(
                f"HTTP error calling GMA API: {e}",
                status_code=500,
            ) from e

    async def list_tenants(self, org_id: str) -> list[dict[str, Any]]:
        """List GMA tenants for an organization.

        Args:
            org_id: The organization ID to filter by.

        Returns:
            List of tenant dictionaries.

        Raises:
            GMAClientError: If the API call fails.
        """
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}"}

        try:
            if self._http_client:
                response = await self._http_client.get(
                    self._api_base_url,
                    params={"orgId": org_id},
                    headers=headers,
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        self._api_base_url,
                        params={"orgId": org_id},
                        headers=headers,
                        timeout=30.0,
                    )

            if response.status_code == 200:
                result: list[dict[str, Any]] = response.json()
                return result

            error_data = {}
            try:
                error_data = response.json()
            except Exception:
                error_data = {"error": response.text}

            raise GMAClientError(
                f"Failed to list GMA tenants: "
                f"{error_data.get('error', 'Unknown error')}",
                status_code=response.status_code,
                details=error_data,
            )

        except httpx.RequestError as e:
            raise GMAClientError(
                f"HTTP error listing GMA tenants: {e}",
                status_code=500,
            ) from e

    async def delete_tenant(self, client_id: str) -> None:
        """Delete a GMA tenant by client ID.

        Args:
            client_id: The client ID of the tenant to delete.

        Raises:
            GMAClientError: If the deletion fails.
        """
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{self._api_base_url}{client_id}"

        try:
            if self._http_client:
                response = await self._http_client.delete(url, headers=headers)
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.delete(
                        url,
                        headers=headers,
                        timeout=30.0,
                    )

            if response.status_code == 204:
                logger.info("Deleted GMA tenant: %s", client_id)
                return

            error_data = {}
            try:
                error_data = response.json()
            except Exception:
                error_data = {"error": response.text}

            raise GMAClientError(
                f"Failed to delete GMA tenant: "
                f"{error_data.get('error', 'Unknown error')}",
                status_code=response.status_code,
                details=error_data,
            )

        except httpx.RequestError as e:
            raise GMAClientError(
                f"HTTP error deleting GMA tenant: {e}",
                status_code=500,
            ) from e


# Global client instance
_gma_client: GMAClient | None = None


def get_gma_client() -> GMAClient:
    """Get the global GMA client instance.

    Returns:
        GMAClient instance.
    """
    global _gma_client
    if _gma_client is None:
        _gma_client = GMAClient()
    return _gma_client
