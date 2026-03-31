"""Core agent definition using Google ADK with Gemini 2.5 Flash."""

import logging
import os
from typing import Any

from google.adk.agents import LlmAgent

from lightspeed_agent.config import get_settings

logger = logging.getLogger(__name__)

# Agent instruction describing its capabilities
AGENT_INSTRUCTION = """You are the Red Hat Lightspeed Agent for Google Cloud, \
an AI assistant specialized in helping users manage their Red Hat infrastructure. \
You have access to the following
Red Hat Insights capabilities:

## Advisor
- Analyze system configurations and provide recommendations
- Identify potential issues before they impact your systems
- Provide guidance on best practices

## Inventory
- Query and manage system inventory
- Track registered systems and their properties
- Search for systems by various attributes

## Vulnerability
- Analyze security vulnerabilities affecting your systems
- Provide CVE information and remediation guidance
- Prioritize vulnerabilities based on risk

## Planning
- Help plan RHEL system upgrades and migrations
- Provide roadmap recommendations
- Assess upgrade readiness

## Subscription Management
- View activation keys for system registration
- Access subscription information

## Access Management
- View access and permissions information for Red Hat Insights applications
- Understand what actions are available based on current user roles

## Content Sources
- List available content repositories
- Query repository information

## First Response Notice
When you first interact with a user in a new conversation, begin your response with \
the following notice (verbatim), followed by the accuracy disclaimer:

"You are interacting with the Red Hat Lightspeed Agent, which can answer questions \
about your Red Hat account, subscription, system configuration, and related details. \
This feature uses AI technology. Interactions may be used to improve Red Hat's \
products or services.

Always review AI-generated content prior to use."

After the first response in a conversation, do not repeat this notice.

When responding to users:
1. Always be helpful and provide clear, actionable information
2. If you need more context, ask clarifying questions
3. Provide security-conscious recommendations
4. When displaying lists of systems or vulnerabilities, format them clearly
5. For CVEs, always include severity information when available
6. When users ask what tools or capabilities you have, describe them based on the \
capability areas listed above (Advisor, Inventory, Vulnerability, \
Planning, Subscription Management, Access Management, Content Sources). Do NOT attempt \
to call a "list_tools" function — it does not exist. Instead, provide a helpful \
summary of your capabilities and example queries for each area
"""


def _setup_environment() -> None:
    """Set up environment variables for Google ADK."""
    settings = get_settings()

    # Configure Vertex AI or Google AI Studio
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = str(settings.google_genai_use_vertexai).upper()

    if settings.google_genai_use_vertexai:
        if settings.google_cloud_project:
            os.environ["GOOGLE_CLOUD_PROJECT"] = settings.google_cloud_project
        os.environ["GOOGLE_CLOUD_LOCATION"] = settings.google_cloud_location
    elif settings.google_api_key:
        os.environ["GOOGLE_API_KEY"] = settings.google_api_key


def create_agent() -> LlmAgent:
    """Create the Lightspeed Agent with MCP tools.

    This function creates an LlmAgent with the Red Hat Lightspeed MCP toolset.
    The caller's JWT token is forwarded to the MCP server via a header_provider
    so the MCP server can authenticate on behalf of the calling user.

    Returns:
        Configured LlmAgent instance.
    """
    _setup_environment()
    settings = get_settings()

    tools: list[Any] = []

    try:
        from lightspeed_agent.tools import READ_ONLY_TOOLS, create_insights_toolset

        logger.info(
            f"Creating MCP toolset with transport={settings.mcp_transport_mode}, "
            f"url={settings.mcp_server_url}"
        )
        tool_filter = READ_ONLY_TOOLS if settings.mcp_read_only else None
        mcp_toolset = create_insights_toolset(
            tool_filter=tool_filter,
        )
        tools = [mcp_toolset]
        logger.info(
            f"Created agent with MCP tools (read_only={settings.mcp_read_only}, "
            f"model={settings.gemini_model})"
        )
    except Exception as e:
        logger.warning(f"Failed to create MCP toolset: {e}", exc_info=True)
        logger.info("Agent created without MCP tools")

    return LlmAgent(
        name=settings.agent_name,
        model=settings.gemini_model,
        description=settings.agent_description,
        instruction=AGENT_INSTRUCTION,
        tools=tools,
    )


# Root agent instance for ADK CLI compatibility
root_agent = create_agent()
