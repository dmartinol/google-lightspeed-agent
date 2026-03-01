# Google Cloud Marketplace Integration

This document describes the integration with Google Cloud Marketplace for commercial distribution of the Lightspeed Agent.

## Overview

The Lightspeed Agent integrates with Google Cloud Marketplace to enable:

- **Discovery**: Customers find the agent in the Marketplace catalog
- **Procurement**: Subscription management through Google billing
- **Authentication**: Dynamic Client Registration (DCR) for new subscribers
- **Usage Metering**: Automatic usage tracking and billing
- **Throttling**: Subscription-tier-based rate limiting

## Architecture

The system uses a **two-service architecture** to handle marketplace integration:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Google Cloud Marketplace                           │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐  │
│  │   Catalog &     │  │   Billing &     │  │   Pub/Sub Procurement       │  │
│  │   Discovery     │  │   Subscription  │  │   Notifications             │  │
│  └────────┬────────┘  └────────┬────────┘  └──────────────┬──────────────┘  │
└───────────┼────────────────────┼──────────────────────────┼─────────────────┘
            │                    │                          │
            ▼                    ▼                          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Marketplace Handler (Port 8001)                          │
│                    ─────────────────────────────────                        │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    Hybrid /dcr Endpoint                             │    │
│  │  - Pub/Sub Events → Approve accounts/entitlements                   │    │
│  │  - DCR Requests → Validate order, create OAuth clients via Keycloak │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                              │                                              │
│                              ▼                                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐  │
│  │   PostgreSQL    │  │   Red Hat SSO   │  │   Google Procurement API    │  │
│  │   (Orders, DCR) │  │   (Keycloak)    │  │   (Account Approval)        │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                                ▲
                                │ Shared PostgreSQL
                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                     Lightspeed Agent (Port 8000)                            │
│                      ──────────────────────────                             │
│  ┌─────────────────┐  ┌─────────────────┐                                   │
│  │   A2A Protocol  │  │  Usage Metering │                                   │
│  │   (JSON-RPC)    │  │   & Reporting   │                                   │
│  └─────────────────┘  └─────────────────┘                                   │
│                              │                                              │
│                              ▼                                              │
│                    ┌─────────────────┐                                      │
│                    │  Service Control│                                      │
│                    │  API Reporter   │                                      │
│                    └─────────────────┘                                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Service Responsibilities

| Service | Port | Purpose | Scaling |
|---------|------|---------|---------|
| **Marketplace Handler** | 8001 | Pub/Sub events, DCR, provisioning | Always on (minScale=1) |
| **Lightspeed Agent** | 8000 | A2A queries, user interactions | Scale to zero when idle |

### Deployment Order

1. **Deploy Marketplace Handler first** - Must be running to receive Pub/Sub events
2. **Deploy Agent after provisioning** - Can be deployed when customers are ready

## Dynamic Client Registration (DCR)

DCR allows Marketplace customers to automatically register as OAuth clients. The Marketplace Handler exposes a single `/dcr` endpoint that handles both procurement events and DCR requests:

| Request Type | Content | Handler Action |
|-------------|---------|----------------|
| Pub/Sub Event | `{"message": {"data": "..."}}` | Approve account/entitlement |
| DCR Request | `{"software_statement": "..."}` | Create OAuth client |

### AgentCard DCR Extension

The AgentCard (served by the Agent on port 8000) advertises DCR support and points to the Handler:

```json
{
  "capabilities": {
    "extensions": [
      {
        "uri": "urn:google:agent:dcr",
        "description": "Dynamic Client Registration for OAuth 2.0",
        "params": {
          "endpoint": "https://handler.example.com/oauth/register",
          "alternativeEndpoint": "https://handler.example.com/dcr",
          "supportedGrantTypes": ["authorization_code", "refresh_token"]
        }
      }
    ]
  }
}
```

