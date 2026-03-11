"""Data models for Google Cloud Service Control API."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CheckErrorCode(StrEnum):
    """Error codes from Service Control check response."""

    # Service not activated for the consumer
    SERVICE_NOT_ACTIVATED = "SERVICE_NOT_ACTIVATED"
    # Billing is disabled for the consumer
    BILLING_DISABLED = "BILLING_DISABLED"
    # Consumer project has been deleted
    PROJECT_DELETED = "PROJECT_DELETED"
    # Consumer project is marked for deletion
    PROJECT_INVALID = "PROJECT_INVALID"
    # IP address blocked
    IP_ADDRESS_BLOCKED = "IP_ADDRESS_BLOCKED"
    # Referer blocked
    REFERER_BLOCKED = "REFERER_BLOCKED"
    # Client app blocked
    CLIENT_APP_BLOCKED = "CLIENT_APP_BLOCKED"
    # API key invalid
    API_KEY_INVALID = "API_KEY_INVALID"
    # API key expired
    API_KEY_EXPIRED = "API_KEY_EXPIRED"
    # API key not found
    API_KEY_NOT_FOUND = "API_KEY_NOT_FOUND"
    # Namespace lookup failed
    NAMESPACE_LOOKUP_UNAVAILABLE = "NAMESPACE_LOOKUP_UNAVAILABLE"
    # Service status unavailable
    SERVICE_STATUS_UNAVAILABLE = "SERVICE_STATUS_UNAVAILABLE"
    # Billing status unavailable
    BILLING_STATUS_UNAVAILABLE = "BILLING_STATUS_UNAVAILABLE"


class CheckError(BaseModel):
    """Error from Service Control check response."""

    code: CheckErrorCode = Field(..., description="Error code")
    detail: str = Field(default="", description="Error detail message")


class CheckResponse(BaseModel):
    """Response from services.check API."""

    model_config = ConfigDict(populate_by_name=True)

    operation_id: str = Field(..., alias="operationId", description="Operation ID")
    check_errors: list[CheckError] = Field(
        default_factory=list, alias="checkErrors", description="Check errors"
    )

    @property
    def is_valid(self) -> bool:
        """Check if the response indicates a valid consumer."""
        return len(self.check_errors) == 0

    @property
    def should_block_service(self) -> bool:
        """Check if service should be blocked based on errors."""
        blocking_codes = {
            CheckErrorCode.SERVICE_NOT_ACTIVATED,
            CheckErrorCode.BILLING_DISABLED,
            CheckErrorCode.PROJECT_DELETED,
        }
        return any(e.code in blocking_codes for e in self.check_errors)


class ReportResponse(BaseModel):
    """Response from services.report API."""

    model_config = ConfigDict(populate_by_name=True)

    report_errors: list[dict[str, Any]] = Field(
        default_factory=list, alias="reportErrors", description="Report errors"
    )
    service_config_id: str | None = Field(
        None, alias="serviceConfigId", description="Service config ID"
    )
    service_rollout_id: str | None = Field(
        None, alias="serviceRolloutId", description="Service rollout ID"
    )

    @property
    def is_success(self) -> bool:
        """Check if report was successful."""
        return len(self.report_errors) == 0


class UsageReport(BaseModel):
    """A usage report to be sent to Service Control (or queued for retry)."""

    order_id: str = Field(..., description="Order ID (entitlement ID)")
    consumer_id: str = Field(..., description="Consumer ID (usageReportingId)")
    start_time: datetime = Field(..., description="Start of reporting period")
    end_time: datetime = Field(..., description="End of reporting period")
    reported: bool = Field(default=False, description="Whether successfully reported")
    reported_at: datetime | None = Field(default=None, description="When reported")
    error_message: str | None = Field(default=None, description="Error if report failed")
    retry_count: int = Field(default=0, description="Number of retry attempts")


