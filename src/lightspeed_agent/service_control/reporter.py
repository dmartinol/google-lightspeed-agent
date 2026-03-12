"""Usage reporter for Google Cloud Service Control."""

import logging
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from lightspeed_agent.config import get_settings
from lightspeed_agent.metering import get_usage_repository
from lightspeed_agent.service_control.client import (
    ServiceControlClient,
    get_service_control_client,
)
from lightspeed_agent.service_control.models import UsageReport

logger = logging.getLogger(__name__)


class ReportResult(BaseModel):
    """Result of a usage report attempt."""

    order_id: str = Field(..., description="Order ID")
    consumer_id: str = Field(..., description="Consumer ID")
    success: bool = Field(..., description="Whether report succeeded")
    error_message: str | None = Field(default=None, description="Error if failed")
    metrics_reported: dict[str, int] = Field(
        default_factory=dict, description="Metrics that were reported"
    )
    reported_at: datetime = Field(
        default_factory=datetime.utcnow, description="When report was attempted"
    )


class UsageReporter:
    """Handles aggregation and reporting of usage to Google Cloud.

    This reporter:
    - Aggregates usage metrics from the metering service
    - Maps internal metrics to Google-defined metric names
    - Reports usage via the Service Control API
    - Handles retries for failed reports
    - Stores unreported usage for later retry
    """

    # Mapping from internal metric types to Google metric names
    METRIC_MAPPING = {
        "api_calls": "api_calls",
        "send_message_requests": "send_message_requests",
        "streaming_requests": "streaming_requests",
        "input_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "total_tokens": "total_tokens",
        "mcp_tool_calls": "mcp_tool_calls",
    }

    def __init__(
        self,
        service_control_client: ServiceControlClient | None = None,
        max_retries: int = 3,
        retry_delay_seconds: int = 60,
    ) -> None:
        """Initialize the usage reporter.

        Args:
            service_control_client: Service Control API client.
            max_retries: Maximum number of retry attempts.
            retry_delay_seconds: Delay between retries.
        """
        self._client = service_control_client or get_service_control_client()
        self._max_retries = max_retries
        self._retry_delay = retry_delay_seconds
        self._settings = get_settings()
        self._usage_repo = get_usage_repository()

        # Store for failed reports (for retry)
        self._failed_reports: list[UsageReport] = []
        # Track last report time per order
        self._last_report_time: dict[str, datetime] = {}

    async def get_consumer_id(self, order_id: str) -> str | None:
        """Get the consumer ID (usageReportingId) for an order.

        Args:
            order_id: Order ID (entitlement ID).

        Returns:
            Consumer ID if found, None otherwise.
        """
        try:
            from lightspeed_agent.marketplace.repository import get_entitlement_repository

            repo = get_entitlement_repository()
            entitlement = await repo.get(order_id)

            if entitlement and entitlement.usage_reporting_id:
                return entitlement.usage_reporting_id

            # If no usage_reporting_id, construct one from project ID
            # Format: project:<project_id>
            if entitlement and entitlement.provider_id:
                return f"project:{entitlement.provider_id}"

        except ImportError:
            logger.warning("Marketplace repository not available")
        except Exception as e:
            logger.error("Failed to get consumer ID for order %s: %s", order_id, e)

        return None

    def map_metrics(self, internal_metrics: dict[str, int]) -> dict[str, int]:
        """Map internal metrics to Google metric names.

        Args:
            internal_metrics: Internal metric name -> value.

        Returns:
            Google metric name -> value.
        """
        mapped = {}
        for internal_name, value in internal_metrics.items():
            google_name = self.METRIC_MAPPING.get(internal_name)
            if google_name and value > 0:
                mapped[google_name] = value
        return mapped

    def _aggregate_metrics_from_rows(self, rows: list[Any]) -> dict[str, int]:
        """Aggregate metrics from claimed usage rows."""
        if not rows:
            return {}
        total_requests = sum(r.request_count for r in rows)
        total_input_tokens = sum(r.input_tokens for r in rows)
        total_output_tokens = sum(r.output_tokens for r in rows)
        total_tool_calls = sum(r.tool_calls for r in rows)

        metrics: dict[str, int] = {}
        if total_requests > 0:
            metrics["send_message_requests"] = total_requests
        if total_input_tokens > 0:
            metrics["input_tokens"] = total_input_tokens
        if total_output_tokens > 0:
            metrics["output_tokens"] = total_output_tokens
        if total_tool_calls > 0:
            metrics["mcp_tool_calls"] = total_tool_calls
        return metrics

    async def report_usage(
        self,
        order_id: str,
        start_time: datetime,
        end_time: datetime,
        retry_on_failure: bool = True,
    ) -> ReportResult:
        """Report usage for a single order.

        Args:
            order_id: Order ID to report for.
            start_time: Start of reporting period.
            end_time: End of reporting period.
            retry_on_failure: Whether to queue for retry on failure.

        Returns:
            ReportResult with status.
        """
        # Get consumer ID
        consumer_id = await self.get_consumer_id(order_id)
        if not consumer_id:
            return ReportResult(
                order_id=order_id,
                consumer_id="unknown",
                success=False,
                error_message="Could not determine consumer ID",
            )

        # 1. Atomically claim rows (no other worker can claim them)
        claimed_rows = await self._usage_repo.claim_unreported_rows_for_reporting(
            order_id=order_id,
            start_time=start_time,
            end_time=end_time,
        )

        if not claimed_rows:
            logger.debug("No billable metrics for order %s in period", order_id)
            return ReportResult(
                order_id=order_id,
                consumer_id=consumer_id,
                success=True,
                metrics_reported={},
            )

        # 2. Aggregate metrics from claimed rows
        metrics = self._aggregate_metrics_from_rows(claimed_rows)
        mapped_metrics = self.map_metrics(metrics)
        claimed_ids = [r.id for r in claimed_rows]

        # 3. Report to Service Control (no DB lock held)
        success, error_msg = await self._client.check_and_report(
            consumer_id=consumer_id,
            metrics=mapped_metrics,
            start_time=start_time,
            end_time=end_time,
            labels={
                "cloudmarketplace.googleapis.com/order_id": order_id,
            },
        )

        result = ReportResult(
            order_id=order_id,
            consumer_id=consumer_id,
            success=success,
            error_message=error_msg,
            metrics_reported=mapped_metrics if success else {},
        )

        if success:
            # 4a. Mark claimed rows as reported
            await self._usage_repo.mark_reported_by_ids(
                ids=claimed_ids,
                reported_at=end_time,
            )
            self._last_report_time[order_id] = end_time
        else:
            # 4b. Release claim for retry
            await self._usage_repo.release_claimed_rows(ids=claimed_ids)
            if retry_on_failure:
                self._queue_failed_report(
                    UsageReport(
                        order_id=order_id,
                        consumer_id=consumer_id,
                        start_time=start_time,
                        end_time=end_time,
                        error_message=error_msg,
                    )
                )

        return result

    async def report_all_usage(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> list[ReportResult]:
        """Report usage for all active orders.

        Args:
            start_time: Start of reporting period.
            end_time: End of reporting period.

        Returns:
            List of ReportResults.
        """
        # Get all active orders from marketplace
        order_ids = await self._get_active_order_ids()

        if not order_ids:
            logger.info("No active orders to report usage for")
            return []

        results = []
        for order_id in order_ids:
            result = await self.report_usage(
                order_id=order_id,
                start_time=start_time,
                end_time=end_time,
            )
            results.append(result)

        return results

    async def _get_active_order_ids(self) -> list[str]:
        """Get list of active order IDs from marketplace.

        Returns:
            List of active order IDs.
        """
        try:
            from lightspeed_agent.marketplace.repository import get_entitlement_repository

            repo = get_entitlement_repository()
            active_entitlements = await repo.get_all_active()
            return [e.id for e in active_entitlements]
        except ImportError:
            logger.warning("Marketplace repository not available")
            return []
        except Exception as e:
            logger.error("Failed to get active orders: %s", e)
            return []

    async def report_hourly(self) -> list[ReportResult]:
        """Report usage for the last hour.

        This is the minimum reporting frequency required by Google.

        Returns:
            List of ReportResults.
        """
        now = datetime.utcnow()
        # Round to the previous hour
        end_time = now.replace(minute=0, second=0, microsecond=0)
        start_time = end_time - timedelta(hours=1)

        logger.info(
            "Running hourly usage report for period %s to %s",
            start_time.isoformat(),
            end_time.isoformat(),
        )

        results = await self.report_all_usage(start_time, end_time)

        # Log summary
        successful = sum(1 for r in results if r.success)
        failed = sum(1 for r in results if not r.success)
        logger.info(
            "Hourly report complete: %d successful, %d failed",
            successful,
            failed,
        )

        return results

    async def release_stale_claimed_rows(self) -> int:
        """Release rows claimed longer than threshold (worker crash recovery).

        Returns:
            Number of rows released.
        """
        return await self._usage_repo.release_stale_claimed_rows(
            older_than_minutes=self._settings.metering_stale_claim_minutes,
        )

    async def run_hourly_cycle(self) -> list[ReportResult]:
        """Run full hourly reporting cycle: release stale, report hour, backfill.

        Call this from the scheduler for each hourly run.
        """
        released = await self.release_stale_claimed_rows()
        if released > 0:
            logger.warning(
                "Released %d stale claimed rows (orphaned by crash)",
                released,
            )

        results = await self.report_hourly()

        backfill_results = await self.report_backfill()
        if backfill_results:
            logger.info(
                "Backfill complete: %d successful, %d failed",
                sum(1 for r in backfill_results if r.success),
                sum(1 for r in backfill_results if not r.success),
            )
            results = results + backfill_results

        return results

    async def report_backfill(self) -> list[ReportResult]:
        """Report unreported periods (catches stale-release orphans and scheduler downtime).

        Processes periods older than the previous hour, up to max_age_hours.
        Run after report_hourly. Uses config: metering_backfill_max_age_hours,
        metering_backfill_limit_per_run.
        """
        now = datetime.utcnow()
        current_hour_start = now.replace(minute=0, second=0, microsecond=0)
        max_age = self._settings.metering_backfill_max_age_hours
        limit = self._settings.metering_backfill_limit_per_run

        periods = await self._usage_repo.get_unreported_periods(
            older_than=current_hour_start,
            max_age_hours=max_age,
            limit=limit,
        )
        if not periods:
            return []

        logger.info(
            "Backfill: reporting %d unreported period(s) (max_age=%dh, limit=%d)",
            len(periods),
            max_age,
            limit,
        )
        results = []
        for order_id, start_time, end_time in periods:
            result = await self.report_usage(
                order_id=order_id,
                start_time=start_time,
                end_time=end_time,
            )
            results.append(result)
        return results

    async def retry_failed_reports(self) -> list[ReportResult]:
        """Retry previously failed reports.

        Returns:
            List of ReportResults for retried reports.
        """
        if not self._failed_reports:
            return []

        logger.info("Retrying %d failed reports", len(self._failed_reports))

        results = []
        still_failed = []

        for report in self._failed_reports:
            if report.retry_count >= self._max_retries:
                logger.error(
                    "Report for order %s exceeded max retries (%d), giving up",
                    report.order_id,
                    self._max_retries,
                )
                continue

            # Increment retry count
            report.retry_count += 1

            # Get consumer ID again in case it was updated
            consumer_id = await self.get_consumer_id(report.order_id)
            if not consumer_id:
                consumer_id = report.consumer_id

            # Re-claim rows (released rows may be claimed again)
            claimed_rows = await self._usage_repo.claim_unreported_rows_for_reporting(
                order_id=report.order_id,
                start_time=report.start_time,
                end_time=report.end_time,
            )

            if not claimed_rows:
                # Rows already claimed/reported by another worker; remove from queue
                logger.info(
                    "Retry for order %s: no rows to claim (already handled), removing from queue",
                    report.order_id,
                )
                results.append(
                    ReportResult(
                        order_id=report.order_id,
                        consumer_id=consumer_id,
                        success=True,
                        metrics_reported={},
                    )
                )
                continue

            metrics = self._aggregate_metrics_from_rows(claimed_rows)
            mapped_metrics = self.map_metrics(metrics)
            claimed_ids = [r.id for r in claimed_rows]

            # Report to Service Control
            success, error_msg = await self._client.check_and_report(
                consumer_id=consumer_id,
                metrics=mapped_metrics,
                start_time=report.start_time,
                end_time=report.end_time,
                labels={
                    "cloudmarketplace.googleapis.com/order_id": report.order_id,
                    "retry_attempt": str(report.retry_count),
                },
            )

            result = ReportResult(
                order_id=report.order_id,
                consumer_id=consumer_id,
                success=success,
                error_message=error_msg,
                metrics_reported=mapped_metrics if success else {},
            )
            results.append(result)

            if not success:
                report.error_message = error_msg
                await self._usage_repo.release_claimed_rows(ids=claimed_ids)
                still_failed.append(report)
            else:
                await self._usage_repo.mark_reported_by_ids(
                    ids=claimed_ids,
                    reported_at=datetime.utcnow(),
                )
                logger.info(
                    "Successfully retried report for order %s on attempt %d",
                    report.order_id,
                    report.retry_count,
                )

        # Update failed reports list
        self._failed_reports = still_failed

        return results

    def _queue_failed_report(self, report: UsageReport) -> None:
        """Queue a failed report for retry.

        Args:
            report: The failed report.
        """
        # Check if we already have a report for this order/period
        for existing in self._failed_reports:
            if (
                existing.order_id == report.order_id
                and existing.start_time == report.start_time
                and existing.end_time == report.end_time
            ):
                # Update the existing report
                existing.error_message = report.error_message
                return

        self._failed_reports.append(report)
        logger.warning(
            "Queued failed report for order %s for retry (total queued: %d)",
            report.order_id,
            len(self._failed_reports),
        )

    def get_failed_reports_count(self) -> int:
        """Get the number of failed reports pending retry.

        Returns:
            Number of failed reports.
        """
        return len(self._failed_reports)

    def get_reporting_stats(self) -> dict[str, Any]:
        """Get reporting statistics.

        Returns:
            Dictionary of stats.
        """
        return {
            "failed_reports_pending": len(self._failed_reports),
            "orders_tracked": len(self._last_report_time),
            "last_report_times": {
                k: v.isoformat() for k, v in self._last_report_time.items()
            },
        }


# Global reporter instance
_usage_reporter: UsageReporter | None = None


def get_usage_reporter() -> UsageReporter:
    """Get the global usage reporter instance.

    Returns:
        UsageReporter instance.
    """
    global _usage_reporter
    if _usage_reporter is None:
        _usage_reporter = UsageReporter()
    return _usage_reporter
