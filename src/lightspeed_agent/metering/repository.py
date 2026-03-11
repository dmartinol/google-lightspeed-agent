"""Persistence helpers for order usage metering."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from lightspeed_agent.db import UsageRecordModel, get_session

logger = logging.getLogger(__name__)


def _current_hour_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Return current UTC hour window [start, end)."""
    current = now or datetime.now(UTC)
    period_start = current.replace(minute=0, second=0, microsecond=0)
    period_end = period_start + timedelta(hours=1)
    return period_start, period_end


def _normalize_utc(dt: datetime) -> datetime:
    """Normalize datetime to timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class UsageRepository:
    """Repository for persisting usage increments per order and period."""

    async def increment_usage(
        self,
        *,
        order_id: str,
        request_count: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        tool_calls: int = 0,
        client_id: str | None = None,
    ) -> None:
        """Persist usage increments into the current hourly usage record.

        Uses atomic INSERT ... ON CONFLICT DO UPDATE (no read-before-write) for
        concurrent-safe increments. Safe for multiple instances/workers.
        """
        if request_count == 0 and input_tokens == 0 and output_tokens == 0 and tool_calls == 0:
            return

        period_start, period_end = _current_hour_window()

        async with get_session() as session:
            dialect_name = session.get_bind().dialect.name
            if dialect_name == "postgresql":
                await self._increment_usage_atomic(
                    session, order_id, period_start, period_end,
                    request_count, input_tokens, output_tokens, tool_calls, client_id,
                )
            else:
                # SQLite (tests): fallback to read-modify-write; not safe under concurrency
                await self._increment_usage_fallback(
                    session, order_id, period_start, period_end,
                    request_count, input_tokens, output_tokens, tool_calls, client_id,
                )

    async def _increment_usage_atomic(
        self,
        session: Any,
        order_id: str,
        period_start: datetime,
        period_end: datetime,
        request_count: int,
        input_tokens: int,
        output_tokens: int,
        tool_calls: int,
        client_id: str | None,
    ) -> None:
        """Atomic upsert: single SQL statement, no read-before-write."""
        stmt = pg_insert(UsageRecordModel).values(
            order_id=order_id,
            client_id=client_id,
            request_count=request_count,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tool_calls=tool_calls,
            period_start=period_start,
            period_end=period_end,
            reported=False,
            reporting_started_at=None,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["order_id", "period_start", "period_end"],
            index_where=UsageRecordModel.reported.is_(False)
            & UsageRecordModel.reporting_started_at.is_(None),
            set_={
                UsageRecordModel.request_count: (
                    UsageRecordModel.request_count + stmt.excluded.request_count
                ),
                UsageRecordModel.input_tokens: (
                    UsageRecordModel.input_tokens + stmt.excluded.input_tokens
                ),
                UsageRecordModel.output_tokens: (
                    UsageRecordModel.output_tokens + stmt.excluded.output_tokens
                ),
                UsageRecordModel.tool_calls: (
                    UsageRecordModel.tool_calls + stmt.excluded.tool_calls
                ),
                UsageRecordModel.client_id: func.coalesce(
                    UsageRecordModel.client_id,
                    stmt.excluded.client_id,
                ),
            },
        )
        await session.execute(stmt)

    async def _increment_usage_fallback(
        self,
        session: Any,
        order_id: str,
        period_start: datetime,
        period_end: datetime,
        request_count: int,
        input_tokens: int,
        output_tokens: int,
        tool_calls: int,
        client_id: str | None,
    ) -> None:
        """Fallback for SQLite (tests): read-modify-write. Not concurrency-safe."""
        result = await session.execute(
            select(UsageRecordModel).where(
                UsageRecordModel.order_id == order_id,
                UsageRecordModel.period_start == period_start,
                UsageRecordModel.period_end == period_end,
                UsageRecordModel.reported.is_(False),
                UsageRecordModel.reporting_started_at.is_(None),
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.request_count += request_count
            existing.input_tokens += input_tokens
            existing.output_tokens += output_tokens
            existing.tool_calls += tool_calls
            if client_id and not existing.client_id:
                existing.client_id = client_id
            return

        session.add(
            UsageRecordModel(
                order_id=order_id,
                client_id=client_id,
                request_count=request_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                tool_calls=tool_calls,
                period_start=period_start,
                period_end=period_end,
                reported=False,
            )
        )

    async def claim_unreported_rows_for_reporting(
        self,
        *,
        order_id: str,
        start_time: datetime,
        end_time: datetime,
        limit: int = 1000,
    ) -> list[UsageRecordModel]:
        """Atomically claim unreported rows for reporting.

        Uses SELECT ... FOR UPDATE SKIP LOCKED so only one worker can claim
        a given row. Claimed rows are excluded from increment_usage (they
        leave the partial unique index). Returns claimed rows with metrics.

        Returns:
            List of claimed UsageRecordModel instances.
        """
        start = _normalize_utc(start_time)
        end = _normalize_utc(end_time)
        claimed_at = datetime.now(UTC)

        async with get_session() as session:
            stmt = (
                select(UsageRecordModel)
                .where(
                    UsageRecordModel.order_id == order_id,
                    UsageRecordModel.reported.is_(False),
                    UsageRecordModel.reporting_started_at.is_(None),
                    UsageRecordModel.period_start >= start,
                    UsageRecordModel.period_end <= end,
                )
                .limit(limit)
            )
            if session.get_bind().dialect.name == "postgresql":
                stmt = stmt.with_for_update(skip_locked=True)

            result = await session.execute(stmt)
            rows = list(result.scalars().all())
            if not rows:
                return []

            ids = [r.id for r in rows]
            await session.execute(
                update(UsageRecordModel)
                .where(UsageRecordModel.id.in_(ids))
                .values(reporting_started_at=claimed_at)
            )
            for r in rows:
                r.reporting_started_at = claimed_at
            return rows

    async def mark_reported_by_ids(
        self,
        ids: list[int],
        reported_at: datetime | None = None,
    ) -> int:
        """Mark claimed rows as reported. Call only after successful API report."""
        if not ids:
            return 0
        marked_at = _normalize_utc(reported_at or datetime.now(UTC))

        async with get_session() as session:
            stmt = (
                update(UsageRecordModel)
                .where(UsageRecordModel.id.in_(ids))
                .values(
                    reported=True,
                    reported_at=marked_at,
                    report_error=None,
                )
            )
            result = await session.execute(stmt)
            return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def release_claimed_rows(self, ids: list[int]) -> int:
        """Release claimed rows on report failure. Clears reporting_started_at."""
        if not ids:
            return 0

        async with get_session() as session:
            stmt = (
                update(UsageRecordModel)
                .where(UsageRecordModel.id.in_(ids))
                .values(reporting_started_at=None, report_error=None)
            )
            result = await session.execute(stmt)
            return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def release_stale_claimed_rows(
        self,
        *,
        older_than_minutes: int = 15,
    ) -> int:
        """Release rows claimed longer than threshold (e.g. worker crash).

        Returns:
            Number of rows released.
        """
        threshold = datetime.now(UTC) - timedelta(minutes=older_than_minutes)
        async with get_session() as session:
            stmt = (
                update(UsageRecordModel)
                .where(
                    UsageRecordModel.reported.is_(False),
                    UsageRecordModel.reporting_started_at.isnot(None),
                    UsageRecordModel.reporting_started_at < threshold,
                )
                .values(reporting_started_at=None, report_error=None)
            )
            result = await session.execute(stmt)
            return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def get_unreported_periods(
        self,
        *,
        older_than: datetime,
        max_age_hours: int = 168,
        limit: int = 20,
    ) -> list[tuple[str, datetime, datetime]]:
        """Return distinct (order_id, period_start, period_end) for unreported rows.

        Only returns periods with period_end <= older_than (includes every hour
        before the current hour). Excludes claimed rows and reported rows (avoids
        overlap with report_hourly, which runs first and marks rows reported).
        Ordered by period_end ASC for chronological catch-up.

        Args:
            older_than: Only periods with period_end <= this (e.g. start of current hour).
            max_age_hours: Ignore periods older than this (default 7 days).
            limit: Max periods to return.

        Returns:
            List of (order_id, period_start, period_end).
        """
        cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
        older_than_norm = _normalize_utc(older_than)
        async with get_session() as session:
            stmt = (
                select(
                    UsageRecordModel.order_id,
                    UsageRecordModel.period_start,
                    UsageRecordModel.period_end,
                )
                .where(
                    UsageRecordModel.reported.is_(False),
                    UsageRecordModel.reporting_started_at.is_(None),
                    UsageRecordModel.period_end <= older_than_norm,
                    UsageRecordModel.period_end >= cutoff,
                )
                .distinct()
                .order_by(UsageRecordModel.period_end.asc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return [
                (r.order_id, _normalize_utc(r.period_start), _normalize_utc(r.period_end))
                for r in result.all()
            ]


_usage_repo: UsageRepository | None = None


def get_usage_repository() -> UsageRepository:
    """Return process-wide usage repository singleton."""
    global _usage_repo
    if _usage_repo is None:
        _usage_repo = UsageRepository()
    return _usage_repo

