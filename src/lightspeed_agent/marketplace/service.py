"""Procurement service for handling marketplace entitlements and accounts."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lightspeed_agent.dcr.service import DCRService

import httpx

from lightspeed_agent.config import get_settings
from lightspeed_agent.marketplace.models import (
    Entitlement,
    EntitlementState,
    ProcurementEvent,
    ProcurementEventType,
)
from lightspeed_agent.marketplace.repository import (
    EntitlementRepository,
    get_entitlement_repository,
)

logger = logging.getLogger(__name__)


class ProcurementService:
    """Service for managing marketplace procurement operations.

    This service handles:
    - Processing entitlement events from Pub/Sub (filtered by product)
    - Managing entitlement lifecycle
    - Interacting with the Commerce Procurement API
    - Validating account state via Procurement API (source of truth)
    """

    PROCUREMENT_API_BASE = "https://cloudcommerceprocurement.googleapis.com/v1"

    def __init__(
        self,
        entitlement_repo: EntitlementRepository | None = None,
        dcr_service: DCRService | None = None,
    ) -> None:
        """Initialize the procurement service.

        Args:
            entitlement_repo: Entitlement repository (uses default if not provided).
            dcr_service: DCR service for OAuth client lifecycle (uses default if not provided).
        """
        self._entitlement_repo = entitlement_repo or get_entitlement_repository()
        self._dcr_service = dcr_service
        self._settings = get_settings()

    def _get_dcr_service(self) -> DCRService:
        """Get the DCR service (lazy initialization)."""
        if self._dcr_service is None:
            from lightspeed_agent.dcr.service import get_dcr_service

            self._dcr_service = get_dcr_service()
        return self._dcr_service

    async def process_event(self, event: ProcurementEvent) -> None:
        """Process a procurement event.

        Args:
            event: The procurement event to process.
        """
        logger.info(
            "Processing procurement event: %s (type=%s)",
            event.event_id,
            event.event_type,
        )

        handlers = {
            # Account lifecycle
            ProcurementEventType.ACCOUNT_CREATION_REQUESTED: (
                self._handle_account_creation_requested
            ),
            ProcurementEventType.ACCOUNT_ACTIVE: self._handle_account_active,
            ProcurementEventType.ACCOUNT_DELETED: self._handle_account_deleted,
            # Entitlement lifecycle
            ProcurementEventType.ENTITLEMENT_CREATION_REQUESTED: (
                self._handle_entitlement_creation_requested
            ),
            ProcurementEventType.ENTITLEMENT_ACTIVE: self._handle_entitlement_active,
            ProcurementEventType.ENTITLEMENT_RENEWED: self._handle_entitlement_renewed,
            ProcurementEventType.ENTITLEMENT_OFFER_ACCEPTED: (
                self._handle_entitlement_offer_accepted
            ),
            # Plan changes
            ProcurementEventType.ENTITLEMENT_PLAN_CHANGE_REQUESTED: (
                self._handle_plan_change_requested
            ),
            ProcurementEventType.ENTITLEMENT_PLAN_CHANGED: self._handle_plan_changed,
            ProcurementEventType.ENTITLEMENT_PLAN_CHANGE_CANCELLED: (
                self._handle_plan_change_cancelled
            ),
            # Cancellation
            ProcurementEventType.ENTITLEMENT_PENDING_CANCELLATION: (
                self._handle_pending_cancellation
            ),
            ProcurementEventType.ENTITLEMENT_CANCELLATION_REVERTED: (
                self._handle_cancellation_reverted
            ),
            ProcurementEventType.ENTITLEMENT_CANCELLING: self._handle_entitlement_cancelling,
            ProcurementEventType.ENTITLEMENT_CANCELLED: self._handle_entitlement_cancelled,
            ProcurementEventType.ENTITLEMENT_DELETED: self._handle_entitlement_deleted,
            # Offer events
            ProcurementEventType.ENTITLEMENT_OFFER_ENDED: self._handle_offer_ended,
        }

        handler = handlers.get(event.event_type)
        if handler:
            await handler(event)
        else:
            logger.warning("No handler for event type: %s", event.event_type)

    # Account lifecycle handlers

    async def _handle_account_creation_requested(
        self, event: ProcurementEvent
    ) -> None:
        """Handle ACCOUNT_CREATION_REQUESTED event.

        The Procurement API requires the account to be approved before any
        entitlements under it can be approved.
        """
        if not event.account:
            logger.error("ACCOUNT_CREATION_REQUESTED missing account info")
            return

        await self._approve_account(event.account.id)
        logger.info("Account creation requested and approved: %s", event.account.id)

    async def _handle_account_active(self, event: ProcurementEvent) -> None:
        """Handle ACCOUNT_ACTIVE event."""
        account_id = event.account.id if event.account else "unknown"
        logger.info("Account active: %s", account_id)

    async def _handle_account_deleted(self, event: ProcurementEvent) -> None:
        """Handle ACCOUNT_DELETED event."""
        account_id = event.account.id if event.account else "unknown"
        logger.info("Account deleted: %s", account_id)

    # Entitlement lifecycle handlers

    async def _handle_entitlement_creation_requested(
        self, event: ProcurementEvent
    ) -> None:
        """Handle ENTITLEMENT_CREATION_REQUESTED event.

        This creates a pending entitlement and optionally auto-approves it.
        """
        if not event.entitlement:
            logger.error("ENTITLEMENT_CREATION_REQUESTED missing entitlement info")
            return

        # Create entitlement record (idempotent — skip if already exists so
        # that Pub/Sub retries after a failed approval don't fail on duplicate).
        existing = await self._entitlement_repo.get(event.entitlement.id)
        if not existing:
            metadata = {}
            if event.entitlement.product:
                metadata["product_id"] = event.entitlement.product
            entitlement = Entitlement(
                id=event.entitlement.id,
                account_id="",  # Will be set when we fetch from API
                state=EntitlementState.PENDING_APPROVAL,
                plan=event.entitlement.new_plan,
                provider_id=event.provider_id,
                metadata=metadata,
            )
            await self._entitlement_repo.create(entitlement)

        # The Procurement API requires the account to be approved before
        # entitlements can be approved.  Resolve the account ID (from event or
        # by fetching the entitlement from the API) and approve it first.
        # This is idempotent (returns 400 if already approved).
        account_id = await self._resolve_account_id(event.entitlement.id, event)
        if account_id:
            await self._approve_account(account_id)
        else:
            logger.warning(
                "Could not resolve account for entitlement %s — "
                "entitlement approval may fail with FAILED_PRECONDITION",
                event.entitlement.id,
            )

        # Auto-approve the entitlement (raises on failure so Pub/Sub retries)
        await self._approve_entitlement(event.entitlement.id)

        logger.info(
            "Entitlement creation requested and approved: %s",
            event.entitlement.id,
        )

    async def _handle_entitlement_active(self, event: ProcurementEvent) -> None:
        """Handle ENTITLEMENT_ACTIVE event.

        This finalizes entitlement activation and provisions resources.
        """
        if not event.entitlement:
            logger.error("ENTITLEMENT_ACTIVE missing entitlement info")
            return

        metadata = {}
        if event.entitlement.product:
            metadata["product_id"] = event.entitlement.product

        entitlement = await self._entitlement_repo.get(event.entitlement.id)
        if entitlement:
            entitlement.state = EntitlementState.ACTIVE
            if metadata:
                entitlement.metadata = {**entitlement.metadata, **metadata}
            await self._entitlement_repo.update(entitlement)
        else:
            # Create if not exists (could happen if we missed creation event)
            entitlement = Entitlement(
                id=event.entitlement.id,
                account_id="",
                state=EntitlementState.ACTIVE,
                provider_id=event.provider_id,
                metadata=metadata,
            )
            await self._entitlement_repo.create(entitlement)

        logger.info("Entitlement activated: %s", event.entitlement.id)

    async def _handle_entitlement_renewed(self, event: ProcurementEvent) -> None:
        """Handle ENTITLEMENT_RENEWED event."""
        if not event.entitlement:
            return

        entitlement = await self._entitlement_repo.get(event.entitlement.id)
        if entitlement:
            entitlement.state = EntitlementState.ACTIVE
            if event.entitlement.new_offer_end_time:
                entitlement.offer_end_time = datetime.fromisoformat(
                    event.entitlement.new_offer_end_time.replace("Z", "+00:00")
                )
            await self._entitlement_repo.update(entitlement)
            logger.info("Entitlement renewed: %s", event.entitlement.id)

    async def _handle_entitlement_offer_accepted(
        self, event: ProcurementEvent
    ) -> None:
        """Handle ENTITLEMENT_OFFER_ACCEPTED event.

        This event is sent when a customer accepts an offer. The provider
        must still approve the account and entitlement via the Procurement API.
        """
        if not event.entitlement:
            return

        # Resolve account ID (from event or by fetching from API) and
        # approve account first, then entitlement.
        account_id = await self._resolve_account_id(event.entitlement.id, event)
        if account_id:
            await self._approve_account(account_id)
        else:
            logger.warning(
                "Could not resolve account for entitlement %s — "
                "entitlement approval may fail with FAILED_PRECONDITION",
                event.entitlement.id,
            )
        await self._approve_entitlement(event.entitlement.id)

        # Store/update the entitlement record locally
        entitlement = await self._entitlement_repo.get(event.entitlement.id)
        if not entitlement:
            metadata = {}
            if event.entitlement.product:
                metadata["product_id"] = event.entitlement.product
            entitlement = Entitlement(
                id=event.entitlement.id,
                account_id=account_id or "",
                state=EntitlementState.ACTIVE,
                plan=event.entitlement.new_plan,
                provider_id=event.provider_id,
                metadata=metadata,
            )
            await self._entitlement_repo.create(entitlement)
        else:
            entitlement.state = EntitlementState.ACTIVE
            entitlement.plan = event.entitlement.new_plan
            await self._entitlement_repo.update(entitlement)

        # Set offer times
        if event.entitlement.new_offer_start_time:
            entitlement.offer_start_time = datetime.fromisoformat(
                event.entitlement.new_offer_start_time.replace("Z", "+00:00")
            )
        if event.entitlement.new_offer_end_time:
            entitlement.offer_end_time = datetime.fromisoformat(
                event.entitlement.new_offer_end_time.replace("Z", "+00:00")
            )

        logger.info("Entitlement offer accepted and approved: %s", event.entitlement.id)

    # Plan change handlers

    async def _handle_plan_change_requested(self, event: ProcurementEvent) -> None:
        """Handle ENTITLEMENT_PLAN_CHANGE_REQUESTED event."""
        if not event.entitlement:
            return

        # Auto-approve plan changes
        await self._approve_plan_change(
            event.entitlement.id,
            event.entitlement.new_plan,
        )
        logger.info(
            "Plan change requested and approved: %s -> %s",
            event.entitlement.id,
            event.entitlement.new_plan,
        )

    async def _handle_plan_changed(self, event: ProcurementEvent) -> None:
        """Handle ENTITLEMENT_PLAN_CHANGED event."""
        if not event.entitlement:
            return

        entitlement = await self._entitlement_repo.get(event.entitlement.id)
        if entitlement:
            entitlement.plan = event.entitlement.new_plan
            await self._entitlement_repo.update(entitlement)
            logger.info(
                "Plan changed: %s -> %s",
                event.entitlement.id,
                event.entitlement.new_plan,
            )

    async def _handle_plan_change_cancelled(self, event: ProcurementEvent) -> None:
        """Handle ENTITLEMENT_PLAN_CHANGE_CANCELLED event."""
        if event.entitlement:
            logger.info("Plan change cancelled: %s", event.entitlement.id)

    # Cancellation handlers

    async def _handle_pending_cancellation(self, event: ProcurementEvent) -> None:
        """Handle ENTITLEMENT_PENDING_CANCELLATION event."""
        if not event.entitlement:
            return

        entitlement = await self._entitlement_repo.get(event.entitlement.id)
        if entitlement:
            entitlement.state = EntitlementState.PENDING_CANCELLATION
            entitlement.cancellation_reason = event.entitlement.cancellation_reason
            await self._entitlement_repo.update(entitlement)
            logger.info("Entitlement pending cancellation: %s", event.entitlement.id)

    async def _handle_cancellation_reverted(self, event: ProcurementEvent) -> None:
        """Handle ENTITLEMENT_CANCELLATION_REVERTED event."""
        if not event.entitlement:
            return

        entitlement = await self._entitlement_repo.get(event.entitlement.id)
        if entitlement:
            entitlement.state = EntitlementState.ACTIVE
            entitlement.cancellation_reason = None
            await self._entitlement_repo.update(entitlement)
            logger.info("Cancellation reverted: %s", event.entitlement.id)

    async def _handle_entitlement_cancelling(self, event: ProcurementEvent) -> None:
        """Handle ENTITLEMENT_CANCELLING event."""
        if not event.entitlement:
            return

        entitlement = await self._entitlement_repo.get(event.entitlement.id)
        if entitlement:
            entitlement.state = EntitlementState.PENDING_CANCELLATION
            await self._entitlement_repo.update(entitlement)
            logger.info("Entitlement cancelling: %s", event.entitlement.id)

    async def _handle_entitlement_cancelled(self, event: ProcurementEvent) -> None:
        """Handle ENTITLEMENT_CANCELLED event.

        Updates entitlement state and deletes the associated OAuth client
        from Red Hat SSO (if created via GMA) and from the local DB.
        """
        if not event.entitlement:
            return

        entitlement = await self._entitlement_repo.get(event.entitlement.id)
        if entitlement:
            entitlement.state = EntitlementState.CANCELLED
            entitlement.cancellation_reason = event.entitlement.cancellation_reason
            await self._entitlement_repo.update(entitlement)
            logger.info("Entitlement cancelled: %s", event.entitlement.id)

        await self._delete_oauth_client(event.entitlement.id)

    async def _handle_entitlement_deleted(self, event: ProcurementEvent) -> None:
        """Handle ENTITLEMENT_DELETED event.

        Updates entitlement state and ensures the associated OAuth client
        is cleaned up (safety net if not already deleted on cancellation).
        """
        if not event.entitlement:
            return

        entitlement = await self._entitlement_repo.get(event.entitlement.id)
        if entitlement:
            entitlement.state = EntitlementState.DELETED
            await self._entitlement_repo.update(entitlement)
            logger.info("Entitlement deleted: %s", event.entitlement.id)

        await self._delete_oauth_client(event.entitlement.id)

    async def _delete_oauth_client(self, order_id: str) -> None:
        """Delete the OAuth client associated with a marketplace order.

        Delegates to DCRService which handles GMA API deletion (if applicable)
        and local DB cleanup. Errors propagate so Pub/Sub retries the event.

        Args:
            order_id: The marketplace order/entitlement ID.
        """
        try:
            dcr_service = self._get_dcr_service()
            await dcr_service.delete_client(order_id)
        except Exception:
            logger.exception(
                "Failed to delete OAuth client for order %s", order_id
            )
            raise

    async def _handle_offer_ended(self, event: ProcurementEvent) -> None:
        """Handle ENTITLEMENT_OFFER_ENDED event."""
        if event.entitlement:
            logger.info("Offer ended: %s", event.entitlement.id)

    # Procurement API operations

    async def _resolve_account_id(
        self, entitlement_id: str, event: ProcurementEvent
    ) -> str | None:
        """Resolve the account ID for an entitlement.

        Pub/Sub events often arrive with an empty account field.  When that
        happens, fetch the entitlement from the Procurement API — the response
        always contains the account reference in the form
        ``providers/{provider}/accounts/{account_id}``.

        Args:
            entitlement_id: The entitlement ID to look up.
            event: The procurement event (checked first for account info).

        Returns:
            The account ID, or None if it cannot be resolved.
        """
        # 1. Try the event payload first
        if event.account and event.account.id:
            return event.account.id

        # 2. Fall back to the Procurement API
        if not self._settings.google_cloud_project:
            return None

        url = (
            f"{self.PROCUREMENT_API_BASE}/providers/{self._settings.google_cloud_project}"
            f"/entitlements/{entitlement_id}"
        )
        headers = await self._get_auth_headers()

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, timeout=30.0)
        except httpx.RequestError as e:
            # Transient network error — re-raise so Pub/Sub retries
            raise RuntimeError(
                f"Network error resolving account for entitlement {entitlement_id}: {e}"
            ) from e

        if response.status_code == 200:
            data = response.json()
            # account field is "providers/{provider}/accounts/{id}"
            account_ref = data.get("account", "")
            if "/accounts/" in account_ref:
                account_id = str(account_ref.rsplit("/accounts/", 1)[1])
                logger.info(
                    "Resolved account %s for entitlement %s via API",
                    account_id,
                    entitlement_id,
                )
                return account_id
        elif response.status_code == 404:
            # Entitlement not found — permanent failure, return None
            logger.warning(
                "Entitlement %s not found in Procurement API (404)",
                entitlement_id,
            )
        else:
            logger.warning(
                "Failed to fetch entitlement %s: HTTP %s — %s",
                entitlement_id,
                response.status_code,
                response.text,
            )

        return None

    async def _get_auth_headers(self) -> dict[str, str]:
        """Get authentication headers for Procurement API calls.

        Uses Application Default Credentials (ADC) for GCP authentication.

        Returns:
            Headers dict with Authorization bearer token.
        """
        try:
            import google.auth
            import google.auth.transport.requests

            credentials, project = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            request = google.auth.transport.requests.Request()
            credentials.refresh(request)  # type: ignore[no-untyped-call]
            return {"Authorization": f"Bearer {credentials.token}"}
        except Exception as e:
            logger.warning("Failed to get ADC credentials: %s", e)
            return {}

    async def _approve_account(self, account_id: str) -> None:
        """Approve an account via the Procurement API.

        The account must be approved before any entitlements under it can
        be approved.  Uses the "signup" approval name.

        Args:
            account_id: The account ID to approve.

        Raises:
            RuntimeError: If the Procurement API returns a non-200 response.
            httpx.RequestError: On network errors.
        """
        if not self._settings.google_cloud_project:
            logger.warning("GOOGLE_CLOUD_PROJECT not set, skipping account approval")
            return

        url = (
            f"{self.PROCUREMENT_API_BASE}/providers/{self._settings.google_cloud_project}"
            f"/accounts/{account_id}:approve"
        )
        headers = await self._get_auth_headers()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                content=b"",
                headers={**headers, "Content-Length": "0"},
                timeout=30.0,
            )

        if response.status_code == 200:
            logger.info("Approved account: %s", account_id)
        elif response.status_code in (400, 409):
            # 400 FAILED_PRECONDITION — account not in approvable state
            # 409 ALREADY_EXISTS — concurrent approval (e.g. race between
            #     ENTITLEMENT_OFFER_ACCEPTED and ENTITLEMENT_CREATION_REQUESTED)
            # Both are safe to ignore so Pub/Sub does not retry indefinitely.
            logger.warning(
                "Cannot approve account %s (already processed?): %s",
                account_id,
                response.text,
            )
        else:
            raise RuntimeError(
                f"Failed to approve account {account_id}: "
                f"HTTP {response.status_code} — {response.text}"
            )

    async def _approve_entitlement(self, entitlement_id: str) -> None:
        """Approve an entitlement via the Procurement API.

        Args:
            entitlement_id: The entitlement ID to approve.

        Raises:
            RuntimeError: If the Procurement API returns a non-200 response.
            httpx.RequestError: On network errors.
        """
        if not self._settings.google_cloud_project:
            logger.warning("GOOGLE_CLOUD_PROJECT not set, skipping approval")
            return

        url = (
            f"{self.PROCUREMENT_API_BASE}/providers/{self._settings.google_cloud_project}"
            f"/entitlements/{entitlement_id}:approve"
        )
        headers = await self._get_auth_headers()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                content=b"",
                headers={**headers, "Content-Length": "0"},
                timeout=30.0,
            )

        if response.status_code == 200:
            logger.info("Approved entitlement: %s", entitlement_id)
        elif response.status_code in (400, 409):
            # 400 FAILED_PRECONDITION — entitlement is not in an approvable
            #     state (e.g. already approved, cancelled).
            # 409 ALREADY_EXISTS — concurrent approval from parallel handlers.
            # Both are safe to ignore so Pub/Sub does not retry indefinitely.
            logger.warning(
                "Cannot approve entitlement %s (already processed?): %s",
                entitlement_id,
                response.text,
            )
        else:
            raise RuntimeError(
                f"Failed to approve entitlement {entitlement_id}: "
                f"HTTP {response.status_code} — {response.text}"
            )

    async def _get_account_state(self, account_id: str) -> str | None:
        """Get account state from the Procurement API.

        Queries the source of truth instead of relying on local DB state
        populated by Pub/Sub events.

        Args:
            account_id: The Procurement Account ID.

        Returns:
            Account state string (e.g., "ACCOUNT_ACTIVE") or None on error.
        """
        try:
            if not self._settings.google_cloud_project:
                logger.warning("GOOGLE_CLOUD_PROJECT not set, skipping account check")
                return "ACCOUNT_ACTIVE"  # Allow for development

            url = (
                f"{self.PROCUREMENT_API_BASE}/providers/{self._settings.google_cloud_project}"
                f"/accounts/{account_id}"
            )
            headers = await self._get_auth_headers()

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url,
                    headers=headers,
                    timeout=30.0,
                )

                if response.status_code == 200:
                    data = response.json()
                    state = str(data.get("state", ""))
                    logger.info("Account %s state: %s", account_id, state)
                    return state
                else:
                    logger.error(
                        "Failed to get account %s: %s",
                        account_id,
                        response.text,
                    )
                    return None
        except Exception as e:
            logger.error("Error getting account %s: %s", account_id, e)
            return None

    async def _approve_plan_change(
        self,
        entitlement_id: str,
        new_plan: str | None,
    ) -> None:
        """Approve a plan change via the Procurement API.

        Args:
            entitlement_id: The entitlement ID.
            new_plan: The new plan name.

        Raises:
            RuntimeError: If the Procurement API returns a non-200 response.
            httpx.RequestError: On network errors.
        """
        if not self._settings.google_cloud_project:
            logger.warning("GOOGLE_CLOUD_PROJECT not set, skipping plan change approval")
            return

        url = (
            f"{self.PROCUREMENT_API_BASE}/providers/{self._settings.google_cloud_project}"
            f"/entitlements/{entitlement_id}:approvePlanChange"
        )
        headers = await self._get_auth_headers()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json={"pendingPlanName": new_plan},
                headers=headers,
                timeout=30.0,
            )

        if response.status_code == 200:
            logger.info(
                "Approved plan change for %s: %s",
                entitlement_id,
                new_plan,
            )
        elif response.status_code in (400, 409):
            # Already processed or concurrent approval — safe to ignore.
            logger.warning(
                "Cannot approve plan change for %s (already processed?): %s",
                entitlement_id,
                response.text,
            )
        else:
            raise RuntimeError(
                f"Failed to approve plan change for {entitlement_id}: "
                f"HTTP {response.status_code} — {response.text}"
            )

    # Validation methods for DCR

    async def is_valid_account(self, account_id: str) -> bool:
        """Check if an account ID is valid for DCR.

        Queries the Procurement API directly instead of relying on local DB
        state. This is the source of truth for account state and works
        correctly in multi-agent deployments where account Pub/Sub events
        are not processed locally.

        Args:
            account_id: The Procurement Account ID.

        Returns:
            True if the account is active, False otherwise.
        """
        state = await self._get_account_state(account_id)
        return state == "ACCOUNT_ACTIVE"

    async def is_valid_order(self, order_id: str) -> bool:
        """Check if an order ID is valid for DCR.

        Args:
            order_id: The Order/Entitlement ID.

        Returns:
            True if valid, False otherwise.
        """
        return await self._entitlement_repo.is_valid(order_id)


# Global service instance
_procurement_service: ProcurementService | None = None


def get_procurement_service() -> ProcurementService:
    """Get the global procurement service instance.

    Returns:
        ProcurementService instance.
    """
    global _procurement_service
    if _procurement_service is None:
        _procurement_service = ProcurementService()
    return _procurement_service
