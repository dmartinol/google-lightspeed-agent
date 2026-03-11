"""Google Cloud Service Control integration for usage reporting.

This module handles:
- Reporting usage metrics to Google Cloud Service Control API
- Validating consumer status before reporting
- Scheduled hourly reporting (Google's minimum requirement)
- Retry logic for failed reports
"""

from lightspeed_agent.service_control.client import (
    ServiceControlClient,
    get_service_control_client,
)
from lightspeed_agent.service_control.models import (
    CheckError,
    CheckErrorCode,
    CheckResponse,
    ReportResponse,
    UsageReport,
)
from lightspeed_agent.service_control.reporter import (
    ReportResult,
    UsageReporter,
    get_usage_reporter,
)
from lightspeed_agent.service_control.router import router as service_control_router
from lightspeed_agent.service_control.scheduler import (
    ReportingScheduler,
    get_reporting_scheduler,
    start_reporting_scheduler,
    stop_reporting_scheduler,
)

__all__ = [
    # Client
    "ServiceControlClient",
    "get_service_control_client",
    # Models
    "CheckError",
    "CheckErrorCode",
    "CheckResponse",
    "ReportResponse",
    "UsageReport",
    # Reporter
    "ReportResult",
    "UsageReporter",
    "get_usage_reporter",
    # Scheduler
    "ReportingScheduler",
    "get_reporting_scheduler",
    "start_reporting_scheduler",
    "stop_reporting_scheduler",
    # Router
    "service_control_router",
]
