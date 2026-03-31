"""AgentCard builder for the Lightspeed Agent using a2a-sdk."""

from typing import Any

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentExtension,
    AgentProvider,
    AgentSkill,
    AuthorizationCodeOAuthFlow,
    ClientCredentialsOAuthFlow,
    OAuth2SecurityScheme,
    OAuthFlows,
    SecurityScheme,
)

from lightspeed_agent.config import get_settings
from lightspeed_agent.tools.skills import get_skills_for_agent_card


def _build_skills() -> list[AgentSkill]:
    """Build AgentSkill objects from MCP skills."""
    settings = get_settings()
    raw_skills = get_skills_for_agent_card(read_only=settings.mcp_read_only)

    skills = []
    for skill_data in raw_skills:
        skill = AgentSkill(
            id=skill_data["id"],
            name=skill_data["name"],
            description=skill_data["description"],
            tags=skill_data.get("tags", []),
            examples=skill_data.get("examples", []),
        )
        skills.append(skill)

    return skills


def _build_oauth_security_scheme() -> OAuth2SecurityScheme:
    """Build OAuth 2.0 security scheme for Red Hat SSO."""
    settings = get_settings()

    token_url = f"{settings.red_hat_sso_issuer}/protocol/openid-connect/token"

    scopes = {
        "openid": "OpenID Connect scope",
        "profile": "User profile information",
        "email": "User email address",
        "api.console": "Access to Red Hat console APIs",
        "api.ocm": "Access to Red Hat OpenShift Cluster Manager APIs",
    }

    auth_code_flow = AuthorizationCodeOAuthFlow(
        authorization_url=f"{settings.red_hat_sso_issuer}/protocol/openid-connect/auth",
        token_url=token_url,
        scopes=scopes,
    )

    client_credentials_flow = ClientCredentialsOAuthFlow(
        token_url=token_url,
        scopes=scopes,
    )

    return OAuth2SecurityScheme(
        type="oauth2",
        description="Red Hat SSO OAuth 2.0 Authentication",
        flows=OAuthFlows(
            authorization_code=auth_code_flow,
            client_credentials=client_credentials_flow,
        ),
    )


def _build_dcr_extension() -> AgentExtension:
    """Build DCR extension for Google Marketplace integration.

    DCR is handled by the marketplace-handler service, which is separate
    from the agent service. The marketplace handler URL should be configured
    via MARKETPLACE_HANDLER_URL environment variable.
    """
    settings = get_settings()

    # Use marketplace handler URL if configured, otherwise fall back to agent URL
    # In production, these should be different services
    handler_url = settings.marketplace_handler_url or settings.agent_provider_url

    return AgentExtension(
        uri="https://cloud.google.com/marketplace/docs/partners/ai-agents/setup-dcr",
        params={
            "target_url": f"{handler_url}/dcr",
        },
    )


def _build_access_mode_extension() -> AgentExtension:
    """Build access mode extension.

    Indicates whether the agent operates in read-only mode and lists
    the OAuth2 scopes reflecting this access level.
    """
    settings = get_settings()

    return AgentExtension(
        uri="urn:redhat:lightspeed:access-mode",
        description="Agent access mode and permitted OAuth2 scopes",
        params={
            "read_only": settings.mcp_read_only,
            "oauth2_scopes": settings.allowed_scopes_list,
        },
    )


def _build_rate_limit_extension() -> AgentExtension:
    """Build rate limiting metadata extension.

    Exposes the agent's rate limits so downstream agents and clients
    can plan their request patterns accordingly.
    """
    settings = get_settings()

    return AgentExtension(
        uri="urn:redhat:lightspeed:rate-limiting",
        description="Agent rate limiting constraints",
        params={
            "requests_per_minute": settings.rate_limit_requests_per_minute,
            "requests_per_hour": settings.rate_limit_requests_per_hour,
        },
    )


def _build_capabilities() -> AgentCapabilities:
    """Build agent capabilities with extensions."""
    dcr_extension = _build_dcr_extension()
    access_mode_extension = _build_access_mode_extension()
    rate_limit_extension = _build_rate_limit_extension()

    return AgentCapabilities(
        streaming=True,
        push_notifications=False,
        state_transition_history=False,
        extensions=[dcr_extension, access_mode_extension, rate_limit_extension],
    )


def build_agent_card() -> AgentCard:
    """Build the complete AgentCard for the Lightspeed Agent.

    Returns:
        Configured AgentCard instance with all capabilities,
        skills, and security requirements.
    """
    settings = get_settings()

    provider = AgentProvider(
        organization="Red Hat",
        url=settings.agent_provider_organization_url,
    )

    oauth_scheme = _build_oauth_security_scheme()
    capabilities = _build_capabilities()
    skills = _build_skills()

    agent_card = AgentCard(
        name=settings.agent_display_name,
        description=settings.agent_description,
        version="0.1.0",
        url=f"{settings.agent_provider_url}/",
        protocol_version="0.3.0",
        provider=provider,
        capabilities=capabilities,
        skills=skills,
        security_schemes={
            "redhat_sso": SecurityScheme(root=oauth_scheme),
        },
        security=[
            {"redhat_sso": ["openid", "api.console", "api.ocm"]},
        ],
        default_input_modes=["text"],
        default_output_modes=["text"],
    )

    return agent_card


def get_agent_card_dict() -> dict[str, Any]:
    """Get the AgentCard as a dictionary for JSON serialization.

    Returns:
        AgentCard data as a dictionary with proper field aliasing.
    """
    agent_card = build_agent_card()

    # Convert to dict, handling both Pydantic v1 and v2 style
    if hasattr(agent_card, "model_dump"):
        return agent_card.model_dump(by_alias=True, exclude_none=True)
    else:
        return agent_card.dict(by_alias=True, exclude_none=True)
