"""Retrying wrapper for ADK's DatabaseSessionService.

ADK's ``DatabaseSessionService`` uses optimistic concurrency on the session's
``update_time`` column.  When two concurrent A2A requests share the same
``context_id`` (and therefore the same ``session_id``), one request may update
the database timestamp before the other can append its event, raising a
``ValueError`` about a stale session.

``RetryingDatabaseSessionService`` catches that specific error, reloads the
session's ``last_update_time`` from the database, and retries the append.
"""

import logging
from typing import Any

from google.adk.events.event import Event
from google.adk.sessions import DatabaseSessionService
from google.adk.sessions.session import Session

logger = logging.getLogger(__name__)

_STALE_SESSION_KEYWORDS = ("stale", "modified in storage")
_DEFAULT_MAX_RETRIES = 3


def _is_stale_session_error(error: ValueError) -> bool:
    """Return True if the ValueError is a stale-session optimistic lock failure."""
    msg = str(error).lower()
    return any(kw in msg for kw in _STALE_SESSION_KEYWORDS)


class RetryingDatabaseSessionService(DatabaseSessionService):  # type: ignore[misc]
    """DatabaseSessionService that retries on stale-session errors.

    All methods except ``append_event`` are inherited unchanged.
    """

    def __init__(self, *, db_url: str, max_retries: int = _DEFAULT_MAX_RETRIES, **kwargs: Any):
        super().__init__(db_url=db_url, **kwargs)
        self._max_retries = max_retries

    async def append_event(self, session: Session, event: Event) -> Event:
        """Append an event, retrying if the session timestamp is stale."""
        last_error: ValueError | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                return await super().append_event(session, event)  # type: ignore[no-any-return]
            except ValueError as exc:
                if not _is_stale_session_error(exc):
                    raise

                last_error = exc
                logger.warning(
                    "Stale session detected (attempt %d/%d), "
                    "refreshing timestamp and retrying",
                    attempt,
                    self._max_retries,
                )

                reloaded = await self.get_session(
                    app_name=session.app_name,
                    user_id=session.user_id,
                    session_id=session.id,
                )
                if reloaded:
                    session.last_update_time = reloaded.last_update_time
                else:
                    logger.warning(
                        "Session not found during reload (attempt %d/%d), "
                        "cannot retry",
                        attempt,
                        self._max_retries,
                    )
                    break

        # All retries exhausted — raise the last stale-session error.
        raise last_error  # type: ignore[misc]
