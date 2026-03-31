"""Skills definitions derived from MCP server capabilities.

These skills are used in the AgentCard to describe the agent's capabilities.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Skill:
    """Represents an agent skill/capability."""

    id: str
    name: str
    description: str
    tags: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert skill to dictionary for AgentCard."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tags": self.tags,
            "examples": self.examples,
        }


# Define all skills based on MCP toolsets
ADVISOR_SKILL = Skill(
    id="rhel-advisor",
    name="RHEL Advisor",
    description=(
        "Analyze system configurations and provide recommendations. "
        "Identify potential issues before they impact your systems and "
        "provide guidance on best practices for RHEL systems."
    ),
    tags=["advisor", "recommendations", "best-practices", "configuration"],
    examples=[
        "What are the active recommendations for my systems?",
        "Show me critical advisor rules affecting my infrastructure",
        "Which systems are affected by recommendation XYZ?",
        "Search for recommendations related to security hardening",
    ],
)

INVENTORY_SKILL = Skill(
    id="system-inventory",
    name="System Inventory",
    description=(
        "Query and manage system inventory. Track registered systems and their "
        "properties, search for systems by various attributes like hostname, "
        "operating system, or tags."
    ),
    tags=["inventory", "systems", "hosts", "infrastructure"],
    examples=[
        "List all my registered RHEL systems",
        "Find systems with hostname containing 'prod'",
        "Show me the system profile for host ABC",
        "What tags are applied to my systems?",
    ],
)

VULNERABILITY_SKILL = Skill(
    id="vulnerability-analysis",
    name="Vulnerability Analysis",
    description=(
        "Analyze security vulnerabilities affecting your systems. Get CVE "
        "information, understand impact, and prioritize remediation based on risk. "
        "Identify which systems are affected by specific vulnerabilities."
    ),
    tags=["security", "vulnerability", "cve", "remediation"],
    examples=[
        "What CVEs are affecting my systems?",
        "Show me details about CVE-2024-1234",
        "Which systems are affected by this CVE?",
        "Explain the impact of these vulnerabilities on my environment",
    ],
)

REMEDIATION_SKILL = Skill(
    id="remediation-playbooks",
    name="Remediation Playbooks",
    description=(
        "Create and manage remediation playbooks for addressing vulnerabilities "
        "and configuration issues. Generate Ansible playbooks to fix identified "
        "problems across your infrastructure."
    ),
    tags=["remediation", "playbooks", "ansible", "automation"],
    examples=[
        "Create a playbook to remediate CVE-2024-1234 on my affected systems",
        "Generate a remediation plan for critical vulnerabilities",
    ],
)

PLANNING_SKILL = Skill(
    id="rhel-planning",
    name="RHEL Planning",
    description=(
        "Plan RHEL system upgrades and migrations. Get information about RHEL "
        "lifecycle dates, upcoming package changes, deprecations, and Application "
        "Streams lifecycle information."
    ),
    tags=["planning", "lifecycle", "upgrades", "migration"],
    examples=[
        "What is the lifecycle for RHEL 8?",
        "Show upcoming package changes and deprecations",
        "What Application Streams are available and their lifecycle?",
        "Are there any relevant upcoming changes for my environment?",
    ],
)

IMAGE_BUILDER_SKILL = Skill(
    id="image-builder",
    name="Image Builder",
    description=(
        "Create and manage custom RHEL images. Define image blueprints, "
        "configure image compositions, track image builds, and manage "
        "the image creation process."
    ),
    tags=["images", "blueprints", "builds", "customization"],
    examples=[
        "Show my image blueprints",
        "What distributions are available for building images?",
        "Check the status of my image builds",
        "Get details about blueprint XYZ",
    ],
)

RHSM_SKILL = Skill(
    id="subscription-management",
    name="Subscription Management",
    description=(
        "Manage Red Hat subscription information. View activation keys "
        "and subscription details for system registration."
    ),
    tags=["subscriptions", "activation-keys", "rhsm"],
    examples=[
        "List my available activation keys",
        "Get details for activation key 'production'",
    ],
)

RBAC_SKILL = Skill(
    id="access-management",
    name="Access Management",
    description=(
        "View access and permissions information for Red Hat Insights "
        "applications. Understand what actions are available based on "
        "current user roles."
    ),
    tags=["rbac", "access", "permissions"],
    examples=[
        "What access do I have across Insights applications?",
    ],
)

CONTENT_SOURCES_SKILL = Skill(
    id="content-sources",
    name="Content Sources",
    description=(
        "Manage and query content repositories. List available repositories "
        "with filtering and pagination options."
    ),
    tags=["repositories", "content", "sources"],
    examples=[
        "List available content repositories",
    ],
)

# All skills
ALL_SKILLS = [
    ADVISOR_SKILL,
    INVENTORY_SKILL,
    VULNERABILITY_SKILL,
    REMEDIATION_SKILL,
    PLANNING_SKILL,
    IMAGE_BUILDER_SKILL,
    RHSM_SKILL,
    RBAC_SKILL,
    CONTENT_SOURCES_SKILL,
]

# Read-only skills (no write operations)
READ_ONLY_SKILLS = [
    ADVISOR_SKILL,
    INVENTORY_SKILL,
    VULNERABILITY_SKILL,
    PLANNING_SKILL,
    RHSM_SKILL,
    RBAC_SKILL,
    CONTENT_SOURCES_SKILL,
]


def get_skills_for_agent_card(read_only: bool = True) -> list[dict[str, Any]]:
    """Get skills formatted for AgentCard.

    Args:
        read_only: If True, only include read-only skills.

    Returns:
        List of skill dictionaries.
    """
    # Hardcoded to read-only skills for now
    return [skill.to_dict() for skill in READ_ONLY_SKILLS]
