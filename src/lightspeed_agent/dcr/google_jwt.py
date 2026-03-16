"""Google JWT validator for DCR software_statement verification."""

import asyncio
import logging
import time
from typing import Any

import httpx
import jwt
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from jwt.exceptions import DecodeError, ExpiredSignatureError, InvalidTokenError

from lightspeed_agent.config import get_settings
from lightspeed_agent.dcr.models import DCRError, DCRErrorCode, GoogleJWTClaims

logger = logging.getLogger(__name__)

# Google's expected issuer for DCR software_statement
GOOGLE_DCR_ISSUER = (
    "https://www.googleapis.com/service_accounts/v1/metadata/x509/"
    "cloud-agentspace@system.gserviceaccount.com"
)


class GoogleCertificateCache:
    """Cache for Google's X.509 certificates used to sign software_statement JWTs."""

    def __init__(self, cert_url: str = GOOGLE_DCR_ISSUER, cache_ttl: int = 3600):
        """Initialize the certificate cache.

        Args:
            cert_url: URL to fetch Google's X.509 certificates.
            cache_ttl: Cache time-to-live in seconds (default: 1 hour).
        """
        self._cert_url = cert_url
        self._cache_ttl = cache_ttl
        self._certificates: dict[str, Any] = {}
        self._last_fetch: float = 0
        self._lock = asyncio.Lock()

    async def get_public_key(self, kid: str) -> Any | None:
        """Get the public key for a given key ID.

        Args:
            kid: Key ID from JWT header.

        Returns:
            Public key or None if not found.
        """
        await self._ensure_fresh()
        return self._certificates.get(kid)

    async def _ensure_fresh(self) -> None:
        """Ensure the cache is fresh, fetching new certificates if needed."""
        current_time = time.monotonic()
        if current_time - self._last_fetch < self._cache_ttl and self._certificates:
            return

        async with self._lock:
            # Double-check after acquiring lock
            if current_time - self._last_fetch < self._cache_ttl and self._certificates:
                return

            await self._fetch_certificates()

    async def _fetch_certificates(self) -> None:
        """Fetch certificates from Google's endpoint."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(self._cert_url, timeout=10.0)
                response.raise_for_status()
                certs_data = response.json()

            self._certificates = {}
            for kid, cert_pem in certs_data.items():
                try:
                    # Parse X.509 certificate and extract public key
                    cert = x509.load_pem_x509_certificate(cert_pem.encode())
                    public_key = cert.public_key()
                    # Convert to PEM format for jose library
                    pem = public_key.public_bytes(
                        encoding=serialization.Encoding.PEM,
                        format=serialization.PublicFormat.SubjectPublicKeyInfo,
                    )
                    self._certificates[kid] = pem.decode()
                except Exception as e:
                    logger.warning("Failed to parse certificate for kid %s: %s", kid, e)

            self._last_fetch = time.monotonic()
            logger.info("Fetched %d certificates from Google", len(self._certificates))

        except httpx.HTTPError as e:
            logger.error("Failed to fetch Google certificates: %s", e)
            if not self._certificates:
                raise RuntimeError(f"Failed to fetch certificates: {e}") from e

    async def force_refresh(self) -> None:
        """Force a refresh of the certificate cache."""
        self._last_fetch = 0
        await self._ensure_fresh()


class GoogleJWTValidator:
    """Validator for Google's software_statement JWT in DCR requests."""

    def __init__(self, expected_audience: str | None = None):
        """Initialize the validator.

        Args:
            expected_audience: Expected audience (agent's provider URL).
                             Uses settings if not provided.
        """
        self._settings = get_settings()
        self._expected_audience = (
            expected_audience or self._settings.agent_provider_organization_url
        )
        self._cert_cache = GoogleCertificateCache()

    async def _decode_without_verification(
        self,
        software_statement: str,
    ) -> GoogleJWTClaims | DCRError:
        """Decode a software_statement JWT without signature or issuer verification.

        Used in development mode (SKIP_JWT_VALIDATION=true) to allow testing
        with any service account, not just Google's cloud-agentspace account.
        """
        logger.warning("Skipping JWT signature/issuer validation - development mode")
        try:
            claims = jwt.decode(
                software_statement,
                options={
                    "verify_signature": False,
                    "verify_exp": False,
                    "verify_aud": False,
                },
                algorithms=["RS256"],
            )
        except DecodeError as e:
            return DCRError(
                error=DCRErrorCode.INVALID_SOFTWARE_STATEMENT,
                error_description=f"Failed to decode JWT (dev mode): {e}",
            )

        google_claims = claims.get("google", {})
        if not google_claims.get("order"):
            return DCRError(
                error=DCRErrorCode.INVALID_SOFTWARE_STATEMENT,
                error_description="Missing google.order claim",
            )

        try:
            jwt_claims = GoogleJWTClaims(**claims)
        except Exception as e:
            return DCRError(
                error=DCRErrorCode.INVALID_SOFTWARE_STATEMENT,
                error_description=f"Invalid claims format (dev mode): {e}",
            )

        logger.info(
            "Dev mode: accepted software_statement for order %s (account: %s)",
            jwt_claims.order_id,
            jwt_claims.account_id,
        )
        return jwt_claims

    async def validate_software_statement(
        self,
        software_statement: str,
    ) -> GoogleJWTClaims | DCRError:
        """Validate a software_statement JWT from Google.

        Args:
            software_statement: The JWT string to validate.

        Returns:
            GoogleJWTClaims on success, DCRError on failure.
        """
        if self._settings.skip_jwt_validation:
            return await self._decode_without_verification(software_statement)

        try:
            # Decode header to get key ID
            unverified_header = jwt.get_unverified_header(software_statement)
        except DecodeError as e:
            logger.warning("Failed to decode JWT header: %s", e)
            return DCRError(
                error=DCRErrorCode.INVALID_SOFTWARE_STATEMENT,
                error_description="Invalid JWT format",
            )

        kid = unverified_header.get("kid")
        if not kid:
            return DCRError(
                error=DCRErrorCode.INVALID_SOFTWARE_STATEMENT,
                error_description="JWT header missing 'kid' claim",
            )

        # Verify algorithm is RS256
        alg = unverified_header.get("alg")
        if alg != "RS256":
            return DCRError(
                error=DCRErrorCode.INVALID_SOFTWARE_STATEMENT,
                error_description=f"Unsupported algorithm: {alg}. Expected RS256",
            )

        # Get the signing key from Google's certificates
        public_key = await self._cert_cache.get_public_key(kid)
        if not public_key:
            # Key not found, try refreshing the cache
            await self._cert_cache.force_refresh()
            public_key = await self._cert_cache.get_public_key(kid)

        if not public_key:
            return DCRError(
                error=DCRErrorCode.INVALID_SOFTWARE_STATEMENT,
                error_description=f"Key with ID '{kid}' not found in Google certificates",
            )

        # Validate and decode the JWT
        try:
            claims = jwt.decode(
                software_statement,
                public_key,
                algorithms=["RS256"],
                audience=self._expected_audience,
                options={
                    "verify_aud": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "require": ["iss", "iat", "exp", "aud", "sub"],
                },
            )
        except ExpiredSignatureError:
            logger.warning("Software statement JWT has expired")
            return DCRError(
                error=DCRErrorCode.INVALID_SOFTWARE_STATEMENT,
                error_description="JWT has expired",
            )
        except InvalidTokenError as e:
            logger.warning("JWT validation failed: %s", e)
            return DCRError(
                error=DCRErrorCode.INVALID_SOFTWARE_STATEMENT,
                error_description=str(e),
            )

        # Verify issuer is exactly the expected Google URL
        if claims.get("iss") != GOOGLE_DCR_ISSUER:
            return DCRError(
                error=DCRErrorCode.INVALID_SOFTWARE_STATEMENT,
                error_description=f"Invalid issuer. Expected: {GOOGLE_DCR_ISSUER}",
            )

        # Verify required Google-specific claims
        google_claims = claims.get("google", {})
        if not google_claims.get("order"):
            return DCRError(
                error=DCRErrorCode.INVALID_SOFTWARE_STATEMENT,
                error_description="Missing google.order claim",
            )

        # Parse claims into model
        try:
            jwt_claims = GoogleJWTClaims(**claims)
        except Exception as e:
            logger.warning("Failed to parse JWT claims: %s", e)
            return DCRError(
                error=DCRErrorCode.INVALID_SOFTWARE_STATEMENT,
                error_description=f"Invalid claims format: {e}",
            )

        logger.info(
            "Validated software_statement for order %s (account: %s)",
            jwt_claims.order_id,
            jwt_claims.account_id,
        )
        return jwt_claims


# Global validator instance
_google_jwt_validator: GoogleJWTValidator | None = None


def get_google_jwt_validator() -> GoogleJWTValidator:
    """Get the global Google JWT validator instance.

    Returns:
        GoogleJWTValidator instance.
    """
    global _google_jwt_validator
    if _google_jwt_validator is None:
        _google_jwt_validator = GoogleJWTValidator()
    return _google_jwt_validator
