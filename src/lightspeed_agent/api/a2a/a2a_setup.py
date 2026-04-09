"""A2A protocol setup using ADK's built-in A2A integration.

This module sets up the A2A protocol endpoints using the google-adk
and a2a-sdk libraries, which handle all the complexity of SSE streaming,
task management, and event conversion automatically.
"""

import logging
import re
from typing import Any
from urllib.parse import urlparse

from a2a.server.apps import A2AFastAPIApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from fastapi import FastAPI
from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
from google.adk.apps import App
from google.adk.artifacts import InMemoryArtifactService
from google.adk.memory import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

from lightspeed_agent.api.a2a.agent_card import build_agent_card
from lightspeed_agent.api.a2a.logging_plugin import AgentLoggingPlugin
from lightspeed_agent.api.a2a.usage_plugin import UsageTrackingPlugin
from lightspeed_agent.config import get_settings
from lightspeed_agent.core import create_agent

logger = logging.getLogger(__name__)


def _normalize_db_url(url: str) -> str:
    """Ensure a database URL uses an async driver for SQLAlchemy.

    ADK's DatabaseSessionService requires ``create_async_engine``, so any
    synchronous PostgreSQL scheme must be replaced with ``postgresql+asyncpg``.
    """
    scheme, remainder = url.split("://", 1)
    normalized_scheme = scheme.lower()

    sync_postgres_schemes = {
        "postgres",
        "postgresql",
        "postgresql+psycopg",
        "postgresql+psycopg2",
    }
    if normalized_scheme in sync_postgres_schemes:
        return f"postgresql+asyncpg://{remainder}"

    return url


def _get_session_service() -> Any:
    """Get the appropriate session service based on SESSION_BACKEND setting.

    Uses SESSION_BACKEND to determine the session storage:
    - ``"memory"``: InMemorySessionService (default, no persistence)
    - ``"database"``: DatabaseSessionService (requires SESSION_DATABASE_URL)

    When SESSION_BACKEND is ``"database"``, failures are raised immediately
    rather than silently falling back to in-memory, so misconfigurations are
    caught at startup.

    Security Note:
        SESSION_DATABASE_URL should point to a separate database from
        DATABASE_URL to ensure agents only access session data, not
        marketplace/auth data.

    Returns:
        Session service instance (DatabaseSessionService or InMemorySessionService).
    """
    settings = get_settings()

    if settings.session_backend == "database":
        from lightspeed_agent.api.a2a.session_service import (
            RetryingDatabaseSessionService,
        )

        # SESSION_DATABASE_URL is guaranteed non-empty by the model validator
        db_url = _normalize_db_url(settings.session_database_url)

        # Log which database is being used (without credentials)
        parsed = urlparse(db_url)
        db_host = parsed.hostname or parsed.query or "local"
        logger.info(
            "Using RetryingDatabaseSessionService for session persistence (host=%s)",
            db_host,
        )

        try:
            return RetryingDatabaseSessionService(db_url=db_url)
        except Exception as e:
            # Sanitize error message to avoid leaking credentials from URLs
            sanitized_msg = re.sub(r"://[^@]+@", "://***@", str(e))
            raise RuntimeError(
                f"Failed to initialize RetryingDatabaseSessionService: {sanitized_msg}"
            ) from None

    logger.info("Using InMemorySessionService for session management")
    return InMemorySessionService()  # type: ignore[no-untyped-call]


def _create_runner() -> Runner:
    """Create a Runner for the ADK agent with usage tracking.

    Returns:
        Configured Runner instance with appropriate services and usage plugin.

    Note:
        Uses DatabaseSessionService for production (PostgreSQL) to persist
        sessions across agent restarts and enable horizontal scaling.
        Falls back to InMemorySessionService for development.
    """
    settings = get_settings()
    agent = create_agent()

    # Create App with usage tracking plugin
    app = App(
        name=settings.agent_name,
        root_agent=agent,
        plugins=[AgentLoggingPlugin(), UsageTrackingPlugin()],
    )

    # Use database-backed session service for production
    session_service = _get_session_service()

    return Runner(
        app=app,
        artifact_service=InMemoryArtifactService(),
        session_service=session_service,
        memory_service=InMemoryMemoryService(),  # type: ignore[no-untyped-call]
    )


def setup_a2a_routes(app: FastAPI) -> None:
    """Set up A2A protocol routes on the FastAPI application.

    This function configures the A2A endpoints using the official
    ADK and a2a-sdk integration, which handles:
    - JSON-RPC message handling
    - SSE streaming with proper event formatting
    - Task state management
    - Event conversion between ADK and A2A formats

    Args:
        app: The FastAPI application to add routes to.
    """
    settings = get_settings()

    # Create A2A components
    task_store = InMemoryTaskStore()

    # A2aAgentExecutor accepts a Runner or a callable that returns one
    # Using a callable allows lazy initialization
    agent_executor = A2aAgentExecutor(runner=_create_runner)

    request_handler = DefaultRequestHandler(
        agent_executor=agent_executor,
        task_store=task_store,
    )

    # Build our custom AgentCard with OAuth security schemes
    agent_card = build_agent_card()

    # Create the A2A application
    a2a_app = A2AFastAPIApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )

    # Add A2A routes to the FastAPI app
    # - POST at rpc_url for JSON-RPC requests (message/send, message/stream, etc.)
    # - GET at agent_card_url for the AgentCard
    a2a_app.add_routes_to_app(
        app,
        agent_card_url="/.well-known/agent.json",
        rpc_url="/",  # Root URL for A2A Inspector compatibility
    )

    logger.info(
        f"A2A routes configured: AgentCard at /.well-known/agent.json, "
        f"JSON-RPC at /, agent_url={settings.agent_provider_url}"
    )
