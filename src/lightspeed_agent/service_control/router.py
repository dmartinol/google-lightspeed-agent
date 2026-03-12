"""API router for Service Control reporting endpoints."""

import logging
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lightspeed_agent.auth.dependencies import CurrentUser, require_scope
from lightspeed_agent.service_control.reporter import (
    get_usage_reporter,
)
from lightspeed_agent.service_control.scheduler import get_reporting_scheduler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/service-control", tags=["service-control"])


class SchedulerStatus(BaseModel):
    """Scheduler status response."""

    running: bool = Field(..., description="Whether scheduler is running")
    hourly_interval_seconds: int = Field(..., description="Hourly report interval")
    retry_interval_seconds: int = Field(..., description="Retry interval")
    last_hourly_run: str | None = Field(None, description="Last hourly run timestamp")
    last_retry_run: str | None = Field(None, description="Last retry run timestamp")
    hourly_run_count: int = Field(default=0, description="Total hourly runs")
    retry_run_count: int = Field(default=0, description="Total retry runs")
    failed_reports_pending: int = Field(default=0, description="Failed reports pending retry")
    orders_tracked: int = Field(default=0, description="Orders being tracked")


class ReportRequest(BaseModel):
    """Manual report request."""

    order_id: str = Field(..., description="Order ID to report for")
    start_time: datetime | None = Field(None, description="Start of period (default: last hour)")
    end_time: datetime | None = Field(None, description="End of period (default: now)")


class ReportResponse(BaseModel):
    """Report response."""

    order_id: str = Field(..., description="Order ID")
    consumer_id: str = Field(..., description="Consumer ID")
    success: bool = Field(..., description="Whether report succeeded")
    error_message: str | None = Field(None, description="Error if failed")
    metrics_reported: dict[str, int] = Field(default_factory=dict, description="Metrics reported")
    reported_at: datetime = Field(..., description="Report timestamp")


@router.get(
    "/status",
    response_model=SchedulerStatus,
    summary="Get scheduler status",
    description="Get the status of the usage reporting scheduler.",
)
async def get_status(
    user: Annotated[CurrentUser, Depends(require_scope("metering:admin"))],
) -> SchedulerStatus:
    """Get scheduler status.

    Args:
        user: Authenticated admin user.

    Returns:
        Scheduler status.
    """
    try:
        scheduler = get_reporting_scheduler()
        status = scheduler.get_status()
        return SchedulerStatus(**status)
    except Exception as e:
        logger.error("Failed to get scheduler status: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post(
    "/report",
    response_model=ReportResponse,
    summary="Trigger manual report",
    description="Manually trigger a usage report for a specific order.",
)
async def trigger_report(
    request: ReportRequest,
    user: Annotated[CurrentUser, Depends(require_scope("metering:admin"))],
) -> ReportResponse:
    """Trigger a manual usage report.

    Args:
        request: Report request.
        user: Authenticated admin user.

    Returns:
        Report result.
    """
    try:
        reporter = get_usage_reporter()

        # Default to last hour if not specified
        now = datetime.utcnow()
        start_time = request.start_time or (now - timedelta(hours=1))
        end_time = request.end_time or now

        result = await reporter.report_usage(
            order_id=request.order_id,
            start_time=start_time,
            end_time=end_time,
            retry_on_failure=True,
        )

        return ReportResponse(
            order_id=result.order_id,
            consumer_id=result.consumer_id,
            success=result.success,
            error_message=result.error_message,
            metrics_reported=result.metrics_reported,
            reported_at=result.reported_at,
        )
    except Exception as e:
        logger.error("Failed to trigger report: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post(
    "/report/all",
    response_model=list[ReportResponse],
    summary="Trigger report for all orders",
    description="Manually trigger usage reports for all orders with activity.",
)
async def trigger_all_reports(
    user: Annotated[CurrentUser, Depends(require_scope("metering:admin"))],
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> list[ReportResponse]:
    """Trigger reports for all orders.

    Args:
        user: Authenticated admin user.
        start_time: Start of period.
        end_time: End of period.

    Returns:
        List of report results.
    """
    try:
        reporter = get_usage_reporter()

        # Default to last hour if not specified
        now = datetime.utcnow()
        start = start_time or (now - timedelta(hours=1))
        end = end_time or now

        results = await reporter.report_all_usage(start, end)

        return [
            ReportResponse(
                order_id=r.order_id,
                consumer_id=r.consumer_id,
                success=r.success,
                error_message=r.error_message,
                metrics_reported=r.metrics_reported,
                reported_at=r.reported_at,
            )
            for r in results
        ]
    except Exception as e:
        logger.error("Failed to trigger all reports: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post(
    "/retry",
    response_model=list[ReportResponse],
    summary="Retry failed reports",
    description="Manually trigger retry of failed reports.",
)
async def trigger_retry(
    user: Annotated[CurrentUser, Depends(require_scope("metering:admin"))],
) -> list[ReportResponse]:
    """Trigger retry of failed reports.

    Args:
        user: Authenticated admin user.

    Returns:
        List of retry results.
    """
    try:
        reporter = get_usage_reporter()
        results = await reporter.retry_failed_reports()

        return [
            ReportResponse(
                order_id=r.order_id,
                consumer_id=r.consumer_id,
                success=r.success,
                error_message=r.error_message,
                metrics_reported=r.metrics_reported,
                reported_at=r.reported_at,
            )
            for r in results
        ]
    except Exception as e:
        logger.error("Failed to trigger retry: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
