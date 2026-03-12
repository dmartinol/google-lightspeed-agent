"""SQLAlchemy ORM models for database persistence."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    TIMESTAMP,
    Boolean,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from lightspeed_agent.db.base import Base

# Use ARRAY(String) on PostgreSQL, JSON on SQLite (for tests)
StringList = ARRAY(String).with_variant(JSON, "sqlite")


class MarketplaceAccountModel(Base):
    """ORM model for marketplace accounts."""

    __tablename__ = "marketplace_accounts"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    provider_id: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSON,
        default=dict,
    )


class MarketplaceEntitlementModel(Base):
    """ORM model for marketplace entitlements (orders)."""

    __tablename__ = "marketplace_entitlements"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(255), nullable=False)
    product_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    plan: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    usage_reporting_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    offer_start_time: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    offer_end_time: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSON,
        default=dict,
    )


class DCRClientModel(Base):
    """ORM model for DCR registered clients."""

    __tablename__ = "dcr_clients"

    order_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    client_secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    registration_access_token_encrypted: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    redirect_uris: Mapped[list[str] | None] = mapped_column(
        StringList,
        nullable=True,
    )
    grant_types: Mapped[list[str] | None] = mapped_column(
        StringList,
        default=lambda: ["authorization_code", "refresh_token"],
    )
    keycloak_client_uuid: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSON,
        default=dict,
    )


class UsageRecordModel(Base):
    """ORM model for usage tracking records."""

    __tablename__ = "usage_records"
    __table_args__ = (
        # Partial unique index: at most one "available" row per (order_id, period).
        # Available = unreported AND not claimed (reporting_started_at IS NULL).
        # Enables atomic INSERT ... ON CONFLICT for increments and claim-then-report.
        Index(
            "uq_usage_records_order_period_unreported",
            "order_id",
            "period_start",
            "period_end",
            unique=True,
            postgresql_where=text("reported IS FALSE AND reporting_started_at IS NULL"),
            sqlite_where=text("reported = 0 AND reporting_started_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    period_start: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )
    period_end: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )
    reported: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    reporting_started_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    reported_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    report_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
    )
