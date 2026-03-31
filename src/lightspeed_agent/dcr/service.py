"""DCR service for handling Dynamic Client Registration requests."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import httpx
from cryptography.fernet import Fernet, InvalidToken

from lightspeed_agent.config import get_settings
from lightspeed_agent.dcr.gma_client import (
    GMAClient,
    GMAClientError,
    get_gma_client,
)
from lightspeed_agent.dcr.google_jwt import GoogleJWTValidator, get_google_jwt_validator
from lightspeed_agent.dcr.models import (
    DCRError,
    DCRErrorCode,
    DCRRequest,
    DCRResponse,
    GoogleJWTClaims,
    RegisteredClient,
)
from lightspeed_agent.dcr.repository import DCRClientRepository, get_dcr_client_repository

if TYPE_CHECKING:
    from lightspeed_agent.marketplace.service import ProcurementService

logger = logging.getLogger(__name__)


class DCRService:
    """Service for handling Dynamic Client Registration.

    This service:
    - Validates software_statement JWTs from Google
    - Cross-references with Marketplace Procurement data
    - Creates OAuth client credentials (real DCR or static)
    - Returns RFC 7591 compliant responses

    Modes:
    - DCR_ENABLED=true: Creates real OAuth tenant clients in Red Hat SSO
      via the GMA API
    - DCR_ENABLED=false: Accepts static credentials (client_id + client_secret)
      provided in the DCR request body. Validates them against the Red Hat SSO
      token endpoint, stores them, and returns them.
    """

    def __init__(
        self,
        jwt_validator: GoogleJWTValidator | None = None,
        procurement_service: ProcurementService | None = None,
        gma_client: GMAClient | None = None,
        client_repository: DCRClientRepository | None = None,
    ) -> None:
        """Initialize the DCR service.

        Args:
            jwt_validator: Google JWT validator.
            procurement_service: Procurement service for validation.
            gma_client: GMA client for real DCR tenant creation.
            client_repository: Repository for storing client mappings.
        """
        self._jwt_validator = jwt_validator or get_google_jwt_validator()
        if procurement_service is None:
            from lightspeed_agent.marketplace.service import get_procurement_service

            procurement_service = get_procurement_service()
        self._procurement_service = procurement_service
        self._gma_client = gma_client
        self._client_repository = client_repository or get_dcr_client_repository()
        self._settings = get_settings()

        # Fernet cipher for encrypting client secrets.
        # In production (Cloud Run), DCR_ENCRYPTION_KEY is required to prevent
        # silent data loss from missing encryption configuration.
        self._fernet: Fernet | None = None
        if self._settings.dcr_encryption_key:
            try:
                self._fernet = Fernet(self._settings.dcr_encryption_key.encode())
            except Exception as e:
                raise ValueError(
                    f"Invalid DCR_ENCRYPTION_KEY: {e}. "
                    "Generate a valid key with: "
                    "python -c 'from cryptography.fernet import Fernet; "
                    "print(Fernet.generate_key().decode())'"
                ) from e
        elif os.getenv("K_SERVICE"):
            raise ValueError(
                "DCR_ENCRYPTION_KEY is required in production "
                f"(K_SERVICE={os.getenv('K_SERVICE')}). "
                "Client secrets cannot be stored without an encryption key. "
                "Generate a key with: python -c 'from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())'"
            )

    def _get_gma_client(self) -> GMAClient:
        """Get the GMA client (lazy initialization)."""
        if self._gma_client is None:
            self._gma_client = get_gma_client()
        return self._gma_client

    def _encrypt_secret(self, secret: str) -> str:
        """Encrypt a client secret for storage.

        Args:
            secret: The plaintext client secret.

        Returns:
            Encrypted secret as base64 string.

        Raises:
            RuntimeError: If DCR_ENCRYPTION_KEY is not configured.
        """
        if not self._fernet:
            raise RuntimeError(
                "Cannot encrypt client secret: DCR_ENCRYPTION_KEY is not configured. "
                "Set DCR_ENCRYPTION_KEY to a valid Fernet key before performing DCR operations."
            )
        return self._fernet.encrypt(secret.encode()).decode()

    def _decrypt_secret(self, encrypted_secret: str) -> str | None:
        """Decrypt a stored client secret.

        Args:
            encrypted_secret: The encrypted secret.

        Returns:
            Decrypted secret or None if decryption fails.
        """
        if not self._fernet:
            logger.error("Cannot decrypt: no encryption key available")
            return None
        try:
            return self._fernet.decrypt(encrypted_secret.encode()).decode()
        except InvalidToken:
            logger.error("Failed to decrypt client secret: invalid token")
            return None

    async def register_client(
        self,
        request: DCRRequest,
    ) -> DCRResponse | DCRError:
        """Process a Dynamic Client Registration request.

        Args:
            request: The DCR request containing software_statement.

        Returns:
            DCRResponse on success, DCRError on failure.
        """
        logger.info("Processing DCR request (dcr_enabled=%s)", self._settings.dcr_enabled)

        # Step 1: Validate the software_statement JWT
        validation_result = await self._jwt_validator.validate_software_statement(
            request.software_statement
        )

        if isinstance(validation_result, DCRError):
            return validation_result

        claims: GoogleJWTClaims = validation_result

        # Step 2: Validate the Procurement Account ID
        if not await self._validate_account(claims.account_id):
            logger.warning("Invalid Procurement Account ID: %s", claims.account_id)
            return DCRError(
                error=DCRErrorCode.UNAPPROVED_SOFTWARE_STATEMENT,
                error_description=f"Invalid Procurement Account ID: {claims.account_id}",
            )

        # Step 3: Validate the Order ID
        if not await self._validate_order(claims.order_id):
            logger.warning("Invalid Order ID: %s", claims.order_id)
            return DCRError(
                error=DCRErrorCode.UNAPPROVED_SOFTWARE_STATEMENT,
                error_description=f"Invalid Order ID: {claims.order_id}",
            )

        # Step 4: Check if client already exists for this order
        existing_client = await self._client_repository.get_by_order_id(claims.order_id)
        if existing_client:
            logger.info(
                "Returning existing credentials for order: %s (client_id=%s)",
                claims.order_id,
                existing_client.client_id,
            )
            return await self._return_existing_credentials(existing_client)

        # Step 5: Create new OAuth client credentials
        if self._settings.dcr_enabled:
            return await self._create_real_client(claims)

        # DCR disabled: accept static credentials from the request body
        return await self._store_static_credentials(request, claims)

    async def _validate_account(self, account_id: str) -> bool:
        """Validate that the Procurement Account ID is valid."""
        if self._settings.skip_jwt_validation:
            logger.warning("Skipping account validation - development mode")
            return True
        return await self._procurement_service.is_valid_account(account_id)

    async def _validate_order(self, order_id: str) -> bool:
        """Validate that the Order ID is valid."""
        if self._settings.skip_jwt_validation:
            logger.warning("Skipping order validation - development mode")
            return True
        return await self._procurement_service.is_valid_order(order_id)

    async def _validate_credentials(self, client_id: str, client_secret: str) -> bool:
        """Validate static credentials against Red Hat SSO token endpoint.

        Attempts a client_credentials grant to verify the credentials are
        registered and valid in the OAuth server.

        Args:
            client_id: The OAuth client ID to validate.
            client_secret: The OAuth client secret to validate.

        Returns:
            True if the credentials are valid, False otherwise.
        """
        token_url = self._settings.sso_token_endpoint
        logger.info("Validating static credentials for client_id=%s", client_id)

        try:
            async with httpx.AsyncClient() as http:
                resp = await http.post(
                    token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                    timeout=30.0,
                )

            if resp.status_code == 200:
                logger.info("Static credentials validated for client_id=%s", client_id)
                return True

            logger.warning(
                "Static credential validation failed for client_id=%s: status=%d",
                client_id,
                resp.status_code,
            )
            return False

        except httpx.RequestError as e:
            logger.error(
                "HTTP error validating credentials for client_id=%s: %s",
                client_id,
                e,
            )
            return False

    async def _store_static_credentials(
        self,
        request: DCRRequest,
        claims: GoogleJWTClaims,
    ) -> DCRResponse | DCRError:
        """Store static credentials provided in the DCR request body.

        When DCR_ENABLED=false, the caller provides pre-registered client_id
        and client_secret in the request body. This method validates them
        against the Red Hat SSO token endpoint, encrypts and stores them
        linked to the order_id, and returns them.

        Args:
            request: The DCR request containing static credentials.
            claims: Validated JWT claims.

        Returns:
            DCRResponse with the stored credentials, or DCRError on failure.
        """
        if not request.client_id or not request.client_secret:
            logger.warning(
                "Static credentials mode requires client_id and client_secret "
                "in the request body (order: %s)",
                claims.order_id,
            )
            return DCRError(
                error=DCRErrorCode.INVALID_CLIENT_METADATA,
                error_description=(
                    "Static credentials mode: both client_id and client_secret "
                    "must be provided in the request body."
                ),
            )

        # Validate credentials against Red Hat SSO
        if not await self._validate_credentials(request.client_id, request.client_secret):
            return DCRError(
                error=DCRErrorCode.INVALID_CLIENT_METADATA,
                error_description=(
                    f"Invalid client credentials: client_id={request.client_id} "
                    "failed validation against the OAuth server."
                ),
            )

        # Encrypt and store
        encrypted_secret = self._encrypt_secret(request.client_secret)

        try:
            await self._client_repository.create(
                client_id=request.client_id,
                client_secret_encrypted=encrypted_secret,
                order_id=claims.order_id,
                account_id=claims.account_id,
                redirect_uris=claims.auth_app_redirect_uris,
                grant_types=["authorization_code", "refresh_token", "client_credentials"],
                metadata={
                    "iss": claims.iss,
                    "aud": claims.aud,
                    "registration_mode": "static",
                },
            )
        except Exception as e:
            logger.exception("Failed to store static credentials: %s", e)
            return DCRError(
                error=DCRErrorCode.SERVER_ERROR,
                error_description=f"Failed to store client credentials: {e}",
            )

        logger.info(
            "Stored static credentials for order %s: client_id=%s",
            claims.order_id,
            request.client_id,
        )

        return DCRResponse(
            client_id=request.client_id,
            client_secret=request.client_secret,
            client_secret_expires_at=0,
        )

    async def _return_existing_credentials(
        self,
        existing_client: RegisteredClient,
    ) -> DCRResponse | DCRError:
        """Return the existing credentials for an order.

        Per Google's DCR spec: "return the existing client_id and client_secret
        pair for the given order"
        """
        client_secret = self._decrypt_secret(existing_client.client_secret_encrypted)
        if not client_secret:
            logger.error(
                "Failed to decrypt secret for client %s",
                existing_client.client_id,
            )
            return DCRError(
                error=DCRErrorCode.SERVER_ERROR,
                error_description="Failed to retrieve existing credentials",
            )

        return DCRResponse(
            client_id=existing_client.client_id,
            client_secret=client_secret,
            client_secret_expires_at=0,
        )

    async def _create_real_client(
        self,
        claims: GoogleJWTClaims,
    ) -> DCRResponse | DCRError:
        """Create a real OAuth tenant client in Red Hat SSO via the GMA API.

        Args:
            claims: Validated JWT claims.

        Returns:
            DCRResponse with new credentials, or DCRError on failure.
        """
        logger.info(
            "Creating OAuth tenant client via GMA API for order: %s",
            claims.order_id,
        )

        try:
            gma_client = self._get_gma_client()
            response = await gma_client.create_tenant(
                order_id=claims.order_id,
                redirect_uris=claims.auth_app_redirect_uris,
            )

            # Encrypt secret for storage
            encrypted_secret = self._encrypt_secret(response.client_secret)

            # Store client mapping in database
            await self._client_repository.create(
                client_id=response.client_id,
                client_secret_encrypted=encrypted_secret,
                order_id=claims.order_id,
                account_id=claims.account_id,
                redirect_uris=claims.auth_app_redirect_uris,
                grant_types=["authorization_code", "refresh_token", "client_credentials"],
                metadata={
                    "iss": claims.iss,
                    "aud": claims.aud,
                    "client_name": response.name,
                    "registration_mode": "gma",
                },
            )

            logger.info(
                "Successfully created OAuth tenant client for order %s: client_id=%s",
                claims.order_id,
                response.client_id,
            )

            return DCRResponse(
                client_id=response.client_id,
                client_secret=response.client_secret,
                client_secret_expires_at=0,
            )

        except GMAClientError as e:
            logger.exception("GMA API error: %s", e)
            if e.status_code and e.status_code < 500:
                return DCRError(
                    error=DCRErrorCode.INVALID_REDIRECT_URI,
                    error_description=f"Failed to create OAuth tenant client: {e}",
                )
            return DCRError(
                error=DCRErrorCode.SERVER_ERROR,
                error_description=f"Failed to create OAuth tenant client: {e}",
            )
        except Exception as e:
            logger.exception("Unexpected error creating OAuth tenant client: %s", e)
            return DCRError(
                error=DCRErrorCode.SERVER_ERROR,
                error_description=f"Failed to create OAuth tenant client: {e}",
            )

    async def delete_client(self, order_id: str) -> None:
        """Delete an OAuth client associated with a marketplace order.

        For GMA-created clients, deletes the tenant from Red Hat SSO via the
        GMA API before removing the local DB record. For static credentials,
        only the local record is removed.

        Args:
            order_id: The marketplace order ID (entitlement ID).

        Raises:
            GMAClientError: If the GMA API deletion fails (caller should retry).
        """
        client = await self._client_repository.get_by_order_id(order_id)
        if not client:
            logger.info("No DCR client found for order_id=%s, nothing to delete", order_id)
            return

        registration_mode = client.metadata.get("registration_mode")

        if registration_mode == "gma":
            logger.info(
                "Deleting GMA tenant for order %s: client_id=%s",
                order_id,
                client.client_id,
            )
            gma_client = self._get_gma_client()
            await gma_client.delete_tenant(client.client_id)

        await self._client_repository.delete_by_order_id(order_id)
        logger.info(
            "Deleted DCR client for order %s (mode=%s)",
            order_id,
            registration_mode,
        )

    async def get_client(self, client_id: str) -> RegisteredClient | None:
        """Get a registered client by client_id.

        Args:
            client_id: The OAuth client ID.

        Returns:
            RegisteredClient if found, None otherwise.
        """
        return await self._client_repository.get_by_client_id(client_id)


# Global service instance
_dcr_service: DCRService | None = None


def get_dcr_service() -> DCRService:
    """Get the global DCR service instance.

    Returns:
        DCRService instance.
    """
    global _dcr_service
    if _dcr_service is None:
        _dcr_service = DCRService()
    return _dcr_service
