"""Red Hat Lightspeed MCP tools integration for Google ADK."""

import os
from collections.abc import Callable
from typing import TYPE_CHECKING

from google.adk.tools.mcp_tool.mcp_session_manager import (
    SseConnectionParams,
    StdioConnectionParams,
    StreamableHTTPServerParams,
)
from mcp import StdioServerParameters

from lightspeed_agent.tools.mcp_config import MCPServerConfig
from lightspeed_agent.tools.mcp_headers import create_mcp_header_provider
from lightspeed_agent.tools.schema_sanitizer import SanitizedMcpToolset as McpToolset

if TYPE_CHECKING:
    from google.adk.agents.readonly_context import ReadonlyContext


def create_insights_toolset(
    config: MCPServerConfig | None = None,
    tool_filter: list[str] | None = None,
) -> McpToolset:
    """Create an MCP toolset for Red Hat Insights.

    Args:
        config: Optional MCP server configuration. If None, loads from settings.
        tool_filter: Optional list of tool names to expose. If None, all tools are exposed.

    Returns:
        Configured McpToolset instance.
    """
    if config is None:
        config = MCPServerConfig.from_settings()

    # Create header provider for JWT pass-through to MCP server
    header_provider = create_mcp_header_provider()

    if config.transport_mode == "stdio":
        return _create_stdio_toolset(config, tool_filter)
    elif config.transport_mode == "sse":
        return _create_sse_toolset(config, tool_filter, header_provider)
    elif config.transport_mode == "http":
        return _create_http_toolset(config, tool_filter, header_provider)
    else:
        raise ValueError(f"Unsupported transport mode: {config.transport_mode}")


def _create_stdio_toolset(
    config: MCPServerConfig,
    tool_filter: list[str] | None = None,
) -> McpToolset:
    """Create MCP toolset using stdio transport.

    This is the default mode for local development using containers.
    """
    server_params = StdioServerParameters(
        command=config.get_stdio_command(),
        args=config.get_stdio_args(),
    )

    connection_params = StdioConnectionParams(server_params=server_params)

    return McpToolset(
        connection_params=connection_params,
        tool_filter=tool_filter,
    )


def _create_sse_toolset(
    config: MCPServerConfig,
    tool_filter: list[str] | None = None,
    header_provider: Callable[["ReadonlyContext"], dict[str, str]] | None = None,
) -> McpToolset:
    """Create MCP toolset using SSE transport.

    This is recommended for production deployments.

    Args:
        config: MCP server configuration.
        tool_filter: Optional list of tool names to expose.
        header_provider: Optional callable for dynamic header injection.
    """
    connection_params = SseConnectionParams(
        url=f"{config.server_url}/sse",
    )

    return McpToolset(
        connection_params=connection_params,
        tool_filter=tool_filter,
        header_provider=header_provider,
    )


def _create_http_toolset(
    config: MCPServerConfig,
    tool_filter: list[str] | None = None,
    header_provider: Callable[["ReadonlyContext"], dict[str, str]] | None = None,
) -> McpToolset:
    """Create MCP toolset using Streamable HTTP transport.

    This is the recommended mode for connecting to MCP servers that support
    the Streamable HTTP transport (e.g., Red Hat Lightspeed MCP server).

    Args:
        config: MCP server configuration.
        tool_filter: Optional list of tool names to expose.
        header_provider: Optional callable for dynamic header injection.
    """
    connection_params = StreamableHTTPServerParams(
        url=config.get_http_url(),
    )

    return McpToolset(
        connection_params=connection_params,
        tool_filter=tool_filter,
        header_provider=header_provider,
    )


def get_insights_tools_for_cloud_run() -> McpToolset:
    """Get MCP toolset configured for Cloud Run deployment.

    In Cloud Run, we prefer SSE transport to a separately deployed MCP server.
    Falls back to stdio for local development.

    Returns:
        Configured McpToolset instance.
    """
    config = MCPServerConfig.from_settings()

    # Check if running in Cloud Run
    if os.getenv("K_SERVICE"):
        # Use SSE transport in Cloud Run
        config.transport_mode = "sse"
    else:
        # Use stdio for local development
        config.transport_mode = "stdio"

    return create_insights_toolset(config)


# Tool categories for filtering
# Note: Tool names must match the MCP server's tool names exactly (with prefixes)
ADVISOR_TOOLS = [
    "advisor__get_active_rules",
    "advisor__get_rule_from_node_id",
    "advisor__get_rule_details",
    "advisor__get_hosts_hitting_a_rule",
    "advisor__get_hosts_details_hitting_a_rule",
    "advisor__get_rule_by_text_search",
    "advisor__get_recommendations_statistics",
]

INVENTORY_TOOLS = [
    "inventory__list_hosts",
    "inventory__get_host_details",
    "inventory__get_host_system_profile",
    "inventory__get_host_tags",
    "inventory__find_host_by_name",
]

VULNERABILITY_TOOLS = [
    "vulnerability__get_openapi",
    "vulnerability__get_cves",
    "vulnerability__get_cve",
    "vulnerability__get_cve_systems",
    "vulnerability__get_system_cves",
    "vulnerability__get_systems",
    "vulnerability__explain_cves",
]

REMEDIATION_TOOLS = [
    "remediations__create_vulnerability_playbook",
]

PLANNING_TOOLS = [
    "planning__get_upcoming_changes",
    "planning__get_appstreams_lifecycle",
    "planning__get_rhel_lifecycle",
    "planning__get_relevant_upcoming_changes",
]

IMAGE_BUILDER_TOOLS = [
    "image-builder__get_openapi",
    "image-builder__get_blueprints",
    "image-builder__get_blueprint_details",
    "image-builder__create_blueprint",
    "image-builder__update_blueprint",
    "image-builder__blueprint_compose",
    "image-builder__get_composes",
    "image-builder__get_compose_details",
    "image-builder__get_distributions",
    "image-builder__get_org_id",
]

RHSM_TOOLS = [
    "rhsm__get_activation_keys",
    "rhsm__get_activation_key",
]

RBAC_TOOLS = [
    "rbac__get_all_access",
]

CONTENT_SOURCES_TOOLS = [
    "content-sources__list_repositories",
]

# Utility tool
MCP_UTILITY_TOOLS = [
    "get_mcp_version",
]

# All available tools
ALL_INSIGHTS_TOOLS = (
    MCP_UTILITY_TOOLS
    + ADVISOR_TOOLS
    + INVENTORY_TOOLS
    + VULNERABILITY_TOOLS
    + REMEDIATION_TOOLS
    + PLANNING_TOOLS
    + IMAGE_BUILDER_TOOLS
    + RHSM_TOOLS
    + RBAC_TOOLS
    + CONTENT_SOURCES_TOOLS
)

# Read-only tools (safe for restricted access)
READ_ONLY_TOOLS = (
    MCP_UTILITY_TOOLS
    + ADVISOR_TOOLS
    + INVENTORY_TOOLS
    + VULNERABILITY_TOOLS
    + PLANNING_TOOLS
    + RHSM_TOOLS
    + RBAC_TOOLS
    + CONTENT_SOURCES_TOOLS
    + [
        "image-builder__get_openapi",
        "image-builder__get_blueprints",
        "image-builder__get_blueprint_details",
        "image-builder__get_composes",
        "image-builder__get_compose_details",
        "image-builder__get_distributions",
        "image-builder__get_org_id",
    ]
)
