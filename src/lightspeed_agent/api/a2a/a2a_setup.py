"""A2A protocol setup using ADK's built-in A2A integration.

This module sets up the A2A protocol endpoints using the google-adk
and a2a-sdk libraries, which handle all the complexity of SSE streaming,
task management, and event conversion automatically.
"""

import logging
from typing import Any

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
from lightspeed_agent.api.a2a.usage_plugin import UsageTrackingPlugin
from lightspeed_agent.config import get_settings
from lightspeed_agent.core import create_agent

logger = logging.getLogger(__name__)


def _get_session_service() -> Any:
    """Get the appropriate session service based on configuration.

    For production, uses DatabaseSessionService which persists sessions to PostgreSQL.
    For development, uses InMemorySessionService.

    Security Note:
        SESSION_DATABASE_URL must be explicitly set to use database persistence.
        This prevents accidental use of the marketplace database (DATABASE_URL)
        for session storage, ensuring:
        - Agents only have access to session data, not marketplace/auth data
        - Compromised agents can't access DCR credentials or order information
        - Different retention policies can be applied to sessions vs. marketplace data

    Returns:
        Session service instance (DatabaseSessionService or InMemorySessionService).
    """
    settings = get_settings()

    # Only use database session service if SESSION_DATABASE_URL is explicitly set
    # Do NOT fall back to DATABASE_URL to avoid mixing session and marketplace data
    session_db_url = settings.session_database_url

    # Use database session service for production (non-SQLite databases)
    if session_db_url and not session_db_url.startswith("sqlite"):
        try:
            from google.adk.sessions import DatabaseSessionService

            # ADK's DatabaseSessionService uses synchronous SQLAlchemy,
            # so we need to convert the async URL to sync format
            db_url = session_db_url
            if "postgresql+asyncpg" in db_url:
                # Convert asyncpg URL to sync psycopg2 format
                db_url = db_url.replace("postgresql+asyncpg", "postgresql+psycopg2")
            elif "postgresql+aiopg" in db_url:
                db_url = db_url.replace("postgresql+aiopg", "postgresql+psycopg2")

            # Log which database is being used (without credentials)
            db_host = db_url.split("@")[-1].split("/")[0] if "@" in db_url else "local"
            logger.info(
                "Using DatabaseSessionService for session persistence (host=%s)",
                db_host,
            )
            return DatabaseSessionService(db_url=db_url)
        except ImportError as e:
            logger.warning(
                "DatabaseSessionService not available (%s), falling back to InMemorySessionService",
                e,
            )
        except Exception as e:
            logger.warning(
                "Failed to initialize DatabaseSessionService (%s), "
                "falling back to InMemorySessionService",
                e,
            )

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
        plugins=[UsageTrackingPlugin()],
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
