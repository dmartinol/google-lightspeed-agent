# Configuration Reference

This document describes all configuration options for the Lightspeed Agent.

## Environment Variables

Configuration is managed through environment variables. Copy `.env.example` to `.env` and customize for your environment.

### Google AI / Gemini

| Variable | Default | Description |
|----------|---------|-------------|
| `GOOGLE_GENAI_USE_VERTEXAI` | `FALSE` | Use Vertex AI instead of Google AI Studio |
| `GOOGLE_API_KEY` | - | Google AI Studio API key (required if not using Vertex AI) |
| `GOOGLE_CLOUD_PROJECT` | - | GCP project ID (required for Vertex AI) |
| `GOOGLE_CLOUD_LOCATION` | `us-central1` | GCP region for Vertex AI |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model to use |

**Using Google AI Studio:**

```bash
GOOGLE_GENAI_USE_VERTEXAI=FALSE
GOOGLE_API_KEY=your-api-key
```

**Using Vertex AI:**

```bash
GOOGLE_GENAI_USE_VERTEXAI=TRUE
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=us-central1
```

### Red Hat SSO / OAuth 2.0

| Variable | Default | Description |
|----------|---------|-------------|
| `RED_HAT_SSO_ISSUER` | `https://sso.redhat.com/auth/realms/redhat-external` | SSO issuer URL |
| `RED_HAT_SSO_CLIENT_ID` | - | Resource Server client ID (used for token introspection) |
| `RED_HAT_SSO_CLIENT_SECRET` | - | Resource Server client secret |
| `AGENT_REQUIRED_SCOPE` | `api.console,api.ocm` | Comma-separated OAuth scopes required in access tokens |
| `AGENT_ALLOWED_SCOPES` | `openid,profile,email,api.console,api.ocm` | Comma-separated allowlist of permitted scopes. Tokens with scopes outside this list are rejected (403). |

**Example:**

```bash
RED_HAT_SSO_ISSUER=https://sso.redhat.com/auth/realms/redhat-external
RED_HAT_SSO_CLIENT_ID=my-client-id
RED_HAT_SSO_CLIENT_SECRET=my-client-secret
```

### Red Hat Lightspeed MCP

The MCP server runs as a sidecar container and provides tools for accessing Red Hat Insights APIs. The agent forwards the caller's JWT token to the MCP server, which uses it to authenticate with console.redhat.com on behalf of the user. See [MCP Integration](mcp-integration.md) for details.

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_TRANSPORT_MODE` | `http` | MCP transport: `stdio`, `http`, or `sse` |
| `MCP_SERVER_URL` | `http://localhost:8080` | MCP server URL (use 8081 for Podman to avoid A2A Inspector conflict) |
| `MCP_READ_ONLY` | `true` | Enable read-only mode for MCP tools |

**Development (stdio mode):**

```bash
# Agent spawns MCP server as subprocess
MCP_TRANSPORT_MODE=stdio
MCP_READ_ONLY=true
```

**Production (http mode with sidecar):**

```bash
# Agent connects to MCP server sidecar via HTTP
MCP_TRANSPORT_MODE=http
MCP_SERVER_URL=http://localhost:8081  # Use 8081 for Podman (8080 for Cloud Run)
MCP_READ_ONLY=true
```

