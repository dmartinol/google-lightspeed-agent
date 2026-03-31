"""Tests for RetryingDatabaseSessionService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lightspeed_agent.api.a2a.session_service import (
    RetryingDatabaseSessionService,
    _is_stale_session_error,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(last_update_time: float = 100.0) -> MagicMock:
    session = MagicMock()
    session.id = "sess-1"
    session.app_name = "test-app"
    session.user_id = "user-1"
    session.last_update_time = last_update_time
    return session


def _make_event(partial: bool = False) -> MagicMock:
    event = MagicMock()
    event.partial = partial
    return event


# ---------------------------------------------------------------------------
# _is_stale_session_error
# ---------------------------------------------------------------------------

class TestIsStaleSessionError:
    def test_detects_stale_keyword(self):
        assert _is_stale_session_error(ValueError("Please check if it is a stale session."))

    def test_detects_modified_in_storage(self):
        assert _is_stale_session_error(
            ValueError("The session has been modified in storage since it was loaded.")
        )

    def test_rejects_unrelated_value_error(self):
        assert not _is_stale_session_error(ValueError("Some other error"))


# ---------------------------------------------------------------------------
# RetryingDatabaseSessionService.append_event
# ---------------------------------------------------------------------------

class TestRetryingAppendEvent:
    """Tests for the retry logic in append_event."""

    @pytest.fixture
    def service(self):
        """Create a RetryingDatabaseSessionService with mocked parent init."""
        with patch.object(RetryingDatabaseSessionService, "__init__", lambda self, **kw: None):
            svc = object.__new__(RetryingDatabaseSessionService)
            svc._max_retries = 3
            return svc

    async def test_succeeds_without_retry(self, service):
        """First call succeeds — no retry, no get_session call."""
        session = _make_session()
        event = _make_event()
        expected = MagicMock()

        with (
            patch(
                "google.adk.sessions.DatabaseSessionService.append_event",
                new_callable=AsyncMock,
                return_value=expected,
            ) as mock_append,
            patch.object(
                service, "get_session", new_callable=AsyncMock
            ) as mock_get,
        ):
            result = await service.append_event(session, event)

        assert result is expected
        mock_append.assert_awaited_once_with(session, event)
        mock_get.assert_not_awaited()

    async def test_retries_on_stale_error_then_succeeds(self, service):
        """Stale error on first attempt, succeeds on second after refresh."""
        session = _make_session(last_update_time=100.0)
        event = _make_event()
        expected = MagicMock()

        reloaded_session = _make_session(last_update_time=200.0)

        with (
            patch(
                "google.adk.sessions.DatabaseSessionService.append_event",
                new_callable=AsyncMock,
                side_effect=[ValueError("stale session detected"), expected],
            ) as mock_append,
            patch.object(
                service,
                "get_session",
                new_callable=AsyncMock,
                return_value=reloaded_session,
            ) as mock_get,
        ):
            result = await service.append_event(session, event)

        assert result is expected
        assert mock_append.await_count == 2
        mock_get.assert_awaited_once_with(
            app_name="test-app", user_id="user-1", session_id="sess-1"
        )
        assert session.last_update_time == 200.0

    async def test_non_stale_value_error_propagates_immediately(self, service):
        """ValueError without stale keywords raises immediately, no retry."""
        session = _make_session()
        event = _make_event()

        with (
            patch(
                "google.adk.sessions.DatabaseSessionService.append_event",
                new_callable=AsyncMock,
                side_effect=ValueError("Something else went wrong"),
            ) as mock_append,
            patch.object(
                service, "get_session", new_callable=AsyncMock
            ) as mock_get,
            pytest.raises(ValueError, match="Something else went wrong"),
        ):
            await service.append_event(session, event)

        mock_append.assert_awaited_once()
        mock_get.assert_not_awaited()

    async def test_retries_exhausted_raises(self, service):
        """All retry attempts fail — the last stale error is raised."""
        session = _make_session()
        event = _make_event()

        reloaded = _make_session(last_update_time=300.0)

        with (
            patch(
                "google.adk.sessions.DatabaseSessionService.append_event",
                new_callable=AsyncMock,
                side_effect=ValueError("stale session"),
            ) as mock_append,
            patch.object(
                service,
                "get_session",
                new_callable=AsyncMock,
                return_value=reloaded,
            ) as mock_get,
            pytest.raises(ValueError, match="stale session"),
        ):
            await service.append_event(session, event)

        # 3 retries = 3 append attempts + 3 get_session refreshes
        assert mock_append.await_count == 3
        assert mock_get.await_count == 3

    async def test_get_session_returns_none_breaks_loop(self, service):
        """If get_session returns None (session deleted), stop retrying and raise."""
        session = _make_session(last_update_time=100.0)
        event = _make_event()

        with (
            patch(
                "google.adk.sessions.DatabaseSessionService.append_event",
                new_callable=AsyncMock,
                side_effect=ValueError("stale session"),
            ) as mock_append,
            patch.object(
                service,
                "get_session",
                new_callable=AsyncMock,
                return_value=None,
            ) as mock_get,
            pytest.raises(ValueError, match="stale session"),
        ):
            await service.append_event(session, event)

        # Only one attempt — loop breaks after get_session returns None
        mock_append.assert_awaited_once()
        mock_get.assert_awaited_once()
        # last_update_time unchanged because reloaded was None
        assert session.last_update_time == 100.0

    async def test_modified_in_storage_message_triggers_retry(self, service):
        """The newer ADK error message variant also triggers retry."""
        session = _make_session()
        event = _make_event()
        expected = MagicMock()

        reloaded = _make_session(last_update_time=200.0)

        with (
            patch(
                "google.adk.sessions.DatabaseSessionService.append_event",
                new_callable=AsyncMock,
                side_effect=[
                    ValueError(
                        "The session has been modified in storage since it was loaded."
                    ),
                    expected,
                ],
            ),
            patch.object(
                service,
                "get_session",
                new_callable=AsyncMock,
                return_value=reloaded,
            ),
        ):
            result = await service.append_event(session, event)

        assert result is expected