For the complete DCR flow, JWT validation, Keycloak integration, security considerations, and local testing instructions, see [Authentication - DCR](authentication.md#dynamic-client-registration-dcr).

## Procurement Integration

### Pub/Sub Notifications

Marketplace sends procurement events via Pub/Sub:

**Event Types:**

| Event | Description |
|-------|-------------|
| `ACCOUNT_CREATION_REQUESTED` | New customer account |
| `ACCOUNT_ACTIVE` | Account approved and active |
| `ACCOUNT_DELETED` | Account deleted |
| `ENTITLEMENT_CREATION_REQUESTED` | New subscription request |
| `ENTITLEMENT_ACTIVE` | Subscription activated |
| `ENTITLEMENT_RENEWED` | Subscription renewed |
| `ENTITLEMENT_OFFER_ACCEPTED` | Offer auto-accepted |
| `ENTITLEMENT_PLAN_CHANGE_REQUESTED` | Plan upgrade/downgrade |
| `ENTITLEMENT_PLAN_CHANGED` | Plan change completed |
| `ENTITLEMENT_PLAN_CHANGE_CANCELLED` | Plan change cancelled |
| `ENTITLEMENT_PENDING_CANCELLATION` | Pending cancellation |
| `ENTITLEMENT_CANCELLATION_REVERTED` | Cancellation reverted |
| `ENTITLEMENT_CANCELLING` | Cancellation in progress |
| `ENTITLEMENT_CANCELLED` | Subscription cancelled |
| `ENTITLEMENT_DELETED` | Subscription deleted |
| `ENTITLEMENT_OFFER_ENDED` | Offer period ended |

**Message Format:**

```json
{
  "eventId": "evt_abc123",
  "eventType": "ENTITLEMENT_ACTIVE",
  "entitlement": {
    "id": "entitlements/abc123",
    "account": "accounts/user@example.com",
    "provider": "providers/lightspeed-agent",
    "product": "products/lightspeed-agent",
    "plan": "plans/professional",
    "state": "ENTITLEMENT_ACTIVE",
    "createTime": "2024-01-15T10:00:00Z"
  }
}
```

### Handling Entitlements

```python
# Example procurement handler
async def handle_procurement_event(message: dict):
    event_type = message["eventType"]
    entitlement = message["entitlement"]

    if event_type == "ENTITLEMENT_ACTIVE":
        # Activate subscription
        await activate_subscription(
            account=entitlement["account"],
            plan=entitlement["plan"]
        )
    elif event_type == "ENTITLEMENT_CANCELLED":
        # Deactivate subscription
        await deactivate_subscription(
            account=entitlement["account"]
        )
```

## Usage Metering

### Metrics Tracked

| Metric | Description | Unit |
|--------|-------------|------|
| `request_count` | Number of API requests | requests |
| `token_usage` | LLM tokens consumed | tokens |
| `tool_calls` | MCP tool invocations | calls |
| `compute_time` | Processing time | seconds |

### Reporting to Service Control

Usage is reported to Google Cloud Service Control API:

```python
from google.cloud import servicecontrol_v1

async def report_usage(
    consumer_id: str,
    operation_id: str,
    metrics: dict
):
    client = servicecontrol_v1.ServiceControllerAsyncClient()

    await client.report(
        service_name="lightspeed-agent.endpoints.project.cloud.goog",
        operations=[
            servicecontrol_v1.Operation(
                operation_id=operation_id,
                consumer_id=f"project:{consumer_id}",
                labels={"cloud.googleapis.com/location": "us-central1"},
                metric_value_sets=[
                    servicecontrol_v1.MetricValueSet(
                        metric_name="serviceruntime.googleapis.com/api/request_count",
                        metric_values=[
                            servicecontrol_v1.MetricValue(int64_value=metrics["requests"])
                        ]
                    )
                ]
            )
        ]
    )
```

### Reporting Interval

Usage is reported:
- **Real-time**: For critical operations (authentication, etc.)
- **Batched**: Every hour for general usage
- **On shutdown**: Flush remaining metrics

## Subscription Tiers

### Tier Configuration

| Tier | Requests/min | Requests/hour | Tokens/day | Features |
|------|-------------|---------------|------------|----------|
| Free | 10 | 100 | 10,000 | Basic queries |
| Professional | 60 | 1,000 | 100,000 | All features |
| Enterprise | 300 | 10,000 | 1,000,000 | Priority support |

### Rate Limit Enforcement

Rate limits are enforced using a Redis-backed sliding window algorithm:

```python
# Pseudocode for current middleware behavior
async def enforce_rate_limit(request):
    # Build principal dimensions from auth context
    principals = []
    if request.state.order_id:
        principals.append(f"order:{request.state.order_id}")
    if request.state.user and request.state.user.user_id:
        principals.append(f"user:{request.state.user.user_id}")
    elif request.state.user and request.state.user.client_id:
        principals.append(f"client:{request.state.user.client_id}")
    if not principals:
        principals.append(f"ip:{request.client.host}")

    # Atomically evaluate all dimensions in Redis + Lua
    allowed, status = await redis_rate_limiter.is_allowed(principal_keys=principals)
    if not allowed:
        return HTTP_429(status)

    return continue_request()
```

See [Rate Limiting](rate-limiting.md) for details on the sliding window algorithm.

## Setup Instructions

### 1. Enable Required APIs

```bash
gcloud services enable \
  cloudcommerceprocurement.googleapis.com \
  servicecontrol.googleapis.com \
  servicemanagement.googleapis.com \
  pubsub.googleapis.com
```

### 2. Create Pub/Sub Subscription

```bash
# Create topic for procurement events
gcloud pubsub topics create marketplace-entitlements

# Create push subscription to your service
gcloud pubsub subscriptions create marketplace-entitlements-sub \
  --topic=marketplace-entitlements \
  --push-endpoint=https://your-marketplace-handler.run.app/dcr
```

### 3. Configure Service Control

```bash
# Set service name
export SERVICE_CONTROL_SERVICE_NAME=lightspeed-agent.endpoints.PROJECT_ID.cloud.goog

# Grant service account permissions
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:lightspeed-agent@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/servicecontrol.serviceController"
```

### 4. Register with Marketplace

1. Go to [Cloud Partner Portal](https://console.cloud.google.com/partner)
2. Create a new product listing
3. Configure pricing plans
4. Set up procurement integration
5. Submit for review

## Testing

### Test DCR Locally

```bash
# Start the marketplace handler first (port 8001)
python -m lightspeed_agent.marketplace

# In another terminal, start the agent (port 8000)
python -m lightspeed_agent.main

# Test DCR endpoint on the handler (requires valid Google JWT)
# For local testing, you may need to mock the JWT validation
curl -X POST http://localhost:8001/dcr \
  -H "Content-Type: application/json" \
  -d '{
    "software_statement": "your-test-jwt"
  }'

# Test the agent health
curl http://localhost:8000/health
```

### Test Procurement Events

```bash
# Publish test event
gcloud pubsub topics publish marketplace-entitlements \
  --message='{
    "eventType": "ENTITLEMENT_ACTIVE",
    "entitlement": {
      "account": "test@example.com",
      "plan": "professional"
    }
  }'
```

### Test Usage Reporting

```bash
# View reported metrics
gcloud logging read \
  'resource.type="cloud_run_revision" AND textPayload:"usage_reported"' \
  --project=PROJECT_ID
```

## Troubleshooting

### DCR Failures

| Error | Cause | Solution |
|-------|-------|----------|
| 400 Invalid redirect_uri | URI not HTTPS | Use HTTPS URIs in production |
| 401 Unauthorized | Missing/invalid token | Check request authentication |
| 409 Client exists | Duplicate registration | Use existing credentials |

### Procurement Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| Missing events | Subscription not configured | Verify Pub/Sub subscription |
| Event processing failed | Handler error | Check logs for exceptions |
| Entitlement not found | Sync delay | Wait and retry |

### Usage Reporting Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| Metrics not appearing | Service account permissions | Grant servicecontrol.serviceController |
| Quota exceeded | Too many report calls | Batch metrics before reporting |
| Invalid operation | Malformed request | Validate operation structure |