### Agent Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_PROVIDER_URL` | `https://localhost:8000` | Agent base URL (where the A2A agent can be reached) |
| `AGENT_PROVIDER_ORGANIZATION_URL` | `https://www.redhat.com` | Provider's organization website URL. Used in AgentCard `provider.url` and as the expected JWT audience for Google DCR |
| `AGENT_NAME` | `lightspeed_agent` | Agent name |
| `AGENT_DESCRIPTION` | Red Hat Lightspeed Agent for Google Cloud | Agent description |
| `AGENT_HOST` | `0.0.0.0` | Server bind address |
| `AGENT_PORT` | `8000` | Server port |
| `GUARDRAIL_ORG_ARGS_ENABLED` | `true` | Turns on [tool org guardrails](api.md#tool-guardrails-organization-arguments). Set `false` only for emergency debugging. Wired in the same deploy surfaces as `AGENT_HOST` / `AGENT_PORT` ([`Containerfile`](../Containerfile), [`deploy/cloudrun/service.yaml`](../deploy/cloudrun/service.yaml), [`deploy/podman/lightspeed-agent-configmap.yaml`](../deploy/podman/lightspeed-agent-configmap.yaml), [`cloudbuild.yaml`](../cloudbuild.yaml)). |

**Example:**

```bash
AGENT_PROVIDER_URL=https://lightspeed-agent.example.com
AGENT_NAME=lightspeed_agent
AGENT_HOST=0.0.0.0
AGENT_PORT=8000
GUARDRAIL_ORG_ARGS_ENABLED=true
```

### Database

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite+aiosqlite:///./lightspeed_agent.db` | Marketplace database connection URL (orders, DCR clients, auth) |
| `SESSION_BACKEND` | `memory` | Session storage backend: `memory` (in-memory, no persistence) or `database` (PostgreSQL, persistent) |
| `SESSION_DATABASE_URL` | *(empty)* | Session database URL for ADK sessions. Required when `SESSION_BACKEND=database`. |

> **Note:** Setting `SESSION_BACKEND=database` without providing `SESSION_DATABASE_URL`
> will cause a startup validation error. This is intentional to prevent running
> production workloads without session persistence.

**SQLite (Development):**

```bash
DATABASE_URL=sqlite+aiosqlite:///./lightspeed_agent.db
```

**PostgreSQL (Production):**

```bash
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/lightspeed_agent
```

**Cloud SQL (GCP):**

```bash
DATABASE_URL=postgresql+asyncpg://user:password@/lightspeed_agent?host=/cloudsql/project:region:instance
```

**Security Isolation (Recommended for Production):**

For production deployments, use `SESSION_BACKEND=database` with a separate database for agent sessions:

```bash
# Explicit database backend for sessions
SESSION_BACKEND=database

# Shared marketplace database (orders, DCR clients, auth data)
DATABASE_URL=postgresql+asyncpg://marketplace:pass@db:5432/marketplace

# Separate session database (ADK sessions only)
SESSION_DATABASE_URL=postgresql+asyncpg://sessions:pass@db:5432/sessions
```

This separation ensures:
- Agents only access session data, not marketplace/auth data
- Compromised agents can't access DCR credentials or order information
- Different retention policies can be applied to sessions vs. marketplace data

**Switching to In-Memory Sessions:**

To disable database session persistence on a running deployment (e.g., for debugging),
set `SESSION_BACKEND=memory` and redeploy:

- **Cloud Run:** Update the `SESSION_BACKEND` env var to `memory` and redeploy the service.
- **Podman:** Update `SESSION_BACKEND` in the ConfigMap to `memory` and restart the pod.

### Dynamic Client Registration (DCR)

DCR allows Google Cloud Marketplace customers to automatically register as OAuth clients.

| Variable | Default | Description |
|----------|---------|-------------|
| `DCR_ENABLED` | `true` | `true`: real DCR via GMA SSO API. `false`: accepts static `client_id`/`client_secret` from the DCR request body, validates them against the token endpoint, and stores them. |
| `GMA_CLIENT_ID` | - | Client ID for GMA SSO API (client_credentials grant with `api.iam.clients.gma` scope) |
| `GMA_CLIENT_SECRET` | - | Client secret for GMA SSO API |
| `GMA_API_BASE_URL` | `https://sso.redhat.com/auth/realms/redhat-external/apis/beta/acs/v1/` | GMA SSO API base URL |
| `DCR_ENCRYPTION_KEY` | - | Fernet key for encrypting stored client secrets |
| `DCR_CLIENT_NAME_PREFIX` | `gemini-order-` | Prefix for generated client names |

**Generate Encryption Key:**

```bash
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

**Example Configuration:**

```bash
DCR_ENABLED=true
GMA_CLIENT_ID=your-gma-client-id
GMA_CLIENT_SECRET=your-gma-client-secret
DCR_ENCRYPTION_KEY=your-generated-fernet-key
DCR_CLIENT_NAME_PREFIX=gemini-order-
```

See [Authentication - DCR](authentication.md#dynamic-client-registration-dcr) for detailed information on the DCR flow.

### Rate Limiting

Rate limiting uses a Redis-backed sliding window algorithm for distributed deployments.

| Variable | Default | Description |
|----------|---------|-------------|
| `RATE_LIMIT_REDIS_URL` | `redis://localhost:6379/0` | Redis URL used for rate limiting |
| `RATE_LIMIT_REDIS_TIMEOUT_MS` | `200` | Redis operation timeout in milliseconds |
| `RATE_LIMIT_KEY_PREFIX` | `lightspeed:ratelimit` | Prefix for Redis rate limit keys |
| `RATE_LIMIT_REQUESTS_PER_MINUTE` | `60` | Max requests per minute |
| `RATE_LIMIT_REQUESTS_PER_HOUR` | `1000` | Max requests per hour |

**Example:**

```bash
RATE_LIMIT_REDIS_URL=redis://localhost:6379/0
RATE_LIMIT_REDIS_TIMEOUT_MS=200
RATE_LIMIT_KEY_PREFIX=lightspeed:ratelimit
RATE_LIMIT_REQUESTS_PER_MINUTE=120
RATE_LIMIT_REQUESTS_PER_HOUR=2000
```

See [Rate Limiting](rate-limiting.md) for details on the sliding window algorithm.

### Google Cloud Service Control

| Variable | Default | Description |
|----------|---------|-------------|
| `SERVICE_CONTROL_SERVICE_NAME` | - | Service name for usage reporting |
| `GOOGLE_APPLICATION_CREDENTIALS` | - | Path to service account key file |

**Example:**

```bash
SERVICE_CONTROL_SERVICE_NAME=lightspeed-agent.endpoints.my-project.cloud.goog
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
```

### Usage Tracking

Usage tracking is built into the agent via the ADK plugin system. No configuration required for basic tracking.

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Set to `DEBUG` to see detailed usage logs |

See [Usage Tracking and Metering](metering.md) for details on the plugin system and how to extend it.

### Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Log level: DEBUG, INFO, WARNING, ERROR |
| `LOG_FORMAT` | `json` | Log format: `json` or `text` |
| `AGENT_LOGGING_DETAIL` | `basic` | Agent execution logging detail: `basic` or `detailed` |

**Example:**

```bash
LOG_LEVEL=DEBUG
LOG_FORMAT=text  # Human-readable for development
AGENT_LOGGING_DETAIL=detailed  # Include tool args/results in logs
```

#### Audit Logging

The `LOG_FORMAT` setting controls how log records are formatted:

- **`json`** (default) — Structured JSON output. Every log record automatically includes audit context fields (`user_id`, `org_id`, `order_id`, `request_id`). Recommended for production and Cloud Run, where Cloud Logging parses these fields for querying.
- **`text`** — Human-readable output (`timestamp - logger - level - message`). Audit context fields are **not** included in the log record. The agent execution plugin still embeds `user_id`, `org_id`, `order_id`, and `request_id` in the log message text, but they are not available as structured fields for filtering. Recommended for local development.

When `LOG_FORMAT=json`, every log record automatically includes audit context fields:

| Field | Source | Description |
|-------|--------|-------------|
| `user_id` | JWT `sub` claim | Authenticated user identifier |
| `org_id` | JWT `org_id` claim | Red Hat organization identifier |
| `order_id` | DCR client lookup | Google Cloud Marketplace order |
| `request_id` | Generated UUID4 | Per-request correlation ID |

These fields enable:
- **Request correlation** — all events in a single request share the same `request_id`
- **User audit** — filter by `user_id` to trace all actions by a specific user
- **Organization audit** — filter by `org_id` for organization-level auditing
- **Data lineage** — `tool_call_completed` events include `data_source=<mcp_tool>`, and `mcp_jwt_forwarded` events prove data was retrieved using the user's authorized JWT

Each agent lifecycle event is tagged with an `event_type` in the log message:

| Event Type | Description |
|------------|-------------|
| `request_authenticated` | User JWT validated, user_id and org_id extracted |
| `agent_run_started` | ADK agent invocation started |
| `agent_run_completed` | ADK agent invocation finished |
| `llm_call_started` | Gemini LLM call initiated |
| `llm_call_completed` | Gemini LLM call finished (includes token counts) |
| `tool_call_started` | MCP tool call initiated |
| `tool_call_completed` | MCP tool call finished (includes `data_source`) |
| `mcp_jwt_forwarded` | User JWT forwarded to MCP sidecar for Red Hat API auth |

**Example JSON log line:**

```json
{"time": "2025-01-15 10:30:45", "level": "INFO", "logger": "lightspeed_agent.api.a2a.logging_plugin", "message": "Tool call completed (event_type=tool_call_completed, tool=get_advisories, data_source=get_advisories, ...)", "user_id": "user-42", "org_id": "org-7", "order_id": "order-99", "request_id": "abc-123-def-456"}
```

On Cloud Run, these JSON logs are automatically parsed by Cloud Logging and can be queried with:

```bash
# Find all actions by a specific user
gcloud logging read 'jsonPayload.user_id="user-42"' --project=$GOOGLE_CLOUD_PROJECT

# Find all tool calls in a specific request
gcloud logging read 'jsonPayload.request_id="abc-123" AND jsonPayload.message=~"tool_call"' --project=$GOOGLE_CLOUD_PROJECT

# Audit all MCP data access for an organization
gcloud logging read 'jsonPayload.org_id="org-7" AND jsonPayload.message=~"mcp_jwt_forwarded"' --project=$GOOGLE_CLOUD_PROJECT
```

### Development Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `DEBUG` | `false` | Enable debug mode (exposes /docs) |
| `SKIP_JWT_VALIDATION` | `false` | Skip JWT validation (dev only!) |

**Development:**

```bash
DEBUG=true
SKIP_JWT_VALIDATION=true
LOG_LEVEL=DEBUG
LOG_FORMAT=text
AGENT_LOGGING_DETAIL=detailed
```

**Production:**

```bash
DEBUG=false
SKIP_JWT_VALIDATION=false
LOG_LEVEL=INFO
LOG_FORMAT=json
AGENT_LOGGING_DETAIL=basic
```

## Configuration Files

### .env.example

Complete template with all configuration options:

```bash
# Copy to .env and customize
cp .env.example .env
```

### pyproject.toml

Project metadata and dependencies. Modify to add/update dependencies:

```toml
[project]
dependencies = [
    "google-adk>=0.5.0",
    # Add more dependencies here
]
```

## Secret Management

### Local Development

Store secrets in `.env` file (not committed to git):

```bash
# .env
GOOGLE_API_KEY=your-api-key
RED_HAT_SSO_CLIENT_SECRET=your-secret
```

### Production (Google Secret Manager)

Create secrets:

```bash
echo -n "secret-value" | gcloud secrets create secret-name --data-file=-
```

Reference in Cloud Run:

```bash
gcloud run deploy service-name \
  --set-secrets="RED_HAT_SSO_CLIENT_SECRET=redhat-sso-client-secret:latest"
```

### Kubernetes

Use Kubernetes secrets:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: lightspeed-agent-secrets
type: Opaque
stringData:
  GOOGLE_API_KEY: your-api-key
  RED_HAT_SSO_CLIENT_SECRET: your-secret
```

## Configuration Validation

The agent validates configuration at startup:

```python
from lightspeed_agent.config import get_settings

settings = get_settings()
# Raises ValidationError if required fields missing
```

### Required Fields

These fields must be set for the agent to start:

- `GOOGLE_API_KEY` (if not using Vertex AI)
- `GOOGLE_CLOUD_PROJECT` (if using Vertex AI)

### Validation Errors

If configuration is invalid, the agent logs an error and exits:

```
ValidationError: 1 validation error for Settings
google_api_key
  Field required [type=missing, input_value={...}, input_type=dict]
```

## Environment-Specific Configuration

### Development

```bash
# .env.development
DEBUG=true
SKIP_JWT_VALIDATION=true
LOG_LEVEL=DEBUG
LOG_FORMAT=text
AGENT_LOGGING_DETAIL=detailed
DATABASE_URL=sqlite+aiosqlite:///./dev.db
SESSION_BACKEND=memory
```

### Staging

```bash
# .env.staging
DEBUG=false
SKIP_JWT_VALIDATION=false
LOG_LEVEL=INFO
LOG_FORMAT=json
DATABASE_URL=postgresql+asyncpg://user:pass@staging-db:5432/insights
SESSION_BACKEND=database
SESSION_DATABASE_URL=postgresql+asyncpg://sessions:pass@staging-db:5432/sessions
```

### Production

```bash
# Secrets managed via Secret Manager
DEBUG=false
SKIP_JWT_VALIDATION=false
LOG_LEVEL=INFO
LOG_FORMAT=json
SESSION_BACKEND=database
# DATABASE_URL and SESSION_DATABASE_URL from Secret Manager
```
