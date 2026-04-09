"""Retrying wrapper for ADK's DatabaseSessionService.

ADK's ``DatabaseSessionService`` uses optimistic concurrency control to
detect when a session has been modified between load and append.  Since
ADK 1.28 this is enforced via an exact ``_storage_update_marker`` (an ISO
timestamp string stored as a Pydantic ``PrivateAttr`` on ``Session``).
When another writer commits first, the marker comparison fails and ADK
raises ``ValueError("The session has been modified in storage …")``.

``RetryingDatabaseSessionService`` catches that error, reloads **both**
the marker and the float ``last_update_time`` from the database, and
retries the append.
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


def _sync_session_from_reloaded(session: Session, reloaded: Session) -> None:
    """Copy concurrency-control fields from a freshly loaded session.

    Updates ``last_update_time`` (float timestamp used by older ADK
    versions) **and** ``_storage_update_marker`` (exact ISO revision
    marker used since ADK 1.28) as well as the in-memory ``events``
    and ``state`` so the next ``append_event`` call sees a consistent
    snapshot.
    """
    session.last_update_time = reloaded.last_update_time
    session.events = reloaded.events
    session.state = reloaded.state

    # _storage_update_marker is a Pydantic PrivateAttr — copy it only
    # when the attribute exists (ADK ≥ 1.28).
    marker = getattr(reloaded, "_storage_update_marker", None)
    if marker is not None:
        session._storage_update_marker = marker


class RetryingDatabaseSessionService(DatabaseSessionService):  # type: ignore[misc]
    """DatabaseSessionService that retries on stale-session errors.

    All methods except ``append_event`` are inherited unchanged.
    """

    def __init__(self, *, db_url: str, max_retries: int = _DEFAULT_MAX_RETRIES, **kwargs: Any):
        super().__init__(db_url=db_url, **kwargs)
        self._max_retries = max_retries

    async def append_event(self, session: Session, event: Event) -> Event:
        """Append an event, retrying if the session revision is stale."""
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
                    "reloading session and retrying",
                    attempt,
                    self._max_retries,
                )

                reloaded = await self.get_session(
                    app_name=session.app_name,
                    user_id=session.user_id,
                    session_id=session.id,
                )
                if reloaded:
                    _sync_session_from_reloaded(session, reloaded)
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
