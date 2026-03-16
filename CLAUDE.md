# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Red Hat Lightspeed Agent for Google Cloud — an A2A-ready (Agent-to-Agent protocol) AI agent providing access to Red Hat Insights, built on Google Agent Development Kit (ADK), Gemini 2.5 Flash, and a Red Hat Lightspeed MCP server sidecar. Integrates with Google Cloud Marketplace for provisioning, billing, and metering.

## Common Commands

### Development

Always use a Python virtual environment — do not install dependencies system-wide.

```bash
python3.12 -m venv .venv              # Create virtual environment
source .venv/bin/activate             # Activate it
pip install -e ".[agent,dev]"         # Install all dependencies (agent + dev)
# Optional: pip install -e ".[agent,dev,telemetry]"  # Include Jaeger/Zipkin exporters
python -m lightspeed_agent.main       # Run the agent API server (port 8000)
```

### Testing

Ensure the virtual environment is activated before running tests.

```bash
make test                              # Run all tests
python -m pytest tests/ -v             # Equivalent direct command
python -m pytest tests/test_auth.py -v # Run a single test file
python -m pytest tests/test_auth.py::test_function_name -v  # Run a single test
```

Tests use in-memory SQLite and `SKIP_JWT_VALIDATION=true` (set automatically in `tests/conftest.py`). The test suite is async — `asyncio_mode = "auto"` is configured in pyproject.toml.

To run with coverage:
```bash
python -m pytest tests/ -v --cov=src/lightspeed_agent --cov-report=term-missing
```

### Linting & Type Checking
```bash
make lint                              # Run both ruff and mypy
ruff check src/ tests/                 # Linter only
mypy src/lightspeed_agent/ --ignore-missing-imports  # Type checker only
```

Ruff rules: `E, F, I, N, W, UP, B, C4, SIM`. Line length: 100. Target: Python 3.12.

### Container Build & Operations (Podman)
```bash
make help                              # List all available targets
make build                             # Build both images (agent + marketplace handler)
make build-agent                       # Build agent image only
make build-marketplace                 # Build marketplace handler image only
make run                               # Start all pods
make stop                              # Stop and remove pods
make logs                              # Agent logs
make logs-mcp                          # MCP server logs
make logs-all                          # All container logs
make status                            # Show pod/container status
make clean                             # Remove containers, images, and volumes
make clean-all                         # Full cleanup including volumes
make dev                               # Run agent locally without containers
make check-env                         # Validate required env vars
make cve-scan                          # Scan for CVEs with Trivy
```

### Before Pushing

Always run lint and tests before pushing commits:

```bash
make lint && make test
```

CI blocks merge on lint/test failures — catching issues locally saves round-trip time.

## Architecture

### Two-Service Design

The system runs as two separate FastAPI services with separate concerns:

1. **Lightspeed Agent** (port 8000, `src/lightspeed_agent/main.py`) — The AI agent service. Scales to zero on Cloud Run. Handles A2A protocol requests (JSON-RPC 2.0 at `/`), serves the AgentCard at `/.well-known/agent.json`. Uses ADK `LlmAgent` with MCP tools loaded from the sidecar.

2. **Marketplace Handler** (port 8001, `src/lightspeed_agent/marketplace/app.py`) — Always-on service for Google Cloud Marketplace Pub/Sub provisioning events and Dynamic Client Registration (DCR). Has a single hybrid `/dcr` endpoint that routes Pub/Sub messages vs DCR requests based on request content.

### Database Isolation

Two separate PostgreSQL databases (security boundary):
- **Marketplace DB** (`DATABASE_URL`) — accounts, entitlements, DCR clients, usage records. Shared by both services.
- **Session DB** (`SESSION_DATABASE_URL`) — ADK conversation sessions. Agent-only.

Both fall back to SQLite for development. ORM models are in `src/lightspeed_agent/db/models.py`.

### Authentication Flow

JWT tokens from Red Hat SSO flow through three layers:
1. `auth/middleware.py` validates Bearer tokens on POST `/` (skips health endpoints, OpenAPI docs, agent card paths, and `/marketplace/` prefixed routes)
2. Token is stored in `contextvars` for the request lifecycle
3. `tools/mcp_headers.py` forwards the caller's JWT to the MCP server so it can authenticate with console.redhat.com on the user's behalf

