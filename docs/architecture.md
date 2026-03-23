# Architecture

This document describes the architecture of the Red Hat Lightspeed Agent for Google Cloud.

## Overview

The Red Hat Lightspeed Agent for Google Cloud is an A2A-ready (Agent-to-Agent) service that provides AI-powered access to Red Hat Insights. It is built using Google's Agent Development Kit (ADK) and integrates with Red Hat's MCP (Model Context Protocol) server for Insights data access.

The system consists of **two separate services**:

1. **Marketplace Handler** - Always running service that handles provisioning and client registration
2. **Lightspeed Agent** - The AI agent that handles user interactions (deployed after provisioning)

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              Google Cloud Marketplace                           │
│                    (Gemini Enterprise / Procurement Events)                     │
└─────────────────────────────────────────────────────────────────────────────────┘
         │                                                    │
         │ Pub/Sub Events                                     │ DCR Request
         │ (Account/Entitlement)                              │ (software_statement)
         ▼                                                    ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          Marketplace Handler Service                            │
│                         (Cloud Run - Always Running)                            │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                           FastAPI Application                             │  │
│  │  ┌──────────────────────────────────────────────────────────────────────┐ │  │
│  │  │                    Hybrid /dcr Endpoint                              │ │  │
│  │  │  - Pub/Sub Events → Approve accounts and entitlements                 │ │  │
│  │  │  - DCR Requests → Create OAuth clients via Keycloak                  │ │  │
│  │  └──────────────────────────────────────────────────────────────────────┘ │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────┘
         │                                                    │
         │ Store                                              │ Create Client
         ▼                                                    ▼
┌─────────────────┐                                  ┌─────────────────────────┐
│   PostgreSQL    │                                  │    Red Hat SSO          │
│   Database      │◀──────────────────────────────▶│    (Keycloak)           │
│  - Accounts     │                                  │  - DCR Endpoint         │
│  - Entitlements │                                  │  - OIDC/OAuth           │
│  - DCR Clients  │                                  └─────────────────────────┘
└─────────────────┘
         ▲
         │ Read/Write
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           Lightspeed Agent Service                              │
│                  (Cloud Run - Deployed After Provisioning)                      │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                           FastAPI Application                             │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐                    │  │
│  │  │   A2A API   │  │ Agent Card  │  │  Health/Ready   │                    │  │
│  │  │     /       │  │ /.well-     │  │  /health        │                    │  │
│  │  │  (JSON-RPC) │  │  known/     │  │  /ready         │                    │  │
│  │  └──────┬──────┘  │  agent.json │  └─────────────────┘                    │  │
│  │         │         └─────────────┘                                         │  │
│  │         ▼                                                                 │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐      │  │
│  │  │                     Authentication Layer                        │      │  │
│  │  │              (JWT Validation via Red Hat SSO)                   │      │  │
│  │  └─────────────────────────────────────────────────────────────────┘      │  │
│  │                              │                                            │  │
│  │                              ▼                                            │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐      │  │
│  │  │                        Agent Core                               │      │  │
│  │  │                  (Google ADK + Gemini)                          │      │  │
│  │  └─────────────────────────────────────────────────────────────────┘      │  │
│  │                              │                                            │  │
│  │                              ▼                                            │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐      │  │
│  │  │                      MCP Sidecar                                │      │  │
│  │  │              (Red Hat Lightspeed MCP Server)                    │      │  │
│  │  └─────────────────────────────────────────────────────────────────┘      │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────┘
         │                    │
         ▼                    ▼
┌─────────────┐      ┌─────────────────────────┐
│   Gemini    │      │  Red Hat Insights APIs  │
│     API     │      │  (via MCP Server)       │
│  (Vertex)   │      │  - Advisor              │
└─────────────┘      │  - Vulnerability        │
                     │  - Patch                │
                     │  - Content              │
                     └─────────────────────────┘
