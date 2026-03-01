# Usage Tracking

This document describes the usage tracking system for monitoring API usage, token consumption, and tool invocations.

## Overview

The Lightspeed Agent uses the **ADK Plugin System** for usage tracking via the `UsageTrackingPlugin`. This approach integrates directly with the agent's execution lifecycle, providing accurate metrics without external dependencies.

All usage tracking is handled by the `UsageTrackingPlugin` in `src/lightspeed_agent/api/a2a/usage_plugin.py`. There is no separate metering middleware - the plugin captures all metrics directly from ADK callbacks.

### What's Tracked

| Metric | Description | Source |
|--------|-------------|--------|
| `total_requests` | A2A requests processed | `before_run_callback` |
| `total_input_tokens` | LLM prompt tokens | `after_model_callback` |
| `total_output_tokens` | LLM response tokens | `after_model_callback` |
| `total_tokens` | Combined token count | Computed |
| `total_tool_calls` | MCP tool invocations | `after_tool_callback` |

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           A2A Request                                   │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           ADK Runner                                    │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    UsageTrackingPlugin                          │    │
│  │                                                                 │    │
│  │  before_run_callback ────► Increment request counter            │    │
│  │                                                                 │    │
│  │  after_model_callback ───► Extract token counts from response   │    │
│  │                                                                 │    │
│  │  after_tool_callback ────► Increment tool call counter          │    │
│  │                                                                 │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                    │                                    │
│                                    ▼                                    │
│                    UsageRepository (DB-backed, per-order)                │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         GET /usage endpoint                             │
└─────────────────────────────────────────────────────────────────────────┘
```

## ADK Plugin System

The Google Agent Development Kit (ADK) provides a powerful plugin system that allows you to observe and customize agent behavior at every stage of execution. The `UsageTrackingPlugin` uses this system to track metrics.

### Plugin Lifecycle Callbacks

ADK plugins can implement these callbacks:

| Callback | When It Runs | Use Case |
|----------|--------------|----------|
| `before_run_callback` | Start of agent execution | Request counting, context setup |
| `after_run_callback` | End of agent execution | Cleanup, final metrics |
| `before_model_callback` | Before LLM call | Request modification |
| `after_model_callback` | After LLM response | Token tracking, response modification |
| `on_model_error_callback` | On LLM error | Error tracking |
| `before_tool_callback` | Before tool execution | Tool call logging |
| `after_tool_callback` | After tool execution | Tool usage tracking |
| `on_tool_error_callback` | On tool error | Error tracking |
| `before_agent_callback` | Before sub-agent call | Sub-agent tracking |
| `after_agent_callback` | After sub-agent call | Sub-agent metrics |

### Plugin Registration

Plugins are registered when creating the ADK `App`:

```python
from google.adk.apps import App
from google.adk.plugins.base_plugin import BasePlugin

class UsageTrackingPlugin(BasePlugin):
    def __init__(self):
        super().__init__(name="usage_tracking")

    # ... callback implementations

# Register the plugin
app = App(
    name="lightspeed-agent",
    root_agent=agent,
    plugins=[UsageTrackingPlugin()],  # Plugin registered here
)
```

## UsageTrackingPlugin Implementation

The `UsageTrackingPlugin` (`src/lightspeed_agent/api/a2a/usage_plugin.py`) implements three callbacks:

### Request Counting

```python
async def before_run_callback(self, *, invocation_context) -> None:
    """Track request count at start of each run."""
    order_id = _resolve_order_id()
    if order_id:
        await self._persist_increment(order_id=order_id, request_count=1)
    return None
