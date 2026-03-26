"""Application settings and configuration management."""

import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Google AI / Gemini Configuration
    google_genai_use_vertexai: bool = Field(
        default=False,
        description="Use Vertex AI instead of Google AI Studio",
    )
    google_api_key: str | None = Field(
        default=None,
        description="Google AI Studio API key",
    )
    google_cloud_project: str | None = Field(
        default=None,
        description="Google Cloud project ID for Vertex AI",
    )
    google_cloud_location: str = Field(
        default="us-central1",
        description="Google Cloud location for Vertex AI",
    )
    gemini_model: str = Field(
        default="gemini-2.5-flash",
        description="Gemini model to use",
    )

    # Red Hat SSO Configuration
    red_hat_sso_issuer: str = Field(
        default="https://sso.redhat.com/auth/realms/redhat-external",
        description="Red Hat SSO issuer URL",
    )
    red_hat_sso_client_id: str = Field(
        default="",
        description="OAuth client ID for Red Hat SSO",
    )
    red_hat_sso_client_secret: str = Field(
        default="",
        description="OAuth client secret for Red Hat SSO",
    )
    # Red Hat Lightspeed MCP Configuration
    mcp_transport_mode: Literal["stdio", "http", "sse"] = Field(
        default="stdio",
        description="MCP server transport mode",
    )
    mcp_server_url: str = Field(
        default="http://localhost:8080",
        description="MCP server URL for http/sse modes",
    )
    mcp_read_only: bool = Field(
        default=True,
        description="Enable read-only mode for MCP tools",
    )

    # Agent Configuration
    agent_provider_url: str = Field(
        default="https://localhost:8000",
        description="Agent base URL (where the A2A agent can be reached)",
    )
    agent_provider_organization_url: str = Field(
        default="https://www.redhat.com",
        description=(
            "Agent provider's organization website URL."
            " Used in AgentCard provider.url and as the expected JWT audience"
            " for Google DCR software_statement validation."
        ),
    )
    agent_name: str = Field(
        default="lightspeed_agent",
        description="Agent name (must be a valid Python identifier)",
    )
    agent_description: str = Field(
        default="Red Hat Lightspeed Agent for Google Cloud",
        description="Agent description",
    )
    agent_host: str = Field(
        default="0.0.0.0",
        description="Server host",
    )
    agent_port: int = Field(
        default=8000,
        description="Server port",
    )

    # Marketplace Handler Configuration
    # The marketplace handler is a separate service that handles DCR and Pub/Sub events
    marketplace_handler_url: str = Field(
        default="",
        description=(
            "URL of the marketplace handler service for DCR."
            " If empty, uses agent_provider_url."
        ),
    )

    # Google Cloud Service Control
    service_control_service_name: str = Field(
        default="",
        description=(
            "Service name for Google Cloud Service Control"
            " (e.g., myservice.gcpmarketplace.example.com)"
        ),
    )
    service_control_enabled: bool = Field(
        default=True,
        description="Enable usage reporting to Google Cloud Service Control",
    )
    # Metering recovery: stale claim release and backfill
    metering_stale_claim_minutes: int = Field(
        default=15,
        description="Release rows claimed longer than this (worker crash recovery)",
    )
    metering_backfill_max_age_hours: int = Field(
        default=168,
        description="Backfill only periods within this many hours (default 7 days)",
    )
    metering_backfill_limit_per_run: int = Field(
        default=20,
        description="Max unreported periods to process per backfill run",
    )

    # Rate Limiting (Redis-backed)
    rate_limit_requests_per_minute: int = Field(
        default=60,
        description="Global requests per minute limit",
    )
    rate_limit_requests_per_hour: int = Field(
        default=1000,
        description="Global requests per hour limit",
    )
    rate_limit_redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis URL for distributed rate limiting",
    )
    rate_limit_redis_timeout_ms: int = Field(
        default=200,
        description="Redis operation timeout in milliseconds",
    )
    rate_limit_key_prefix: str = Field(
        default="lightspeed:ratelimit",
        description="Redis key prefix for rate limiting data",
    )

    # Logging
    log_level: str = Field(
        default="INFO",
        description="Logging level",
    )
    log_format: Literal["json", "text"] = Field(
        default="json",
        description="Log format",
    )
    agent_logging_detail: Literal["basic", "detailed"] = Field(
        default="basic",
        description="Agent execution logging detail level. "
        "'basic' logs tool names and token counts. "
        "'detailed' also logs tool arguments and truncated results.",
    )

    # DCR (Dynamic Client Registration) Configuration
    dcr_enabled: bool = Field(
        default=True,
        description=(
            "Enable real DCR with Red Hat SSO (Keycloak)."
            " When disabled, uses pre-seeded credentials from the database."
        ),
    )
    dcr_initial_access_token: str = Field(
        default="",
        description="Keycloak Initial Access Token for creating OAuth clients via DCR",
    )
    dcr_client_name_prefix: str = Field(
        default="gemini-order-",
        description="Prefix for OAuth client names created via DCR",
    )
    dcr_encryption_key: str = Field(
        default="",
        description=(
            "Fernet encryption key for DCR client secrets"
            " (generate with: python -c"
            " 'from cryptography.fernet import Fernet;"
            " print(Fernet.generate_key().decode())')"
        ),
    )

    # Database Configuration
    # Marketplace database: stores accounts, entitlements, DCR clients, usage records
    # This is shared between the marketplace handler and agent for order validation
    database_url: str = Field(
        default="sqlite+aiosqlite:///./lightspeed_agent.db",
        description=(
            "Marketplace database URL (PostgreSQL for production)."
            " Stores accounts, entitlements, DCR clients."
        ),
    )
    database_pool_size: int = Field(
        default=5,
        description="Database connection pool size",
    )
    database_pool_max_overflow: int = Field(
        default=10,
        description="Maximum overflow connections beyond pool size",
    )

    # Session database: stores ADK sessions, conversation history, memory
    # Separate from marketplace DB for security isolation - each agent can have its own
    session_database_url: str = Field(
        default="",
        description=(
            "Session database URL for ADK sessions."
            " If empty, uses DATABASE_URL."
            " For security isolation, use a separate database."
        ),
    )

    # Agent required scopes for token introspection (comma-separated)
    agent_required_scope: str = Field(
        default="api.console,api.ocm",
        description=(
            "Comma-separated OAuth scopes required in access tokens."
            " Checked via token introspection."
        ),
    )

    @property
    def required_scopes_list(self) -> list[str]:
        """Parse comma-separated agent_required_scope into a list."""
        return [s.strip() for s in self.agent_required_scope.split(",") if s.strip()]

    @property
    def keycloak_introspection_endpoint(self) -> str:
        """Get the Keycloak token introspection endpoint URL."""
        return f"{self.red_hat_sso_issuer}/protocol/openid-connect/token/introspect"

    @property
    def keycloak_token_endpoint(self) -> str:
        """Get the Keycloak token endpoint URL."""
        return f"{self.red_hat_sso_issuer}/protocol/openid-connect/token"

    @property
    def keycloak_admin_api_base(self) -> str:
        """Get the Keycloak Admin REST API base URL.

        Derived from the issuer by inserting /admin before /realms/.
        E.g. https://host/auth/realms/myrealm -> https://host/auth/admin/realms/myrealm
        """
        return self.red_hat_sso_issuer.replace("/realms/", "/admin/realms/", 1)

    @property
    def keycloak_dcr_endpoint(self) -> str:
        """Get the Keycloak DCR endpoint URL."""
        # Red Hat SSO issuer format: https://sso.redhat.com/auth/realms/redhat-external
        # DCR endpoint: https://sso.redhat.com/auth/realms/redhat-external/clients-registrations/openid-connect
        return f"{self.red_hat_sso_issuer}/clients-registrations/openid-connect"

    # Development Settings
    debug: bool = Field(
        default=False,
        description="Enable debug mode",
    )
    skip_jwt_validation: bool = Field(
        default=False,
        description="Skip JWT validation (development only)",
    )

    @model_validator(mode="after")
    def _block_skip_jwt_in_production(self) -> "Settings":
        """Prevent SKIP_JWT_VALIDATION from being enabled in production.

        Cloud Run sets K_SERVICE automatically. If that variable is present,
        this is a managed deployment and JWT validation must never be skipped.
        """
        if self.skip_jwt_validation and os.getenv("K_SERVICE"):
            raise ValueError(
                "SKIP_JWT_VALIDATION=true is not allowed in Cloud Run "
                f"(K_SERVICE={os.getenv('K_SERVICE')}). "
                "This setting is intended for local development only."
            )
        return self

    # OpenTelemetry Configuration
    otel_enabled: bool = Field(
        default=False,
        description="Enable OpenTelemetry tracing",
    )
    otel_service_name: str = Field(
        default="lightspeed_agent",
        description="Service name for OpenTelemetry traces",
    )
    otel_exporter_otlp_endpoint: str = Field(
        default="http://localhost:4317",
        description="OTLP exporter endpoint (gRPC)",
    )
    otel_exporter_otlp_http_endpoint: str = Field(
        default="http://localhost:4318",
        description="OTLP exporter endpoint (HTTP)",
    )
    otel_exporter_type: Literal["otlp", "otlp-http", "jaeger", "zipkin", "console"] = Field(
        default="otlp",
        description="Telemetry exporter type",
    )
    otel_traces_sampler: Literal[
        "always_on",
        "always_off",
        "traceidratio",
        "parentbased_always_on",
        "parentbased_always_off",
        "parentbased_traceidratio",
    ] = Field(
        default="always_on",
        description="Trace sampling strategy",
    )
    otel_traces_sampler_arg: float = Field(
        default=1.0,
        description="Sampler argument (e.g., ratio for traceidratio)",
    )


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings."""
    return Settings()
