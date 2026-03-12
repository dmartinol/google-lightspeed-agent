"""Data models for Dynamic Client Registration (DCR)."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class DCRErrorCode(StrEnum):
    """DCR error codes per RFC 7591."""

    INVALID_REQUEST = "invalid_request"
    INVALID_SOFTWARE_STATEMENT = "invalid_software_statement"
    UNAPPROVED_SOFTWARE_STATEMENT = "unapproved_software_statement"
    INVALID_REDIRECT_URI = "invalid_redirect_uri"
    INVALID_CLIENT_METADATA = "invalid_client_metadata"
    SERVER_ERROR = "server_error"


class DCRError(BaseModel):
    """DCR error response per RFC 7591."""

    error: DCRErrorCode = Field(..., description="Error code")
    error_description: str | None = Field(None, description="Human-readable error description")


class GoogleClaims(BaseModel):
    """Google-specific claims in the software_statement JWT."""

    order: str = Field(..., description="Marketplace Order ID")


class GoogleJWTClaims(BaseModel):
    """Claims from Google's software_statement JWT.

    Based on the Google Cloud Marketplace DCR specification.
    """

    iss: str = Field(
        ...,
        description="Issuer - Google's service account URL",
    )
    iat: int = Field(
        ...,
        description="Issued at timestamp",
    )
    exp: int = Field(
        ...,
        description="Expiration timestamp",
    )
    aud: str = Field(
        ...,
        description="Audience - Agent's provider URL",
    )
    sub: str = Field(
        ...,
        description="Subject - Procurement Account ID",
    )
    auth_app_redirect_uris: list[str] = Field(
        default_factory=list,
        description="Redirect URIs for OAuth flow",
    )
    google: GoogleClaims = Field(
        ...,
        description="Google-specific claims",
    )

    class Config:
        extra = "allow"  # Allow unknown fields per spec

    @property
    def order_id(self) -> str:
        """Get the Order ID from Google claims."""
        return self.google.order

    @property
    def account_id(self) -> str:
        """Get the Procurement Account ID (sub claim)."""
        return self.sub


class DCRRequest(BaseModel):
    """DCR request payload.

    Contains the software_statement JWT signed by Google, and optionally
    static client credentials (client_id + client_secret) per RFC 7591.
    When static credentials are provided and DCR_ENABLED=false, they are
    validated against Red Hat SSO and stored for the order.
    """

    software_statement: str = Field(
        ...,
        description="JWT signed by Google containing registration claims",
    )
    client_id: str | None = Field(
        None,
        description="Pre-registered OAuth 2.0 client identifier (static credentials mode)",
    )
    client_secret: str | None = Field(
        None,
        description="Pre-registered OAuth 2.0 client secret (static credentials mode)",
    )


class DCRResponse(BaseModel):
    """DCR success response per RFC 7591.

    Returns the newly created OAuth 2.0 client credentials.
    """

    client_id: str = Field(
        ...,
        description="The new OAuth 2.0 client identifier",
    )
    client_secret: str = Field(
        ...,
        description="The new OAuth 2.0 client secret",
    )
    client_secret_expires_at: int = Field(
        default=0,
        description="Secret expiration (0 = never expires)",
    )
    client_id_issued_at: int | None = Field(
        default=None,
        description="Timestamp when client_id was issued",
    )
    registration_access_token: str | None = Field(
        default=None,
        description="Token for accessing registration endpoint",
    )
    registration_client_uri: str | None = Field(
        default=None,
        description="URI for client configuration endpoint",
    )
    redirect_uris: list[str] | None = Field(
        default=None,
        description="Registered redirect URIs",
    )
    grant_types: list[str] | None = Field(
        default=None,
        description="Allowed grant types",
    )
    token_endpoint_auth_method: str | None = Field(
        default=None,
        description="Token endpoint authentication method",
    )


class RegisteredClient(BaseModel):
    """Stored registered client information.

    Note: Per Google's DCR spec, we must return the SAME client_id and
    client_secret for repeat requests with the same order ID. Therefore,
    we store the encrypted secret (not just a hash) so we can return it.
    """

    client_id: str = Field(..., description="OAuth client ID")
    client_secret_encrypted: str = Field(..., description="Encrypted client secret (Fernet)")
    order_id: str = Field(..., description="Associated Order ID")
    account_id: str = Field(..., description="Associated Account ID")
    redirect_uris: list[str] = Field(
        default_factory=list,
        description="Registered redirect URIs",
    )
    grant_types: list[str] = Field(
        default_factory=lambda: ["authorization_code", "refresh_token"],
        description="Allowed grant types",
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Registration timestamp",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata",
    )