```

## Two-Service Architecture

### Why Two Services?

The system is split into two services for important operational reasons:

| Service | Purpose | Lifecycle |
|---------|---------|-----------|
| **Marketplace Handler** | Handles provisioning and DCR | Always running (minScale=1) |
| **Lightspeed Agent** | AI agent for user queries | Deployed after provisioning |

1. **Marketplace Handler must be always running** to receive Pub/Sub events from Google Cloud Marketplace for account and entitlement approvals
2. **Agent can be deployed on-demand** after a customer has been provisioned
3. **Separation of concerns**: Provisioning logic is isolated from agent logic
4. **Independent scaling**: Handler scales for provisioning traffic, Agent scales for user traffic

## Components

### Marketplace Handler Service

A separate FastAPI application for provisioning, providing:

- **Hybrid /dcr Endpoint**: Single endpoint handling both:
  - Pub/Sub events (account and entitlement approvals, filtered by product)
  - DCR requests (OAuth client creation)
- **Health Endpoints**: Kubernetes-compatible health checks
- **Database Access**: PostgreSQL for persistent storage

### Lightspeed Agent Service

The main AI agent FastAPI application, providing:

- **A2A Endpoints**: Agent-to-Agent protocol implementation (JSON-RPC)
- **Agent Card**: `/.well-known/agent.json` with capabilities and DCR extension
- **Health Endpoints**: Kubernetes-compatible health and readiness checks

### Authentication Layer

Handles all authentication and authorization:

- **Token Introspection**: Validates tokens via Keycloak introspection endpoint (RFC 7662)
- **Scope Checking**: Checks for required `api.console` and `api.ocm` scopes
- **Bypass for Discovery**: `/.well-known/agent.json` is public per A2A spec

### Agent Core

The AI agent built with Google ADK:

- **Gemini Model**: Uses Gemini 2.5 Flash for natural language understanding
- **Tool Orchestration**: Manages tool calls to MCP server
- **Session Management**: Maintains conversation context

### MCP Sidecar

Runs as a sidecar container connecting to Red Hat Insights:

- **Tool Discovery**: Discovers available Insights tools
- **Tool Execution**: Executes tools and returns results
- **Authentication**: Handles service account authentication to Red Hat APIs

## Data Flow

### Flow 1: Marketplace Procurement (Async)

This flow happens when a customer purchases from Google Cloud Marketplace:

```
1. Customer purchases from Google Cloud Marketplace
2. Marketplace sends Pub/Sub event to Marketplace Handler
3. Handler receives POST /dcr with Pub/Sub message wrapper
4. Handler filters by product (SERVICE_CONTROL_SERVICE_NAME) — account events pass through
5. Handler extracts event type (ACCOUNT_CREATION_REQUESTED, ENTITLEMENT_CREATION_REQUESTED, etc.)
6. Handler calls Google Procurement API to approve account, then entitlement
7. Handler stores entitlement in PostgreSQL
8. Customer is now provisioned for the service
```

```
┌─────────────┐      ┌───────────────┐      ┌────────────────┐      ┌────────────┐
│  Customer   │────▶│   Marketplace │────▶│    Pub/Sub     │────▶│  Handler   │
│  Purchases  │      │   (Purchase)  │      │  (Event Push)  │      │  /dcr      │
└─────────────┘      └───────────────┘      └────────────────┘      └─────┬──────┘
                                                                          │
                                         ┌─────────────────┐              │
                                         │   PostgreSQL    │◀────────────┤
                                         │   (Store)       │              │
                                         └─────────────────┘              │
                                                                          ▼
                                         ┌─────────────────────────────────────┐
                                         │   Google Procurement API            │
                                         │   (Approve Entitlement)             │
                                         └─────────────────────────────────────┘
```

### Flow 2: Dynamic Client Registration (Sync)

This flow happens when an admin configures the agent in Gemini Enterprise:

```
1. Admin configures agent in Gemini Enterprise
2. Gemini sends POST /dcr with software_statement JWT
3. Handler validates Google's JWT signature
4. Handler verifies order_id matches a provisioned entitlement
5. Handler calls Red Hat SSO DCR to create OAuth client
6. Handler stores client mapping in PostgreSQL
7. Handler returns client_id, client_secret to Gemini
8. Gemini stores credentials for future OAuth flows
```

```
┌─────────────┐      ┌──────────────┐      ┌─────────────────┐      ┌────────────┐
│   Admin     │────▶│    Gemini    │────▶│   POST /dcr     │────▶│  Handler   │
│  Configures │      │  Enterprise  │      │ software_stmt   │      │  /dcr      │
└─────────────┘      └──────────────┘      └─────────────────┘      └─────┬──────┘
                                                                          │
                           ┌──────────────────────────────────────────────┤
                           │                                              │
                           ▼                                              ▼
                    ┌─────────────────┐                       ┌─────────────────┐
                    │   PostgreSQL    │                       │  Red Hat SSO    │
                    │   (Check Order) │                       │  (Create OAuth  │
                    │   (Store Client)│                       │   Client)       │
                    └─────────────────┘                       └─────────────────┘
