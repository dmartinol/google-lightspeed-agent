"""Marketplace Handler Router.

Implements a hybrid /dcr endpoint that handles both:
1. Direct DCR requests from Gemini Enterprise (contains software_statement)
2. Pub/Sub events from Google Cloud Marketplace (contains message structure)

This pattern follows the reference implementation where a single endpoint
intelligently routes based on request content.
"""

import base64
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from lightspeed_agent.config import get_settings
from lightspeed_agent.dcr import DCRError, DCRRequest, get_dcr_service
from lightspeed_agent.marketplace.models import ProcurementEvent, ProcurementEventType
from lightspeed_agent.marketplace.service import get_procurement_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Marketplace Handler"])


@router.post("/dcr")
async def hybrid_dcr_handler(request: Request) -> JSONResponse:
    """Hybrid handler for DCR and Pub/Sub events.

    This endpoint handles two types of requests:

    1. **Direct DCR Request** (from Gemini Enterprise):
       - Contains `software_statement` in the body
       - Validates JWT, creates OAuth client, returns credentials
       - Synchronous flow

    2. **Pub/Sub Event** (from Google Cloud Marketplace):
       - Contains `message` with base64-encoded data
       - Processes procurement events (account/entitlement creation)
       - Approves via Procurement API
       - Asynchronous flow

    Returns:
        DCR credentials or acknowledgment.
    """
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from e

    # Route based on request content
    if "software_statement" in body:
        # Path A: Direct DCR request from Gemini Enterprise
        return await _handle_dcr_request(body)
    elif "message" in body:
        # Path B: Pub/Sub event from Marketplace
        return await _handle_pubsub_event(body)
    else:
        # Unknown request format
        logger.warning("Unknown request format: %s", list(body.keys()))
        raise HTTPException(
            status_code=400,
            detail="Request must contain either 'software_statement' (DCR) or 'message' (Pub/Sub)",
        )


async def _handle_dcr_request(body: dict[str, Any]) -> JSONResponse:
    """Handle a direct DCR request from Gemini Enterprise.

    Args:
        body: Request body containing software_statement.

    Returns:
        JSONResponse with client credentials or error.
    """
    logger.info("Processing DCR request")

    dcr_service = get_dcr_service()
    dcr_request = DCRRequest(
        software_statement=body["software_statement"],
        client_id=body.get("client_id"),
        client_secret=body.get("client_secret"),
    )

    result = await dcr_service.register_client(dcr_request)

    if isinstance(result, DCRError):
        logger.warning("DCR error: %s - %s", result.error, result.error_description)
        return JSONResponse(
            status_code=400,
            content={
                "error": result.error.value,
                "error_description": result.error_description,
            },
        )

    logger.info("DCR successful: client_id=%s", result.client_id)
    return JSONResponse(
        status_code=201,
        content={
            "client_id": result.client_id,
            "client_secret": result.client_secret,
            "client_secret_expires_at": result.client_secret_expires_at,
        },
    )


async def _handle_pubsub_event(body: dict[str, Any]) -> JSONResponse:
    """Handle a Pub/Sub event from Google Cloud Marketplace.

    Args:
        body: Request body containing Pub/Sub message.

    Returns:
        JSONResponse acknowledging the event.
    """
    message = body.get("message", {})
    message_id = message.get("messageId", "unknown")

    logger.info("Processing Pub/Sub message: %s", message_id)

    # Decode the message data
    data_b64 = message.get("data", "")
    if not data_b64:
        logger.warning("Empty Pub/Sub message data")
        return JSONResponse(content={"status": "ok", "message": "Empty message"})

    try:
        data_json = base64.b64decode(data_b64).decode("utf-8")
        data = json.loads(data_json)
    except Exception as e:
        logger.error("Failed to decode Pub/Sub message: %s", e)
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid message encoding"},
        )

    # Extract event type and process
    event_type_str = data.get("eventType", "")
    logger.info("Marketplace event type: %s", event_type_str)

    # Try to parse as a known event type
    try:
        event_type = ProcurementEventType(event_type_str)
    except ValueError:
        logger.warning("Unknown event type: %s", event_type_str)
        return JSONResponse(content={"status": "ok", "message": f"Unknown event: {event_type_str}"})

    # Build procurement event
    event = _build_procurement_event(data, event_type)
    if not event:
        logger.warning("Could not build procurement event from data")
        return JSONResponse(content={"status": "ok", "message": "Invalid event data"})

    # Process the event
    procurement_service = get_procurement_service()
    await procurement_service.process_event(event)

    logger.info("Processed marketplace event: %s (%s)", message_id, event_type_str)
    return JSONResponse(content={"status": "ok", "event_type": event_type_str})


def _build_procurement_event(
    data: dict[str, Any],
    event_type: ProcurementEventType,
) -> ProcurementEvent | None:
    """Build a ProcurementEvent from Pub/Sub message data.

    Args:
        data: Decoded message data.
        event_type: The event type.

    Returns:
        ProcurementEvent or None if invalid.
    """
    from lightspeed_agent.marketplace.models import (
        AccountInfo,
        EntitlementInfo,
        ProcurementEvent,
    )

    settings = get_settings()

    # Extract common fields
    event_id = data.get("eventId", data.get("id", "unknown"))
    provider_id = data.get("providerId", settings.service_control_service_name or "")

    # Extract account info (multiple possible locations)
    account_data = data.get("account", {})
    account_id = (
        account_data.get("id")
        or account_data.get("name", "").split("/")[-1]
        or data.get("accountId")
        or data.get("account_id")
    )

    account_info = None
    if account_id:
        account_info = AccountInfo(
            id=account_id,
            update_time=account_data.get("updateTime"),  # type: ignore[call-arg]
        )

    # Extract entitlement info (multiple possible locations)
    entitlement_data = data.get("entitlement", {})
    entitlement_id = (
        entitlement_data.get("id")
        or entitlement_data.get("name", "").split("/")[-1]
        or data.get("entitlementId")
        or data.get("entitlement_id")
        or data.get("orderId")
        or data.get("order_id")
    )

    entitlement_info = None
    if entitlement_id:
        entitlement_info = EntitlementInfo(  # type: ignore[call-arg]
            id=entitlement_id,
            new_plan=entitlement_data.get("newPlan") or entitlement_data.get("plan"),
            previous_plan=entitlement_data.get("previousPlan"),
            new_offer_start_time=entitlement_data.get("newOfferStartTime"),
            new_offer_end_time=entitlement_data.get("newOfferEndTime"),
            cancellation_reason=entitlement_data.get("cancellationReason"),
            update_time=entitlement_data.get("updateTime"),
        )

    return ProcurementEvent(  # type: ignore[call-arg]
        event_id=event_id,
        event_type=event_type,
        provider_id=provider_id,
        account=account_info,
        entitlement=entitlement_info,
    )
