"""Marketplace Handler FastAPI Application.

This is a separate service from the Agent that handles:
1. Pub/Sub events from Google Cloud Marketplace (async provisioning)
2. DCR requests from Gemini Enterprise (sync client registration)

The service exposes a single /dcr endpoint that handles both flows
using smart routing based on request content.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from lightspeed_agent.config import get_settings
from lightspeed_agent.marketplace.router import router as handler_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager for startup/shutdown events."""
    settings = get_settings()

    # Startup: Initialize database
    try:
        from lightspeed_agent.db import init_database

        logger.info("Initializing database: %s", settings.database_url.split("@")[-1])
        await init_database()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error("Failed to initialize database: %s", e)
        raise

    yield

    # Shutdown: Close database connection
    try:
        from lightspeed_agent.db import close_database

        logger.info("Closing database connection")
        await close_database()
    except Exception as e:
        logger.error("Failed to close database: %s", e)


def create_app() -> FastAPI:
    """Create and configure the Marketplace Handler FastAPI application.

    Returns:
        Configured FastAPI application instance.
    """
    settings = get_settings()

    app = FastAPI(
        title="Marketplace Handler",
        description="Handles Google Cloud Marketplace provisioning and DCR",
        version="0.1.0",
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        lifespan=lifespan,
    )

    # Health check endpoint
    @app.get("/health")
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "healthy", "service": "marketplace-handler"}

    # Ready check endpoint
    @app.get("/ready")
    async def ready_check() -> dict[str, str]:
        """Readiness check endpoint."""
        return {"status": "ready", "service": "marketplace-handler"}

    # Include the main handler router
    # This provides the /dcr endpoint that handles both Pub/Sub and DCR
    app.include_router(handler_router)

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    return app