```

### Flow 3: Client Authentication

Clients obtain access tokens directly from Red Hat SSO (Keycloak) using their
DCR-issued credentials. The agent does not participate in token issuance — it
acts purely as a Resource Server.

```
1. Client authenticates directly with Red Hat SSO (e.g., client_credentials grant)
2. Red Hat SSO issues access token with api.console and api.ocm scopes
3. Client uses the token for A2A requests to the agent
```

### Flow 4: User Query (A2A)

This flow handles actual user interactions with the agent:

```
1. User sends query to / endpoint (A2A JSON-RPC)
2. JWT token validated against Red Hat SSO
3. Query passed to Agent Core
4. Agent processes query with Gemini
5. Agent calls MCP tools as needed
6. MCP sidecar queries Red Hat Insights APIs
7. Results aggregated and returned to user
```

## Module Structure

```
src/lightspeed_agent/
├── api/                        # Agent API layer
│   ├── app.py                 # FastAPI application factory (Agent)
│   └── a2a/                   # A2A protocol
│       ├── router.py          # A2A JSON-RPC endpoints
│       └── agent_card.py      # AgentCard builder
├── auth/                       # Authentication (shared)
│   ├── introspection.py       # Token introspection (RFC 7662)
│   ├── middleware.py           # Auth middleware
│   ├── dependencies.py        # FastAPI dependencies
│   └── models.py              # Auth data models
├── config/                     # Configuration (shared)
│   └── settings.py            # Pydantic settings
├── core/                       # Agent core
│   └── agent.py               # ADK agent definition
├── db/                         # Database (shared)
│   ├── base.py                # SQLAlchemy engine and Base
│   └── models.py              # ORM models (accounts, entitlements, DCR clients, usage)
├── dcr/                        # Dynamic Client Registration
│   ├── google_jwt.py          # Google JWT validation
│   ├── keycloak_client.py     # Keycloak DCR API client
│   ├── models.py              # DCR Pydantic models
│   ├── repository.py          # PostgreSQL repository
│   └── service.py             # DCR business logic
├── marketplace/                # Marketplace Handler service
│   ├── app.py                 # Handler FastAPI app factory (port 8001)
│   ├── router.py              # Hybrid /dcr endpoint (Pub/Sub + DCR)
│   ├── models.py              # Marketplace Pydantic models
│   ├── repository.py          # PostgreSQL repositories
│   ├── service.py             # Procurement API integration
│   └── __main__.py            # Entry point: python -m lightspeed_agent.marketplace
└── tools/                      # MCP integration
    ├── mcp_config.py          # MCP server configuration
    ├── mcp_headers.py         # MCP auth headers
    ├── insights_tools.py      # Insights tool wrappers
    └── skills.py              # Agent skills definition
