"""Tests for MCP tools integration."""

import os
from unittest.mock import patch

from lightspeed_agent.tools.insights_tools import (
    ADVISOR_TOOLS,
    ALL_INSIGHTS_TOOLS,
    INVENTORY_TOOLS,
    READ_ONLY_TOOLS,
    VULNERABILITY_TOOLS,
)
from lightspeed_agent.tools.mcp_config import MCPServerConfig
from lightspeed_agent.tools.skills import (
    ALL_SKILLS,
    READ_ONLY_SKILLS,
    Skill,
    get_skills_for_agent_card,
)


class TestMCPServerConfig:
    """Tests for MCPServerConfig."""

    def test_create_from_settings(self):
        """Test creating config from settings."""
        with patch.dict(os.environ, {
            "MCP_TRANSPORT_MODE": "stdio",
            "MCP_READ_ONLY": "true",
        }):
            # Clear cached settings
            from lightspeed_agent.config.settings import get_settings
            get_settings.cache_clear()

            config = MCPServerConfig.from_settings()

            assert config.transport_mode == "stdio"
            assert config.read_only is True

    def test_stdio_command(self):
        """Test stdio command generation."""
        config = MCPServerConfig(
            transport_mode="stdio",
        )

        assert config.get_stdio_command() == "podman"

    def test_stdio_args(self):
        """Test stdio args generation."""
        config = MCPServerConfig(
            transport_mode="stdio",
            read_only=True,
        )

        args = config.get_stdio_args()

        assert "run" in args
        assert "--interactive" in args
        assert "--rm" in args
        assert "--read-only" in args
        assert config.container_image in args

    def test_stdio_args_no_readonly(self):
        """Test stdio args without read-only flag."""
        config = MCPServerConfig(
            transport_mode="stdio",
            read_only=False,
        )

        args = config.get_stdio_args()

        assert "--read-only" not in args

    def test_http_url(self):
        """Test HTTP URL generation."""
        config = MCPServerConfig(
            transport_mode="http",
            server_url="http://localhost:8080",
        )

        assert config.get_http_url() == "http://localhost:8080/mcp"


class TestSkills:
    """Tests for skills definitions."""

    def test_skill_to_dict(self):
        """Test skill serialization to dict."""
        skill = Skill(
            id="test-skill",
            name="Test Skill",
            description="A test skill",
            tags=["test", "example"],
            examples=["Example 1", "Example 2"],
        )

        result = skill.to_dict()

        assert result["id"] == "test-skill"
        assert result["name"] == "Test Skill"
        assert result["description"] == "A test skill"
        assert result["tags"] == ["test", "example"]
        assert result["examples"] == ["Example 1", "Example 2"]

    def test_all_skills_have_required_fields(self):
        """Test all skills have required fields."""
        for skill in ALL_SKILLS:
            assert skill.id, f"Skill {skill.name} missing id"
            assert skill.name, f"Skill {skill.id} missing name"
            assert skill.description, f"Skill {skill.id} missing description"

    def test_read_only_skills_subset(self):
        """Test read-only skills are subset of all skills."""
        read_only_ids = {s.id for s in READ_ONLY_SKILLS}
        all_ids = {s.id for s in ALL_SKILLS}

        assert read_only_ids.issubset(all_ids)

    def test_get_skills_for_agent_card_returns_read_only(self):
        """Test getting skills for agent card returns only read-only skills."""
        skills = get_skills_for_agent_card()

        assert len(skills) == len(READ_ONLY_SKILLS)
        skill_ids = {s["id"] for s in skills}
        read_only_ids = {s.id for s in READ_ONLY_SKILLS}
        assert skill_ids == read_only_ids
        for skill in skills:
            assert "id" in skill
            assert "name" in skill
            assert "description" in skill


class TestToolLists:
    """Tests for tool category lists."""

    def test_advisor_tools_not_empty(self):
        """Test advisor tools list is not empty."""
        assert len(ADVISOR_TOOLS) > 0

    def test_inventory_tools_not_empty(self):
        """Test inventory tools list is not empty."""
        assert len(INVENTORY_TOOLS) > 0

    def test_vulnerability_tools_not_empty(self):
        """Test vulnerability tools list is not empty."""
        assert len(VULNERABILITY_TOOLS) > 0

    def test_all_tools_contains_categories(self):
        """Test all tools list contains category tools."""
        for tool in ADVISOR_TOOLS:
            assert tool in ALL_INSIGHTS_TOOLS
        for tool in INVENTORY_TOOLS:
            assert tool in ALL_INSIGHTS_TOOLS
        for tool in VULNERABILITY_TOOLS:
            assert tool in ALL_INSIGHTS_TOOLS

    def test_read_only_tools_subset(self):
        """Test read-only tools are subset of all tools."""
        for tool in READ_ONLY_TOOLS:
            assert tool in ALL_INSIGHTS_TOOLS

    def test_no_duplicate_tools(self):
        """Test no duplicate tools in ALL_INSIGHTS_TOOLS."""
        assert len(ALL_INSIGHTS_TOOLS) == len(set(ALL_INSIGHTS_TOOLS))
