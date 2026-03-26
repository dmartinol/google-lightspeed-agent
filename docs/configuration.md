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

**Example:**

```bash
AGENT_PROVIDER_URL=https://lightspeed-agent.example.com
AGENT_NAME=lightspeed_agent
AGENT_HOST=0.0.0.0
AGENT_PORT=8000
```

### Database

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite+aiosqlite:///./lightspeed_agent.db` | Marketplace database connection URL (orders, DCR clients, auth) |
| `SESSION_DATABASE_URL` | (uses DATABASE_URL) | Session database URL for ADK sessions. Optional - for security isolation. |

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

**Security Isolation (Optional):**

For production deployments, you can use separate databases for marketplace data and agent sessions:

```bash
# Shared marketplace database (orders, DCR clients, auth data)
DATABASE_URL=postgresql+asyncpg://marketplace:pass@db:5432/marketplace

# Separate session database (ADK sessions only)
SESSION_DATABASE_URL=postgresql+asyncpg://sessions:pass@db:5432/sessions
```

This separation ensures:
- Agents only access session data, not marketplace/auth data
- Compromised agents can't access DCR credentials or order information
- Different retention policies can be applied to sessions vs. marketplace data

### Dynamic Client Registration (DCR)

DCR allows Google Cloud Marketplace customers to automatically register as OAuth clients.

| Variable | Default | Description |
|----------|---------|-------------|
| `DCR_ENABLED` | `true` | `true`: real DCR via Red Hat SSO (Keycloak). `false`: accepts static `client_id`/`client_secret` from the DCR request body, validates them against the token endpoint, and stores them. |
| `DCR_INITIAL_ACCESS_TOKEN` | - | Initial access token for Red Hat SSO DCR endpoint |
| `DCR_ENCRYPTION_KEY` | - | Fernet key for encrypting stored client secrets |
| `DCR_CLIENT_NAME_PREFIX` | `gemini-order-` | Prefix for generated client names |

**Generate Encryption Key:**

```bash
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

**Example Configuration:**

```bash
DCR_ENABLED=true
DCR_INITIAL_ACCESS_TOKEN=your-keycloak-initial-access-token
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
```

### Staging

```bash
# .env.staging
DEBUG=false
SKIP_JWT_VALIDATION=false
LOG_LEVEL=INFO
LOG_FORMAT=json
DATABASE_URL=postgresql+asyncpg://user:pass@staging-db:5432/insights
```

### Production

```bash
# Secrets managed via Secret Manager
DEBUG=false
SKIP_JWT_VALIDATION=false
LOG_LEVEL=INFO
LOG_FORMAT=json
# DATABASE_URL from Secret Manager
```
