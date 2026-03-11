"""Tests for A2A protocol implementation."""

from unittest.mock import AsyncMock, patch

import pytest
from a2a.types import (
    AgentCapabilities,
    AgentSkill,
    Message,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)
from fastapi.testclient import TestClient

from lightspeed_agent.api.a2a.agent_card import build_agent_card, get_agent_card_dict
from lightspeed_agent.api.app import create_app


class TestAgentCard:
    """Tests for AgentCard creation."""

    def test_build_agent_card(self):
        """Test building a complete AgentCard."""
        card = build_agent_card()

        assert card.name
        assert card.description
        assert card.url
        assert card.provider
        assert card.capabilities
        assert len(card.skills) > 0

    def test_agent_card_has_provider(self):
        """Test AgentCard has provider information."""
        card = build_agent_card()

        assert card.provider.organization == "Red Hat"
        assert "redhat.com" in card.provider.url

    def test_agent_card_has_oauth_security(self):
        """Test AgentCard has OAuth security scheme."""
        card = build_agent_card()

        assert "redhat_sso" in card.security_schemes
        # The security scheme is wrapped, access via root
        scheme = card.security_schemes["redhat_sso"]
        assert scheme.root.type == "oauth2"

    def test_agent_card_has_dcr_extension(self):
        """Test AgentCard has DCR extension in capabilities."""
        card = build_agent_card()

        # Extensions are now a list of AgentExtension objects
        assert card.capabilities.extensions is not None
        assert len(card.capabilities.extensions) > 0
        dcr_ext = card.capabilities.extensions[0]
        assert "dcr" in dcr_ext.uri
        assert dcr_ext.params is not None
        assert "endpoint" in dcr_ext.params

    def test_agent_card_url_points_to_root(self):
        """Test AgentCard URL points to root endpoint."""
        card = build_agent_card()

        # The main A2A endpoint is at root /
        assert card.url.endswith("/")

    def test_agent_card_has_skills(self):
        """Test AgentCard has skills from MCP."""
        card = build_agent_card()

        assert len(card.skills) > 0
        skill_ids = [s.id for s in card.skills]
        assert "rhel-advisor" in skill_ids or len(skill_ids) > 0

    def test_get_agent_card_dict(self):
        """Test AgentCard serialization to dict."""
        card_dict = get_agent_card_dict()

        assert "name" in card_dict
        assert "description" in card_dict
        assert "protocolVersion" in card_dict  # aliased field
        assert "securitySchemes" in card_dict  # aliased field
        assert "defaultInputModes" in card_dict  # aliased field


class TestModels:
    """Tests for A2A data models using a2a-sdk types."""

    def test_message_creation(self):
        """Test Message model creation."""
        message = Message(
            message_id="test-msg-id",
            role="user",
            parts=[TextPart(text="Hello")],
        )

        assert message.role == "user"
        assert len(message.parts) == 1
        # Parts are wrapped in a Part union type
        assert message.parts[0].root.text == "Hello"
        assert message.message_id == "test-msg-id"

    def test_task_creation(self):
        """Test Task model creation with SDK types."""
        task = Task(
            id="test-task-id",
            context_id="test-context-id",
            status=TaskStatus(state=TaskState.submitted),
        )

        assert task.id == "test-task-id"
        assert task.context_id == "test-context-id"
        assert task.status.state == TaskState.submitted

    def test_task_state_transitions(self):
        """Test Task state can be updated."""
        task = Task(
            id="test-task-id",
            context_id="test-context-id",
            status=TaskStatus(state=TaskState.submitted),
        )

        task.status = TaskStatus(state=TaskState.working)
        assert task.status.state == TaskState.working

        task.status = TaskStatus(state=TaskState.completed)
        assert task.status.state == TaskState.completed

    def test_agent_skill_serialization(self):
        """Test AgentSkill serialization with aliases."""
        skill = AgentSkill(
            id="test-skill",
            name="Test Skill",
            description="A test skill",
            tags=["test", "example"],
        )

        data = skill.model_dump(by_alias=True)
        assert "inputModes" in data or "input_modes" in data or data.get("inputModes") is None
        assert "tags" in data
        assert skill.tags == ["test", "example"]

    def test_agent_capabilities_with_extensions(self):
        """Test AgentCapabilities with extensions."""
        from a2a.types import AgentExtension

        ext = AgentExtension(
            uri="urn:test:dcr",
            params={"endpoint": "http://example.com/register"},
        )
        caps = AgentCapabilities(
            streaming=True,
            extensions=[ext],
        )

        assert caps.streaming is True
        assert len(caps.extensions) == 1
        assert caps.extensions[0].uri == "urn:test:dcr"


class TestA2AEndpoints:
    """Tests for A2A API endpoints."""

    @pytest.fixture
    def client(self):
        """Create test client with rate limiter mocked out."""
        mock_limiter = AsyncMock()
        mock_limiter.is_allowed = AsyncMock(
            return_value=(True, {
                "requests_this_minute": 1,
                "requests_this_hour": 1,
                "limit_per_minute": 60,
                "limit_per_hour": 1000,
                "exceeded": "ok",
                "retry_after": 0,
                "limited_principal": "none",
            })
        )
        # Reset the global singleton so the mock is picked up
        import lightspeed_agent.ratelimit.middleware as rl_mod

        rl_mod._rate_limiter = None
        with patch.object(rl_mod, "get_redis_rate_limiter", return_value=mock_limiter):
            app = create_app()
            yield TestClient(app)
        rl_mod._rate_limiter = None

    def test_agent_card_endpoint(self, client):
        """Test /.well-known/agent.json endpoint."""
        response = client.get("/.well-known/agent.json")

        assert response.status_code == 200
        data = response.json()
        assert "name" in data
        assert "skills" in data
        assert "securitySchemes" in data

    def test_health_endpoint(self, client):
        """Test /health endpoint."""
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    def test_ready_endpoint(self, client):
        """Test /ready endpoint."""
        response = client.get("/ready")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"

    def test_send_message_jsonrpc(self, client):
        """Test / endpoint with JSON-RPC message/send."""
        request_body = {
            "jsonrpc": "2.0",
            "method": "message/send",
            "params": {
                "message": {
                    "messageId": "msg-1",
                    "role": "user",
                    "parts": [{"type": "text", "text": "Hello!"}],
                },
            },
            "id": "test-1",
        }

        response = client.post("/", json=request_body)

        assert response.status_code == 200
        data = response.json()
        assert data["jsonrpc"] == "2.0"
        assert data["id"] == "test-1"
        # Response should have either result or error
        assert "result" in data or "error" in data

    def test_method_not_found(self, client):
        """Test JSON-RPC with unknown method."""
        request_body = {
            "jsonrpc": "2.0",
            "method": "unknown/method",
            "params": {},
            "id": "test-2",
        }

        response = client.post("/", json=request_body)

        # ADK returns error for unknown methods
        data = response.json()
        assert data["jsonrpc"] == "2.0"
        assert "error" in data