```

### Container Images

| Image | Service | Port | Purpose |
|-------|---------|------|---------|
| `lightspeed-agent` | Agent | 8000 | A2A protocol, user queries |
| `marketplace-handler` | Handler | 8001 | Pub/Sub events, DCR |
| `insights-mcp` | MCP Sidecar | 8081 | Red Hat Lightspeed tools |

## External Dependencies

| Service | Used By | Purpose | Required |
|---------|---------|---------|----------|
| Google Gemini | Agent | AI model for queries | Yes |
| Red Hat SSO | Both | User authentication, DCR | Yes |
| Red Hat Lightspeed MCP | Agent | Data access | Yes |
| PostgreSQL | Both | Data persistence | Yes (Production) |
| Google Cloud Pub/Sub | Handler | Marketplace events | Production |
| Google Procurement API | Handler | Entitlement approval, account validation | Production |
| Google Service Control | Agent | Usage reporting | Production |

## Scaling Considerations

### Horizontal Scaling

- Both services are stateless and can scale horizontally
- State stored in PostgreSQL (shared by both services)
- Rate limits enforced via Redis (shared across replicas)

### Service Scaling Requirements

| Service | Min Instances | Max Instances | Notes |
|---------|---------------|---------------|-------|
| Marketplace Handler | 1 | 5 | Always running for Pub/Sub |
| Lightspeed Agent | 0 | 10 | Scale to zero when idle |

### Resource Requirements

| Service | CPU | Memory | Notes |
|---------|-----|--------|-------|
| Marketplace Handler | 1 | 512Mi | Lightweight, event-driven |
| Lightspeed Agent | 2 | 2Gi | AI processing, MCP calls |
| MCP Sidecar | 0.5 | 256Mi | Red Hat Insights queries |

### Connection Pooling

- Database connections pooled via SQLAlchemy
- HTTP connections to external services pooled via httpx
- Both services share the same PostgreSQL database

## Security

### Authentication

- A2A query endpoints require valid Bearer token from Red Hat SSO
- Tokens validated via Keycloak introspection endpoint (RFC 7662)
- Required `api.console` and `api.ocm` scopes checked; returns 403 if missing

### Public Endpoints

Certain endpoints must be publicly accessible per A2A protocol:

| Service | Endpoint | Reason |
|---------|----------|--------|
| Agent | `/.well-known/agent.json` | A2A discovery (no auth per spec) |
| Handler | `/dcr` | Pub/Sub push and DCR requests |
| Handler | `/health` | Health checks |

Both services are deployed with `--allow-unauthenticated` on Cloud Run.
Authentication is enforced at the **application layer** via OAuth middleware.

### Authorization

- Scope-based access control for authenticated endpoints
- Client ID extracted for usage tracking
- Organization ID used for multi-tenancy
- DCR requests validated via Google JWT signature

### Secrets Management

- Secrets stored in environment variables
- Production uses Google Secret Manager
- No secrets in code or configuration files
- DCR encryption key protects stored client secrets

### Network Security

- HTTPS enforced in production
- CORS configured for allowed origins
- Rate limiting prevents abuse
- Pub/Sub verification via message signature

## Database Schema

The system uses PostgreSQL for persistence. For production deployments, the marketplace database (shared by both services) is separate from the session database (agent only).

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     Marketplace Database (Shared)                           │
│                                                                             │
│  ┌────────────────────┐  ┌────────────────────┐  ┌────────────────────┐     │
│  │ marketplace_       │  │ marketplace_       │  │ dcr_clients        │     │
│  │ accounts           │  │ entitlements       │  │                    │     │
│  │ - id               │  │ - id (order_id)    │  │ - client_id        │     │
│  │ - state            │  │ - account_id       │  │ - client_secret    │     │
│  │ - provider_id      │  │ - state            │  │ - order_id         │     │
│  └────────────────────┘  └────────────────────┘  └────────────────────┘     │
│                                                                             │
│  ┌────────────────────┐                                                     │
│  │ usage_records      │                                                     │
│  │ - order_id         │                                                     │
│  │ - tokens           │                                                     │
│  │ - reported         │                                                     │
│  └────────────────────┘                                                     │
│                                                                             │
│  Access: Marketplace Handler (read/write), Agent (read-only for validation) │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                     Session Database (Agent Only)                           │
│                                                                             │
│  ┌────────────────────┐  ┌────────────────────┐  ┌────────────────────┐     │
│  │ sessions           │  │ events             │  │ artifacts          │     │
│  │ - session_id       │  │ - event_id         │  │ - artifact_id      │     │
│  │ - user_id          │  │ - session_id       │  │ - session_id       │     │
│  │ - state            │  │ - content          │  │ - content          │     │
│  └────────────────────┘  └────────────────────┘  └────────────────────┘     │
│                                                                             │
│  Access: Agent only (read/write)                                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

| Variable | Service | Description |
|----------|---------|-------------|
| `DATABASE_URL` | Both | Marketplace database (accounts, orders, DCR clients) |
| `SESSION_DATABASE_URL` | Agent | Session database (ADK sessions). If empty, uses `DATABASE_URL` |

## Architecture Decision Records

### ADR-1: Real DCR with Red Hat SSO (Keycloak)

**Status**: Accepted

**Context**: Google Cloud Marketplace requires agents to implement DCR (RFC 7591) to create OAuth client credentials for each marketplace order. Options considered: (1) return tracking credentials without creating real OAuth clients, or (2) create actual OAuth clients in Red Hat SSO via its DCR API.

**Decision**: Implement real DCR with Red Hat SSO (Keycloak). Each order gets a real, functioning OAuth client with proper OAuth 2.0 flow and per-order isolation.

**Consequences**: Requires DCR to be enabled on the Red Hat SSO realm and an Initial Access Token from the admin. More complex setup but more robust architecture.

### ADR-2: PostgreSQL for Persistence

**Status**: Accepted

**Context**: Marketplace accounts, entitlements, DCR clients, and usage records need durable storage that survives container restarts and supports horizontal scaling.

**Decision**: Use PostgreSQL with SQLAlchemy async for all persistence.

**Consequences**: Adds SQLAlchemy and asyncpg dependencies. Enables horizontal scaling (multiple instances share state) and provides durability and auditability.

### ADR-3: Configurable DCR Mode

**Status**: Accepted

**Context**: Not all deployments have DCR enabled on Red Hat SSO, and development/testing environments may not need real DCR.

**Decision**: Make DCR mode configurable via `DCR_ENABLED`. When `true` (default), real OAuth clients are created in Keycloak. When `false`, static credentials from environment variables are returned.

**Consequences**: Two code paths to maintain. Clear documentation needed for each mode. See [Authentication](authentication.md#dynamic-client-registration-dcr) for details.