```

This callback fires at the start of every A2A request, persisting a request increment for the current order.

### Token Tracking

```python
async def after_model_callback(
    self,
    *,
    callback_context,
    llm_response: LlmResponse,
) -> Optional[LlmResponse]:
    """Track token usage from LLM responses."""
    if llm_response.usage_metadata and (order_id := _resolve_order_id()):
        usage = llm_response.usage_metadata
        input_tokens = getattr(usage, "prompt_token_count", 0) or 0
        output_tokens = getattr(usage, "candidates_token_count", 0) or 0
        await self._persist_increment(
            order_id=order_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    return None  # Don't modify the response
```

This callback fires after every LLM call. The `usage_metadata` object contains:
- `prompt_token_count`: Tokens in the prompt (input)
- `candidates_token_count`: Tokens in the response (output)
- `total_token_count`: Combined count
- `thoughts_token_count`: Reasoning tokens (for thinking models)

### Tool Call Tracking

```python
async def after_tool_callback(
    self,
    *,
    tool: BaseTool,
    tool_args: dict[str, Any],
    tool_context,
    result: dict,
) -> Optional[dict]:
    """Track tool/MCP calls."""
    if order_id := _resolve_order_id():
        await self._persist_increment(order_id=order_id, tool_calls=1)
    return None  # Don't modify the result
```

This callback fires after every MCP tool invocation, persisting a tool-call increment for the current order.

## Storage: UsageRepository

Usage data is persisted in the database via `UsageRepository` (`src/lightspeed_agent/metering/repository.py`):

- **Per-order, per-period**: Usage is stored per `order_id` and hourly time window
- **Atomic increments**: PostgreSQL uses `INSERT ... ON CONFLICT DO UPDATE` for concurrent-safe writes
- **Claim-then-report**: Rows are claimed for reporting, then marked reported or released on failure

Key methods:
- `increment_usage()`: Persist usage increments (called by `UsageTrackingPlugin`)
- `claim_unreported_rows_for_reporting()`: Atomically claim rows for Service Control reporting
- `mark_reported_by_ids()` / `release_claimed_rows()`: Mark reported or release on failure
- `get_usage_by_order()`: Aggregate totals by order (for GET /usage endpoint)

## API Endpoint

### GET /usage

Returns aggregate usage statistics.

**Authentication**: Not required

```bash
curl http://localhost:8000/usage
```

**Response:**

```json
{
  "status": "ok",
  "usage_by_order": {
    "order-123": {
      "total_input_tokens": 12345,
      "total_output_tokens": 45678,
      "total_tokens": 58023,
      "total_requests": 150,
      "total_tool_calls": 75
    }
  }
}
```

## Rate Limiting

The agent includes a separate Redis-backed rate limiter that works independently from usage tracking.

### Configuration

```bash
# Environment variables
RATE_LIMIT_REDIS_URL=redis://localhost:6379/0
RATE_LIMIT_REDIS_TIMEOUT_MS=200
RATE_LIMIT_KEY_PREFIX=lightspeed:ratelimit
RATE_LIMIT_REQUESTS_PER_MINUTE=60    # Max requests per minute
RATE_LIMIT_REQUESTS_PER_HOUR=1000    # Max requests per hour
```

### How It Works

The `RateLimitMiddleware` uses an atomic Redis + Lua sliding window algorithm:

1. Resolve principal dimensions for the request:
   - `order_id` (if available)
   - `user_id` (or `client_id` if `user_id` is missing) for authenticated requests
   - client IP only when no authenticated principal is available
2. For each principal dimension, remove expired timestamps from minute/hour Redis windows.
3. Atomically evaluate limits across all dimensions in a single Redis Lua execution.
4. If any dimension exceeds its limit, return HTTP `429` with `Retry-After`.
5. If all dimensions pass, record the request in all relevant Redis windows.

### Rate Limited Paths

Only the A2A endpoint is rate limited:

| Path | Description |
|------|-------------|
| `/` | A2A JSON-RPC endpoint |

### Rate Limit Response

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 60
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 0
Content-Type: application/json

{
  "error": "rate_limit_exceeded",
  "message": "Rate limit exceeded (per_minute)",
  "retry_after": 60
}
```

See [Rate Limiting](rate-limiting.md) for more details.

## Extending for Production

### Per-Tool Metrics

To track usage per tool, extend the `after_tool_callback` and call `_persist_increment` (which records `tool_calls=1` per order). For per-tool breakdown, you would need to extend the `usage_records` schema or add a separate tracking table.

### Database Persistence

Usage is persisted via `UsageRepository` and the `usage_records` table. See `src/lightspeed_agent/metering/repository.py` and `docs/troubleshooting.md` for migration and index setup.

### OpenTelemetry Integration

ADK has built-in OpenTelemetry support. Enable it for distributed tracing:

```bash
# Enable OTEL export to Google Cloud
adk run --otel_to_cloud agents/rh_lightspeed_agent
```

Or configure programmatically:

```python
from opentelemetry import trace
from opentelemetry.exporter.cloud_monitoring import CloudMonitoringMetricsExporter

# Export metrics to Cloud Monitoring
exporter = CloudMonitoringMetricsExporter(project_id="your-project")
```

### Google Cloud Service Control

The agent includes a Service Control integration for Google Cloud Marketplace billing in `src/lightspeed_agent/service_control/`. This module:

- Reports usage metrics to Google Cloud Service Control API
- Runs on a scheduled hourly basis (Google's minimum requirement)
- Handles retry logic for failed reports

The reporter uses per-order usage from `UsageRepository` (DB-backed). Each active marketplace order receives its own usage delta.

```python
# Enable Service Control reporting via environment variables
SERVICE_CONTROL_ENABLED=true
SERVICE_CONTROL_SERVICE_NAME=your-service-name.endpoints.your-project.cloud.goog
```

The `UsageReporter` computes usage deltas between reporting periods and maps them to Google-defined metric names:

| Internal Metric | Google Metric |
|-----------------|---------------|
| `total_requests` | `send_message_requests` |
| `total_input_tokens` | `input_tokens` |
| `total_output_tokens` | `output_tokens` |
| `total_tool_calls` | `mcp_tool_calls` |

### BigQuery Analytics

ADK provides a BigQuery Agent Analytics Plugin for detailed analytics:

```python
from google.adk.plugins import BigQueryAnalyticsPlugin

app = App(
    name="lightspeed-agent",
    root_agent=agent,
    plugins=[
        UsageTrackingPlugin(),
        BigQueryAnalyticsPlugin(
            project_id="your-project",
            dataset_id="agent_analytics",
        ),
    ],
)
```

## Capabilities

- **Per-order, DB persistence**: Usage is stored per order and hourly period in the database; multiple replicas share the same data
- **Atomic increments**: PostgreSQL uses `INSERT ... ON CONFLICT DO UPDATE` for concurrent-safe writes; safe for multi-worker deployments
- **Claim-then-report**: Rows are atomically claimed for reporting, then marked reported or released on failure; prevents double reporting
- **Historical data retained**: Reported rows remain in the database for audit
- **Service Control integration**: Hourly reporting to Google Cloud Service Control for marketplace billing (requests, tokens, tool calls)
- **Retry on failure**: Failed reports are queued and retried with configurable max attempts; rows are released on failure for re-claim on retry
- **Stale claim recovery**: Rows claimed by a crashed worker (never marked or released) are released at the start of each hourly run; threshold configurable via `METERING_STALE_CLAIM_MINUTES`
- **Automatic backfill**: Unreported periods (from scheduler downtime or stale releases) are reported on each hourly run; configurable via `METERING_BACKFILL_MAX_AGE_HOURS` (default 7 days) and `METERING_BACKFILL_LIMIT_PER_RUN` (default 20)
- **GET /usage endpoint**: Returns per-order aggregate totals (requests, tokens, tool calls)

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `METERING_STALE_CLAIM_MINUTES` | 15 | Release rows claimed longer than this (worker crash recovery) |
| `METERING_BACKFILL_MAX_AGE_HOURS` | 168 | Backfill only periods within this many hours (7 days) |
| `METERING_BACKFILL_LIMIT_PER_RUN` | 20 | Max unreported periods to process per backfill run |

## Testing

```bash
# Start the server
python -m lightspeed_agent.main

# Check initial usage
curl http://localhost:8000/usage

# Make A2A requests
curl -X POST http://localhost:8000/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer dev-token" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message/send",
    "id": 1,
    "params": {
      "message": {
        "role": "user",
        "parts": [{"type": "text", "text": "What systems have critical vulnerabilities?"}]
      }
    }
  }'

# Check updated usage
curl http://localhost:8000/usage
```

## References

- [ADK Plugins Documentation](https://google.github.io/adk-docs/plugins/)
- [ADK Callbacks Documentation](https://google.github.io/adk-docs/callbacks/)
- [ADK OpenTelemetry Integration](https://docs.cloud.google.com/stackdriver/docs/instrumentation/ai-agent-adk)
- [BigQuery Agent Analytics Plugin](https://codelabs.developers.google.com/adk-bigquery-agent-analytics-plugin)
