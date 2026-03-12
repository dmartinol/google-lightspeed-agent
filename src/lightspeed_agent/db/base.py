"""Database engine and session configuration."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from lightspeed_agent.config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all models."""

    pass


# Global engine and session factory
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Get the database engine, creating it if necessary.

    Returns:
        AsyncEngine instance.
    """
    global _engine
    if _engine is None:
        settings = get_settings()
        engine_kwargs: dict[str, Any] = {"echo": settings.debug}
        if settings.database_url.startswith("sqlite"):
            from sqlalchemy.pool import StaticPool

            engine_kwargs["poolclass"] = StaticPool
            engine_kwargs["connect_args"] = {"check_same_thread": False}
        else:
            engine_kwargs["pool_size"] = settings.database_pool_size
            engine_kwargs["max_overflow"] = settings.database_pool_max_overflow
        _engine = create_async_engine(settings.database_url, **engine_kwargs)
        logger.info("Created database engine for %s", settings.database_url.split("@")[-1])
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get the session factory, creating it if necessary.

    Returns:
        async_sessionmaker instance.
    """
    global _session_factory
    if _session_factory is None:
        engine = get_engine()
        _session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get a database session as a context manager.

    Yields:
        AsyncSession instance.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_database(max_retries: int = 30, retry_delay: float = 2.0) -> None:
    """Initialize the database, creating all tables.

    This should be called on application startup. Includes retry logic to wait
    for the database to be ready (e.g., when PostgreSQL is starting up in the same pod).

    Args:
        max_retries: Maximum number of connection attempts (default: 30).
        retry_delay: Delay in seconds between retries (default: 2.0).
    """
    engine = get_engine()

    # Import models to register them with Base
    from lightspeed_agent.db import models  # noqa: F401

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("Database tables created/verified")
            return
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                logger.warning(
                    "Database connection attempt %d/%d failed: %s. Retrying in %.1fs...",
                    attempt,
                    max_retries,
                    str(e),
                    retry_delay,
                )
                await asyncio.sleep(retry_delay)
            else:
                logger.error(
                    "Database connection failed after %d attempts: %s",
                    max_retries,
                    str(e),
                )

    # If we get here, all retries failed
    raise RuntimeError(
        f"Failed to connect to database after {max_retries} attempts"
    ) from last_error


async def close_database() -> None:
    """Close the database connection.

    This should be called on application shutdown.
    """
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("Database connection closed")
