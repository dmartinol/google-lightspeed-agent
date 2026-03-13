"""Data models for Google Cloud Marketplace Procurement integration."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProcurementEventType(StrEnum):
    """Marketplace Procurement event types from Pub/Sub."""

    # Account events
    ACCOUNT_CREATION_REQUESTED = "ACCOUNT_CREATION_REQUESTED"
    ACCOUNT_ACTIVE = "ACCOUNT_ACTIVE"
    ACCOUNT_DELETED = "ACCOUNT_DELETED"

    # Entitlement lifecycle events
    ENTITLEMENT_CREATION_REQUESTED = "ENTITLEMENT_CREATION_REQUESTED"
    ENTITLEMENT_ACTIVE = "ENTITLEMENT_ACTIVE"
    ENTITLEMENT_RENEWED = "ENTITLEMENT_RENEWED"
    ENTITLEMENT_OFFER_ACCEPTED = "ENTITLEMENT_OFFER_ACCEPTED"

    # Plan change events
    ENTITLEMENT_PLAN_CHANGE_REQUESTED = "ENTITLEMENT_PLAN_CHANGE_REQUESTED"
    ENTITLEMENT_PLAN_CHANGED = "ENTITLEMENT_PLAN_CHANGED"
    ENTITLEMENT_PLAN_CHANGE_CANCELLED = "ENTITLEMENT_PLAN_CHANGE_CANCELLED"

    # Cancellation events
    ENTITLEMENT_PENDING_CANCELLATION = "ENTITLEMENT_PENDING_CANCELLATION"
    ENTITLEMENT_CANCELLATION_REVERTED = "ENTITLEMENT_CANCELLATION_REVERTED"
    ENTITLEMENT_CANCELLING = "ENTITLEMENT_CANCELLING"
    ENTITLEMENT_CANCELLED = "ENTITLEMENT_CANCELLED"
    ENTITLEMENT_DELETED = "ENTITLEMENT_DELETED"

    # Offer events
    ENTITLEMENT_OFFER_ENDED = "ENTITLEMENT_OFFER_ENDED"


class AccountState(StrEnum):
    """Account states in the procurement lifecycle."""

    PENDING = "pending"
    ACTIVE = "active"
    DELETED = "deleted"


class EntitlementState(StrEnum):
    """Entitlement states in the procurement lifecycle."""

    PENDING = "pending"
    PENDING_APPROVAL = "pending_approval"
    ACTIVE = "active"
    PENDING_CANCELLATION = "pending_cancellation"
    CANCELLED = "cancelled"
    DELETED = "deleted"
    SUSPENDED = "suspended"


class EntitlementInfo(BaseModel):
    """Entitlement information from Pub/Sub message."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(..., description="Entitlement ID (Order ID)")
    update_time: str | None = Field(
        default=None,
        alias="updateTime",
        description="Last update timestamp",
    )
    new_plan: str | None = Field(
        default=None,
        alias="newPlan",
        description="New plan for plan change events",
    )
    new_offer_duration_years: int | None = Field(
        default=None,
        alias="newOfferDurationYears",
        description="New offer duration in years",
    )
    new_offer_duration_months: int | None = Field(
        default=None,
        alias="newOfferDurationMonths",
        description="New offer duration in months",
    )
    new_offer_start_time: str | None = Field(
        default=None,
        alias="newOfferStartTime",
        description="When the new offer starts",
    )
    new_offer_end_time: str | None = Field(
        default=None,
        alias="newOfferEndTime",
        description="When the new offer ends",
    )
    cancellation_reason: str | None = Field(
        default=None,
        alias="cancellationReason",
        description="Reason for cancellation",
    )
    previous_plan: str | None = Field(
        default=None,
        alias="previousPlan",
        description="Previous plan for plan change events",
    )
    product: str | None = Field(
        default=None,
        description="Product identifier from marketplace",
    )


class AccountInfo(BaseModel):
    """Account information from Pub/Sub message."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(..., description="Account ID (Procurement Account ID)")
    update_time: str | None = Field(
        default=None,
        alias="updateTime",
        description="Last update timestamp",
    )


class ProcurementEvent(BaseModel):
    """Marketplace Procurement event from Pub/Sub."""

    model_config = ConfigDict(populate_by_name=True)

    event_id: str = Field(..., alias="eventId", description="Unique event identifier")
    event_type: ProcurementEventType = Field(
        ...,
        alias="eventType",
        description="Type of procurement event",
    )
    provider_id: str = Field(
        ...,
        alias="providerId",
        description="Partner/Provider ID",
    )
    entitlement: EntitlementInfo | None = Field(
        default=None,
        description="Entitlement information (for entitlement events)",
    )
    account: AccountInfo | None = Field(
        default=None,
        description="Account information (for account events)",
    )



class Account(BaseModel):
    """Stored account record."""

    id: str = Field(..., description="Account ID (Procurement Account ID)")
    state: AccountState = Field(
        default=AccountState.PENDING,
        description="Account state",
    )
    provider_id: str = Field(..., description="Provider ID")
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Creation timestamp",
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Last update timestamp",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata",
    )


class Entitlement(BaseModel):
    """Stored entitlement record (Order)."""

    id: str = Field(..., description="Entitlement ID (Order ID)")
    account_id: str = Field(..., description="Associated Account ID")
    state: EntitlementState = Field(
        default=EntitlementState.PENDING,
        description="Entitlement state",
    )
    plan: str | None = Field(default=None, description="Current pricing plan")
    provider_id: str = Field(..., description="Provider ID")
    usage_reporting_id: str | None = Field(
        default=None,
        description="Consumer ID for Service Control usage reporting",
    )
    offer_start_time: datetime | None = Field(
        default=None,
        description="When the offer starts",
    )
    offer_end_time: datetime | None = Field(
        default=None,
        description="When the offer ends",
    )
    cancellation_reason: str | None = Field(
        default=None,
        description="Reason for cancellation",
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Creation timestamp",
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Last update timestamp",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata",
    )


