"""Google Cloud Service Control API client."""

import logging
from datetime import datetime
from typing import Any
from uuid import uuid4

from lightspeed_agent.config import get_settings
from lightspeed_agent.service_control.models import (
    CheckError,
    CheckErrorCode,
    CheckResponse,
    ReportResponse,
)

logger = logging.getLogger(__name__)


class ServiceControlClient:
    """Client for Google Cloud Service Control API.

    This client handles:
    - Checking consumer status before reporting
    - Reporting usage metrics to Google
    - Error handling and retries
    """

    def __init__(
        self,
        service_name: str | None = None,
        project_id: str | None = None,
    ) -> None:
        """Initialize the Service Control client.

        Args:
            service_name: Service name (e.g., myservice.gcpmarketplace.example.com).
            project_id: Google Cloud project ID.
        """
        settings = get_settings()
        self._service_name = service_name or settings.service_control_service_name
        self._project_id = project_id or settings.google_cloud_project
        self._client: Any = None

    def _get_client(self) -> Any:
        """Get or create the Service Control client.

        Returns:
            ServiceControllerClient instance.

        Raises:
            ImportError: If google-cloud-service-control is not installed.
        """
        if self._client is None:
            try:
                from google.cloud import servicecontrol_v1

                self._client = servicecontrol_v1.ServiceControllerClient()
            except ImportError as e:
                logger.error(
                    "google-cloud-service-control not installed. "
                    "Install with: pip install google-cloud-service-control"
                )
                raise ImportError(
                    "google-cloud-service-control is required for usage reporting"
                ) from e

        return self._client

    async def check(
        self,
        consumer_id: str,
        operation_name: str | None = None,
    ) -> CheckResponse:
        """Check if a consumer is valid for service usage.

        This must be called before reporting usage to verify the consumer
        status (billing enabled, service activated, etc.).

        Args:
            consumer_id: Consumer ID (usageReportingId from entitlement).
            operation_name: Optional operation name.

        Returns:
            CheckResponse with validation status.
        """
        operation_id = str(uuid4())

        try:
            client = self._get_client()
            from google.cloud import servicecontrol_v1

            # Build the check request
            operation = servicecontrol_v1.Operation(
                operation_id=operation_id,
                operation_name=operation_name or f"{self._service_name}.usage",
                consumer_id=consumer_id,
                start_time=datetime.utcnow(),
            )

            request = servicecontrol_v1.CheckRequest(
                service_name=self._service_name,
                operation=operation,
            )

            # Execute the check
            response = client.check(request=request)

            # Convert to our model
            check_errors = []
            for error in response.check_errors:
                try:
                    code = CheckErrorCode(error.code.name)
                except ValueError:
                    code = CheckErrorCode.SERVICE_STATUS_UNAVAILABLE
                check_errors.append(
                    CheckError(code=code, detail=error.detail or "")
                )

            return CheckResponse(  # type: ignore[call-arg]
                operation_id=response.operation_id or operation_id,
                check_errors=check_errors,
            )

        except Exception as e:
            logger.error("Service Control check failed: %s", e)
            # Return a response indicating the check failed
            return CheckResponse(  # type: ignore[call-arg]
                operation_id=operation_id,
                check_errors=[
                    CheckError(
                        code=CheckErrorCode.SERVICE_STATUS_UNAVAILABLE,
                        detail=str(e),
                    )
                ],
            )

    async def report(
        self,
        consumer_id: str,
        metrics: dict[str, int],
        start_time: datetime,
        end_time: datetime,
        labels: dict[str, str] | None = None,
    ) -> ReportResponse:
        """Report usage metrics to Google Service Control.

        Args:
            consumer_id: Consumer ID (usageReportingId from entitlement).
            metrics: Dictionary of metric name -> value.
            start_time: Start of reporting period.
            end_time: End of reporting period.
            labels: Optional user labels for cost attribution.

        Returns:
            ReportResponse with status.
        """
        operation_id = str(uuid4())

        try:
            client = self._get_client()
            from google.cloud import servicecontrol_v1

            # Build metric value sets
            metric_value_sets = []
            for metric_name, value in metrics.items():
                if value > 0:
                    metric_value_sets.append(
                        servicecontrol_v1.MetricValueSet(
                            metric_name=f"{self._service_name}/{metric_name}",
                            metric_values=[
                                servicecontrol_v1.MetricValue(int64_value=value)
                            ],
                        )
                    )

            if not metric_value_sets:
                logger.debug("No metrics to report for consumer %s", consumer_id)
                return ReportResponse(report_errors=[])  # type: ignore[call-arg]

            # Build the operation
            operation = servicecontrol_v1.Operation(
                operation_id=operation_id,
                operation_name=f"{self._service_name}.usage",
                consumer_id=consumer_id,
                start_time=start_time,
                end_time=end_time,
                metric_value_sets=metric_value_sets,
                user_labels=labels or {},
            )

            # Build the report request
            request = servicecontrol_v1.ReportRequest(
                service_name=self._service_name,
                operations=[operation],
            )

            # Execute the report
            response = client.report(request=request)

            # Convert to our model
            report_errors = []
            for error in response.report_errors:
                report_errors.append({
                    "operation_id": error.operation_id,
                    "status": error.status,
                })

            return ReportResponse(  # type: ignore[call-arg]
                report_errors=report_errors,
                service_config_id=response.service_config_id,
                service_rollout_id=response.service_rollout_id,
            )

        except Exception as e:
            logger.error("Service Control report failed: %s", e)
            return ReportResponse(  # type: ignore[call-arg]
                report_errors=[{"error": str(e), "operation_id": operation_id}]
            )

    async def check_and_report(
        self,
        consumer_id: str,
        metrics: dict[str, int],
        start_time: datetime,
        end_time: datetime,
        labels: dict[str, str] | None = None,
    ) -> tuple[bool, str | None]:
        """Check consumer status and report usage if valid.

        This is the recommended flow: check first, then report.

        Args:
            consumer_id: Consumer ID (usageReportingId from entitlement).
            metrics: Dictionary of metric name -> value.
            start_time: Start of reporting period.
            end_time: End of reporting period.
            labels: Optional user labels.

        Returns:
            Tuple of (success, error_message).
        """
        # First, check the consumer status
        check_response = await self.check(consumer_id)

        if not check_response.is_valid:
            error_codes = [e.code.value for e in check_response.check_errors]
            error_msg = f"Consumer check failed: {', '.join(error_codes)}"
            logger.warning(error_msg)

            if check_response.should_block_service:
                return False, f"Service blocked: {error_msg}"

            # For non-blocking errors, we might still want to report
            logger.info("Non-blocking check errors, proceeding with report")

        # Report the usage
        report_response = await self.report(
            consumer_id=consumer_id,
            metrics=metrics,
            start_time=start_time,
            end_time=end_time,
            labels=labels,
        )

        if report_response.is_success:
            logger.info(
                "Successfully reported usage for consumer %s: %s",
                consumer_id,
                metrics,
            )
            return True, None
        else:
            error_msg = f"Report failed: {report_response.report_errors}"
            logger.error(error_msg)
            return False, error_msg


# Global client instance
_service_control_client: ServiceControlClient | None = None


def get_service_control_client() -> ServiceControlClient:
    """Get the global Service Control client instance.

    Returns:
        ServiceControlClient instance.
    """
    global _service_control_client
    if _service_control_client is None:
        _service_control_client = ServiceControlClient()
    return _service_control_client