Setting `SKIP_JWT_VALIDATION=true` bypasses auth (dev only, blocked when running on Cloud Run).

### MCP Integration

The agent loads tools from a Red Hat Lightspeed MCP server running as a sidecar:
- Transport modes: `stdio` (dev), `http` (prod), `sse` (streaming) — configured via `MCP_TRANSPORT_MODE`
- Read-only mode (`MCP_READ_ONLY=true`) filters to a safe subset of tools
- Tool categories: Advisor, Inventory, Vulnerability, Remediations, Planning, Image Builder, Subscription Management, Content Sources
- MCP toolset creation is in `tools/insights_tools.py`; config in `tools/mcp_config.py`

### Key Middleware Stack (bottom to top)
1. CORS
2. JWT authentication (`auth/middleware.py`)
3. Redis rate limiting (`ratelimit/middleware.py`) — 60 req/min, 1000 req/hour

### DCR (Dynamic Client Registration)

Two modes controlled by `DCR_ENABLED`:
- **Real DCR** (`true`): Creates OAuth clients in Red Hat SSO (Keycloak) via `dcr/keycloak_client.py`
- **Static credentials** (`false`): Accepts pre-seeded client_id/secret in DCR request body

Client secrets are Fernet-encrypted at rest (`DCR_ENCRYPTION_KEY`).

### Usage Metering

`api/a2a/usage_plugin.py` hooks into ADK to track tokens/requests per LLM call. Hourly aggregates are stored in `UsageRecordModel` and async-reported to Google Cloud Service Control (`service_control/reporter.py`). Includes backfill for offline periods.

## Code Layout

```
src/lightspeed_agent/
├── api/app.py              # FastAPI app factory (lifespan, middleware, routes)
├── api/a2a/                # A2A protocol: routes, AgentCard, usage tracking
├── auth/                   # JWT validation middleware + token introspection
├── config/                 # Pydantic BaseSettings (all env vars, validation)
├── core/agent.py           # LlmAgent creation with MCP tools
├── db/                     # SQLAlchemy ORM (4 models, async engine)
├── dcr/                    # Dynamic Client Registration service
├── marketplace/            # Marketplace handler (separate service entry point)
├── metering/               # Usage record repository + backfill
├── ratelimit/              # Redis-backed distributed rate limiter
├── service_control/        # Google Cloud Service Control metering
├── telemetry/              # OpenTelemetry setup (OTLP, Jaeger, Zipkin)
├── tools/                  # MCP toolset definitions + JWT header forwarding
└── main.py                 # Agent service entry point
```

## Configuration

All configuration is via environment variables, managed through Pydantic settings in `config/settings.py`. See `.env.example` for the complete list (30+ vars). Key variables:

**LLM / Google Cloud:**
- `GOOGLE_API_KEY` or `GOOGLE_CLOUD_PROJECT` + `GOOGLE_GENAI_USE_VERTEXAI=TRUE` (LLM access)
- `GEMINI_MODEL` (model selection, default: `gemini-2.5-flash`)

**Database:**
- `DATABASE_URL` / `SESSION_DATABASE_URL` (PostgreSQL or SQLite)

**Auth:**
- `RED_HAT_SSO_CLIENT_ID` / `RED_HAT_SSO_CLIENT_SECRET`
- `SKIP_JWT_VALIDATION` (dev only)

**MCP:**
- `MCP_TRANSPORT_MODE`, `MCP_SERVER_URL`

**DCR:**
- `DCR_ENABLED`, `DCR_ENCRYPTION_KEY`

**Agent:**
- `AGENT_HOST`, `AGENT_PORT`

**Service Control:**
- `SERVICE_CONTROL_SERVICE_NAME`, `SERVICE_CONTROL_ENABLED`

**Rate Limiting:**
- `RATE_LIMIT_REDIS_URL`

**Observability:**
- `LOG_LEVEL`, `LOG_FORMAT`
- `OTEL_ENABLED`, `OTEL_EXPORTER_TYPE`, `OTEL_SERVICE_NAME`

## CI Pipeline

GitHub Actions (`.github/workflows/ci.yml`) runs on CentOS Stream 9 with Python 3.12:
1. **Lint** — ruff + mypy
2. **Test** — pytest
3. **Build** — Podman container build
4. **CI Gate** — blocks merge if any job fails

Secret scanning is configured via `.gitleaks.toml`.
