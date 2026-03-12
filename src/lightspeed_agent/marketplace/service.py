"""Procurement service for handling marketplace entitlements and accounts."""

import logging
from datetime import datetime

import httpx

from lightspeed_agent.config import get_settings
from lightspeed_agent.marketplace.models import (
    Account,
    AccountState,
    Entitlement,
    EntitlementState,
    ProcurementEvent,
    ProcurementEventType,
)
from lightspeed_agent.marketplace.repository import (
    AccountRepository,
    EntitlementRepository,
    get_account_repository,
    get_entitlement_repository,
)

logger = logging.getLogger(__name__)


class ProcurementService:
    """Service for managing marketplace procurement operations.

    This service handles:
    - Processing procurement events from Pub/Sub
    - Managing account and entitlement lifecycle
    - Interacting with the Commerce Procurement API
    - Generating OAuth credentials for orders
    """

    PROCUREMENT_API_BASE = "https://cloudcommerceprocurement.googleapis.com/v1"

    def __init__(
        self,
        account_repo: AccountRepository | None = None,
        entitlement_repo: EntitlementRepository | None = None,
    ) -> None:
        """Initialize the procurement service.

        Args:
            account_repo: Account repository (uses default if not provided).
            entitlement_repo: Entitlement repository (uses default if not provided).
        """
        self._account_repo = account_repo or get_account_repository()
        self._entitlement_repo = entitlement_repo or get_entitlement_repository()
        self._settings = get_settings()

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
            # Account events
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

    # Account event handlers

    async def _handle_account_creation_requested(
        self, event: ProcurementEvent
    ) -> None:
        """Handle ACCOUNT_CREATION_REQUESTED event.

        Per the reference implementation, this event triggers:
        1. Create account record in PENDING state
        2. Approve the account via Procurement API
        3. Account becomes ACTIVE after approval

        This must complete before DCR can return credentials for orders
        associated with this account.
        """
        if not event.account:
            logger.error("ACCOUNT_CREATION_REQUESTED missing account info")
            return

        # Create account in pending state
        account = await self._account_repo.get(event.account.id)
        if not account:
            account = Account(
                id=event.account.id,
                state=AccountState.PENDING,
                provider_id=event.provider_id,
            )
            await self._account_repo.create(account)

        # Approve the account via Procurement API
        approved = await self._approve_account(event.account.id)
        if approved:
            account.state = AccountState.ACTIVE
            await self._account_repo.update(account)
            logger.info(
                "Account creation requested and approved: %s",
                event.account.id,
            )
        else:
            logger.warning(
                "Account created but approval failed: %s (will retry on ACCOUNT_ACTIVE)",
                event.account.id,
            )

    async def _handle_account_active(self, event: ProcurementEvent) -> None:
        """Handle ACCOUNT_ACTIVE event."""
        if not event.account:
            logger.error("ACCOUNT_ACTIVE event missing account info")
            return

        account = await self._account_repo.get(event.account.id)
        if account:
            account.state = AccountState.ACTIVE
            await self._account_repo.update(account)
        else:
            account = Account(
                id=event.account.id,
                state=AccountState.ACTIVE,
                provider_id=event.provider_id,
            )
            await self._account_repo.create(account)

        logger.info("Account activated: %s", event.account.id)

    async def _handle_account_deleted(self, event: ProcurementEvent) -> None:
        """Handle ACCOUNT_DELETED event."""
        if not event.account:
            logger.error("ACCOUNT_DELETED event missing account info")
            return

        account = await self._account_repo.get(event.account.id)
        if account:
            account.state = AccountState.DELETED
            await self._account_repo.update(account)
            logger.info("Account marked as deleted: %s", event.account.id)

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

        # Create entitlement record
        entitlement = Entitlement(
            id=event.entitlement.id,
            account_id="",  # Will be set when we fetch from API
            state=EntitlementState.PENDING_APPROVAL,
            plan=event.entitlement.new_plan,
            provider_id=event.provider_id,
        )
        await self._entitlement_repo.create(entitlement)

        # Auto-approve the entitlement
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

        entitlement = await self._entitlement_repo.get(event.entitlement.id)
        if entitlement:
            entitlement.state = EntitlementState.ACTIVE
            await self._entitlement_repo.update(entitlement)
        else:
            # Create if not exists (could happen if we missed creation event)
            entitlement = Entitlement(
                id=event.entitlement.id,
                account_id="",
                state=EntitlementState.ACTIVE,
                provider_id=event.provider_id,
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
        """Handle ENTITLEMENT_OFFER_ACCEPTED event (for auto-approved offers)."""
        if not event.entitlement:
            return

        entitlement = await self._entitlement_repo.get(event.entitlement.id)
        if not entitlement:
            entitlement = Entitlement(
                id=event.entitlement.id,
                account_id="",
                state=EntitlementState.ACTIVE,
                plan=event.entitlement.new_plan,
                provider_id=event.provider_id,
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

        logger.info("Entitlement offer accepted: %s", event.entitlement.id)

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
        """Handle ENTITLEMENT_CANCELLED event."""
        if not event.entitlement:
            return

        entitlement = await self._entitlement_repo.get(event.entitlement.id)
        if entitlement:
            entitlement.state = EntitlementState.CANCELLED
            entitlement.cancellation_reason = event.entitlement.cancellation_reason
            await self._entitlement_repo.update(entitlement)
            logger.info("Entitlement cancelled: %s", event.entitlement.id)

    async def _handle_entitlement_deleted(self, event: ProcurementEvent) -> None:
        """Handle ENTITLEMENT_DELETED event."""
        if not event.entitlement:
            return

        entitlement = await self._entitlement_repo.get(event.entitlement.id)
        if entitlement:
            entitlement.state = EntitlementState.DELETED
            await self._entitlement_repo.update(entitlement)
            logger.info("Entitlement deleted: %s", event.entitlement.id)

    async def _handle_offer_ended(self, event: ProcurementEvent) -> None:
        """Handle ENTITLEMENT_OFFER_ENDED event."""
        if event.entitlement:
            logger.info("Offer ended: %s", event.entitlement.id)

    # Procurement API operations

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

    async def _approve_entitlement(self, entitlement_id: str) -> bool:
        """Approve an entitlement via the Procurement API.

        Args:
            entitlement_id: The entitlement ID to approve.

        Returns:
            True if approved, False otherwise.
        """
        try:
            if not self._settings.service_control_service_name:
                logger.warning("SERVICE_CONTROL_SERVICE_NAME not set, skipping approval")
                return True  # Allow for development

            svc = self._settings.service_control_service_name
            url = (
                f"{self.PROCUREMENT_API_BASE}/providers/{svc}"
                f"/entitlements/{entitlement_id}:approve"
            )
            headers = await self._get_auth_headers()

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    json={},
                    headers=headers,
                    timeout=30.0,
                )

                if response.status_code == 200:
                    logger.info("Approved entitlement: %s", entitlement_id)
                    return True
                else:
                    logger.error(
                        "Failed to approve entitlement %s: %s",
                        entitlement_id,
                        response.text,
                    )
                    return False
        except Exception as e:
            logger.error("Error approving entitlement %s: %s", entitlement_id, e)
            return False

    async def _approve_account(self, account_id: str) -> bool:
        """Approve an account via the Procurement API.

        Per the reference implementation, accounts must be approved before
        DCR can return credentials.

        Args:
            account_id: The Procurement Account ID to approve.

        Returns:
            True if approved, False otherwise.
        """
        try:
            if not self._settings.service_control_service_name:
                logger.warning("SERVICE_CONTROL_SERVICE_NAME not set, skipping approval")
                return True  # Allow for development

            svc = self._settings.service_control_service_name
            url = (
                f"{self.PROCUREMENT_API_BASE}/providers/{svc}"
                f"/accounts/{account_id}:approve"
            )
            headers = await self._get_auth_headers()

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    json={},
                    headers=headers,
                    timeout=30.0,
                )

                if response.status_code == 200:
                    logger.info("Approved account: %s", account_id)
                    return True
                else:
                    logger.error(
                        "Failed to approve account %s: %s",
                        account_id,
                        response.text,
                    )
                    return False
        except Exception as e:
            logger.error("Error approving account %s: %s", account_id, e)
            return False

    async def _approve_plan_change(
        self,
        entitlement_id: str,
        new_plan: str | None,
    ) -> bool:
        """Approve a plan change via the Procurement API.

        Args:
            entitlement_id: The entitlement ID.
            new_plan: The new plan name.

        Returns:
            True if approved, False otherwise.
        """
        try:
            if not self._settings.service_control_service_name:
                logger.warning("SERVICE_CONTROL_SERVICE_NAME not set, skipping approval")
                return True  # Allow for development

            svc = self._settings.service_control_service_name
            url = (
                f"{self.PROCUREMENT_API_BASE}/providers/{svc}"
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
                    return True
                else:
                    logger.error(
                        "Failed to approve plan change for %s: %s",
                        entitlement_id,
                        response.text,
                    )
                    return False
        except Exception as e:
            logger.error("Error approving plan change for %s: %s", entitlement_id, e)
            return False

    # Validation methods for DCR

    async def is_valid_account(self, account_id: str) -> bool:
        """Check if an account ID is valid for DCR.

        Args:
            account_id: The Procurement Account ID.

        Returns:
            True if valid, False otherwise.
        """
        return await self._account_repo.is_valid(account_id)

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
