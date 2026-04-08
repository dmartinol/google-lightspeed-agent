# API Reference

This document describes the API endpoints provided by the Lightspeed Agent.

## Architecture Overview

The Lightspeed Agent is built using [Google ADK](https://github.com/google/adk-python) (Agent Development Kit)
with the [A2A protocol](https://google.github.io/A2A/) (Agent-to-Agent) for interoperability.

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              FastAPI Application                            │
│                                   (app.py)                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│  Middleware Stack (applied in reverse order)                                │
│  ┌─────────────┐  ┌──────────────────┐  ┌─────────────────┐                 │
│  │    CORS     │→ │ Authentication   │→ │  RateLimiting   │                 │
│  └─────────────┘  └──────────────────┘  └─────────────────┘                 │
├─────────────────────────────────────────────────────────────────────────────┤
│  Routers                                                                    │
│  ┌──────────────────┐ ┌─────────────┐ ┌────────────────┐                    │
│  │  A2A Protocol    │ │     DCR     │ │  Marketplace   │                    │
│  │  (a2a_setup.py)  │ │   (dcr/)    │ │ (marketplace/) │                    │
│  │  POST /          │ │             │ │                │                    │
│  └────────┬─────────┘ └─────────────┘ └────────────────┘                    │
└───────────│─────────────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           A2A Protocol Layer                                │
│                           (from a2a-sdk)                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                     A2AFastAPIApplication                           │    │
│  │  Routes:                                                            │    │
│  │  • GET  /.well-known/agent.json  → AgentCard (no auth)              │    │
│  │  • POST /                        → JSON-RPC 2.0 endpoint            │    │
│  └───────────────────────────────┬─────────────────────────────────────┘    │
│                                  │                                          │
│  ┌───────────────────────────────▼─────────────────────────────────────┐    │
│  │                    DefaultRequestHandler                            │    │
│  │  • Parses JSON-RPC requests (message/send, message/stream, etc.)    │    │
│  │  • Manages SSE streaming for message/stream                         │    │
│  │  • Routes to agent executor                                         │    │
│  └───────────────────────────────┬─────────────────────────────────────┘    │
│                                  │                                          │
│  ┌───────────────────────────────▼─────────────────────────────────────┐    │
│  │                     InMemoryTaskStore                               │    │
│  │  • Stores task state (submitted, working, completed, failed)        │    │
│  │  • Enables task retrieval via tasks/get, tasks/cancel               │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           ADK Agent Layer                                   │
│                           (from google-adk)                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                      A2aAgentExecutor                               │    │
│  │  Bridges A2A ←→ ADK with converters:                                │    │
│  │  • convert_a2a_request_to_adk_run_args (inbound)                    │    │
│  │  • convert_event_to_a2a_events (outbound)                           │    │
│  │  • convert_a2a_part_to_genai_part / convert_genai_part_to_a2a_part  │    │
│  └───────────────────────────────┬─────────────────────────────────────┘    │
│                                  │                                          │
│  ┌───────────────────────────────▼─────────────────────────────────────┐    │
│  │                           Runner                                    │    │
│  │  ┌─────────────────┐  ┌──────────────────┐  ┌───────────────────┐   │    │
│  │  │  SessionService │  │ ArtifactService  │  │   MemoryService   │   │    │
│  │  │ (Database/Mem)  │  │    (InMemory)    │  │    (InMemory)     │   │    │
│  │  └─────────────────┘  └──────────────────┘  └───────────────────┘   │    │
│  └───────────────────────────────┬─────────────────────────────────────┘    │
│                                  │                                          │
│  ┌───────────────────────────────▼─────────────────────────────────────┐    │
│  │                              App                                    │    │
│  │  Plugins:                                                           │    │
│  │  • UsageTrackingPlugin - tracks tokens, requests, tool calls        │    │
│  └───────────────────────────────┬─────────────────────────────────────┘    │
│                                  │                                          │
│  ┌───────────────────────────────▼─────────────────────────────────────┐    │
│  │                          LlmAgent                                   │    │
│  │                        (core/agent.py)                              │    │
│  │  • Model: Gemini 2.5 Flash (configurable)                           │    │
│  │  • Instructions: Red Hat Insights domain knowledge                  │    │
│  │  • Tools: MCP Toolset (Red Hat Insights API)                        │    │
│  └───────────────────────────────┬─────────────────────────────────────┘    │
└──────────────────────────────────│──────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           MCP Toolset                                       │
│                         (tools/insights_tools.py)                           │
├─────────────────────────────────────────────────────────────────────────────┤
│  Tools provided via Model Context Protocol (MCP):                           │
│  • Advisor    - System recommendations and configuration assessment         │
│  • Inventory  - System inventory management and queries                     │
│  • Vulnerability - CVE analysis and security scanning                       │
│  • Remediations  - Playbook creation and issue resolution                   │
│  • Planning   - RHEL upgrade and migration planning                         │
│  • Image Builder - Custom RHEL image creation                               │
│  • Subscriptions - Activation keys and subscription info                    │
│  • Content Sources - Repository management                                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Package | Responsibility |
|-----------|---------|----------------|
| `FastAPI` | fastapi | Web framework, routing, middleware |
| `A2AFastAPIApplication` | a2a-sdk | A2A protocol HTTP integration |
| `DefaultRequestHandler` | a2a-sdk | JSON-RPC parsing, SSE streaming |
| `InMemoryTaskStore` | a2a-sdk | Task state persistence |
| `A2aAgentExecutor` | google-adk | A2A ↔ ADK conversion bridge |
| `Runner` | google-adk | Agent execution orchestration |
| `App` | google-adk | Plugin management, agent container |
| `LlmAgent` | google-adk | LLM interaction, tool execution |
| `McpToolset` | google-adk | MCP server connection |

### Request Flow (message/send)

```
                           JSON-RPC Request
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  1. A2AFastAPIApplication receives POST /                                │
│     {"jsonrpc":"2.0","method":"message/send","params":{...},"id":"..."}  │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  2. DefaultRequestHandler parses JSON-RPC                                │
│     • Validates request format                                           │
│     • Extracts message from params                                       │
│     • Creates RequestContext with task_id, context_id                    │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  3. A2aAgentExecutor.execute() - INBOUND CONVERSION                      │
│     convert_a2a_request_to_adk_run_args():                               │
│     • A2A Message.parts → GenAI Content                                  │
│     • Extracts user_id, session_id from context                          │
│     • Creates run_config for ADK                                         │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  4. Runner.run_async() executes agent                                    │
│     • LlmAgent sends prompt to Gemini                                    │
│     • Gemini may call MCP tools (via McpToolset)                         │
│     • Yields ADK events (content chunks, tool calls, etc.)               │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  5. A2aAgentExecutor - OUTBOUND CONVERSION                               │
│     convert_event_to_a2a_events():                                       │
│     • ADK events → TaskStatusUpdateEvent, TaskArtifactUpdateEvent        │
│     • GenAI Content → A2A Message.parts                                  │
│     • Publishes to EventQueue                                            │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  6. Response returned                                                    │
│     message/send: Full JSON-RPC response with result                     │
│     message/stream: SSE stream of A2A events                             │
└──────────────────────────────────────────────────────────────────────────┘
```

### Class Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              a2a-sdk                                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────────────┐    ┌──────────────────────┐                       │
│  │ A2AFastAPIApplication│    │ DefaultRequestHandler│                       │
│  │ (server/apps.py)     │───→│ (server/request_     │                       │
│  │                      │    │  handlers.py)        │                       │
│  │ + add_routes_to_app()│    │                      │                       │
│  └──────────────────────┘    │ + handle_send()      │                       │
│                              │ + handle_stream()    │                       │
│                              └──────────┬───────────┘                       │
│                                         │                                   │
│  ┌──────────────────────────────────────┼──────────────────────────────┐    │
│  │ Types (a2a.types)                    │                              │    │
│  │ ┌──────────┐ ┌──────────┐ ┌──────────▼──┐ ┌────────────┐            │    │
│  │ │AgentCard │ │ Message  │ │AgentExecutor│ │InMemoryTask│            │    │
│  │ │          │ │          │ │ (Protocol)  │ │Store       │            │    │
│  │ │+name     │ │+role     │ │             │ │            │            │    │
│  │ │+skills[] │ │+parts[]  │ │+execute()   │ │+get_task() │            │    │
│  │ │+caps     │ │+message_ │ │+cancel()    │ │+save_task()│            │    │
│  │ └──────────┘ │ id       │ └─────▲───────┘ └────────────┘            │    │
│  │              └──────────┘       │                                   │    │
│  └─────────────────────────────────│───────────────────────────────────┘    │
└────────────────────────────────────│────────────────────────────────────────┘
                                     │ implements
┌────────────────────────────────────│────────────────────────────────────────┐
│                              google-adk                                     │
├────────────────────────────────────│────────────────────────────────────────┤
│                                    │                                        │
│  ┌─────────────────────────────────┴────────────────────────────────────┐   │
│  │                      A2aAgentExecutor                                │   │
│  │                  (a2a/executor/a2a_agent_executor.py)                │   │
│  │                                                                      │   │
│  │  + runner: Runner                                                    │   │
│  │  + execute(context, event_queue)                                     │   │
│  │  - _handle_request()                                                 │   │
│  │  - _prepare_session()                                                │   │
│  └───────────────────────────────────┬──────────────────────────────────┘   │
│                                      │ uses                                 │
│  ┌───────────────────────────────────▼──────────────────────────────────┐   │
│  │                           Runner                                     │   │
│  │                       (runners.py)                                   │   │
│  │                                                                      │   │
│  │  + app: App                                                          │   │
│  │  + session_service: SessionService                                   │   │
│  │  + artifact_service: ArtifactService                                 │   │
│  │  + memory_service: MemoryService                                     │   │
│  │  + run_async(**args) → AsyncGenerator[Event]                         │   │
│  └───────────────────────────────────┬──────────────────────────────────┘   │
│                                      │ contains                             │
│  ┌───────────────────────────────────▼──────────────────────────────────┐   │
│  │                              App                                     │   │
│  │                          (apps.py)                                   │   │
│  │                                                                      │   │
│  │  + name: str                                                         │   │
│  │  + root_agent: LlmAgent                                              │   │
│  │  + plugins: list[BasePlugin]                                         │   │
│  └───────────────────────────────────┬──────────────────────────────────┘   │
│                                      │ contains                             │
│  ┌───────────────────────────────────▼──────────────────────────────────┐   │
│  │                          LlmAgent                                    │   │
│  │                      (agents/llm_agent.py)                           │   │
│  │                                                                      │   │
│  │  + name: str                                                         │   │
│  │  + model: str (e.g., "gemini-2.5-flash")                             │   │
│  │  + instruction: str                                                  │   │
│  │  + tools: list[BaseTool | McpToolset]                                │   │
│  └───────────────────────────────────┬──────────────────────────────────┘   │
│                                      │ uses                                 │
│  ┌───────────────────────────────────▼──────────────────────────────────┐   │
│  │                         McpToolset                                   │   │
│  │                    (tools/mcp_tool/mcp_toolset.py)                   │   │
│  │                                                                      │   │
│  │  + connection_params: StdioServerParameters | SseServerParameters    │   │
│  │  + tool_filter: list[str] | None                                     │   │
│  │  + header_provider: callable | None                                  │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                     Converters (a2a/converters/)                     │   │
│  │  ┌────────────────────┐  ┌────────────────────┐                      │   │
│  │  │ request_converter  │  │ event_converter    │                      │   │
│  │  │                    │  │                    │                      │   │
│  │  │convert_a2a_request_│  │convert_event_to_   │                      │   │
│  │  │to_adk_run_args()   │  │a2a_events()        │                      │   │
│  │  └────────────────────┘  └────────────────────┘                      │   │
│  │  ┌────────────────────────────────────────────┐                      │   │
│  │  │ part_converter                             │                      │   │
│  │  │ convert_a2a_part_to_genai_part()           │                      │   │
│  │  │ convert_genai_part_to_a2a_part()           │                      │   │
│  │  └────────────────────────────────────────────┘                      │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │ connects to
                                      ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                       lightspeed_agent (this project)                        │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         core/agent.py                               │   │
│  │  create_agent() → LlmAgent                                          │   │
│  │  • Configures Gemini model                                          │   │
│  │  • Sets up MCP toolset with dynamic headers                         │   │
│  │  • Defines agent instructions                                       │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                            │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      api/a2a/a2a_setup.py                           │   │
│  │  setup_a2a_routes(app: FastAPI)                                     │   │
│  │  • Creates Runner with UsageTrackingPlugin                          │   │
│  │  • Wires A2aAgentExecutor → DefaultRequestHandler                   │   │
│  │  • Mounts A2AFastAPIApplication to FastAPI                          │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                            │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      api/a2a/agent_card.py                          │   │
│  │  build_agent_card() → AgentCard                                     │   │
│  │  • Defines skills from MCP tools                                    │   │
│  │  • Configures OAuth security scheme                                 │   │
│  │  • Adds DCR extension for marketplace                               │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                            │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      api/a2a/usage_plugin.py                        │   │
│  │  UsageTrackingPlugin (extends BasePlugin)                           │   │
│  │  • Tracks input/output tokens via after_model_callback              │   │
│  │  • Counts requests via before_run_callback                          │   │
│  │  • Counts tool calls via after_tool_callback                        │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

## Base URL

- **Local Development**: `http://localhost:8000`
- **Production**: Your Cloud Run service URL

## Authentication

Most endpoints require a valid JWT access token from Red Hat SSO. Include the token in the Authorization header:

```
Authorization: Bearer <access_token>
```

See [Authentication](authentication.md) for details on obtaining tokens.

> **Note**: The AgentCard advertises OAuth 2.0 security schemes for client discovery.
> Authentication enforcement on the A2A JSON-RPC endpoint should be implemented via
> middleware or request dependencies depending on deployment requirements.

### Tool guardrails (organization arguments)

The ADK `GuardrailPlugin` (enabled by default via `GUARDRAIL_ORG_ARGS_ENABLED`) runs on every MCP tool call. It only inspects arguments that use organization-style parameter names (e.g. `org_id`, `organization_id`, `rh_org_id`, matched case-insensitively with hyphen/underscore normalization). **If a call has no such parameters, the guardrail does nothing.** If it does, every extracted value must equal the authenticated request’s `org_id` from Red Hat SSO. **If the request has no `org_id` in context but the tool args include those fields, the call is blocked** (same error code). Otherwise the tool is short-circuited with a structured error (`code: guardrail_org_mismatch`) and is not sent to the MCP server. In development with `SKIP_JWT_VALIDATION` and a `Bearer` header, middleware typically sets `org_id` to `dev-org`, so the “missing org context” path is uncommon.

This complements MCP/API-side enforcement and mitigates cross-tenant argument injection from the model.

For the environment variable, defaults, and where it is set in images and deploy manifests, see [Configuration — Agent Configuration](configuration.md#agent-configuration).

## A2A Protocol Endpoints

The agent implements the [A2A (Agent-to-Agent) protocol](https://google.github.io/A2A/) for interoperability with other agents.

### GET /.well-known/agent.json

Returns the AgentCard describing the agent's capabilities.

**Authentication**: Not required

**Response:**

```json
{
  "name": "lightspeed-agent",
  "description": "Red Hat Lightspeed Agent for Google Cloud",
  "url": "https://your-agent-url.com",
  "version": "0.1.0",
  "provider": {
    "organization": "Red Hat",
    "url": "https://www.redhat.com"
  },
  "capabilities": {
    "streaming": true,
    "pushNotifications": false,
    "stateTransitionHistory": true
  },
  "authentication": {
    "schemes": [
      {
        "scheme": "oauth2",
        "authorizationUrl": "https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/auth",
        "tokenUrl": "https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token",
        "scopes": {
          "openid": "OpenID Connect",
          "profile": "User profile",
          "email": "Email address"
        }
      }
    ]
  },
  "defaultInputModes": ["text"],
  "defaultOutputModes": ["text"],
  "skills": [
    {
      "id": "advisor",
      "name": "System Advisor",
      "description": "Get system recommendations and configuration assessment"
    },
    {
      "id": "inventory",
      "name": "System Inventory",
      "description": "Manage and query system inventory"
    },
    {
      "id": "vulnerability",
      "name": "Vulnerability Analysis",
      "description": "Analyze security vulnerabilities and CVEs"
    }
  ]
}
```

### POST /

Send a message to the agent using JSON-RPC 2.0 format. This is the main A2A endpoint.

**Authentication**: Required

**Methods:**
- `message/send` - Send a message and get response
- `message/stream` - Send a message and get streaming response (SSE)

**Request:**

```json
{
  "jsonrpc": "2.0",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "parts": [
        {
          "type": "text",
          "text": "What systems have critical vulnerabilities?"
        }
      ]
    }
  },
  "id": "request-123"
}
```

**Response (Success):**

```json
{
  "jsonrpc": "2.0",
  "result": {
    "id": "task-456",
    "status": {
      "state": "completed"
    },
    "artifacts": [
      {
        "parts": [
          {
            "type": "text",
            "text": "I found 3 systems with critical vulnerabilities:\n\n1. server-01.example.com - CVE-2024-1234\n2. server-02.example.com - CVE-2024-5678\n3. database-01.example.com - CVE-2024-9012"
          }
        ]
      }
    ]
  },
  "id": "request-123"
}
```

**Response (Error):**

```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32600,
    "message": "Invalid Request",
    "data": "Missing required field: message"
  },
  "id": "request-123"
}
```

### POST / (Streaming)

For streaming responses, use the `message/stream` method. The response is Server-Sent Events (SSE).

**Request:**

```json
{
  "jsonrpc": "2.0",
  "method": "message/stream",
  "params": {
    "configuration": {
      "acceptedOutputModes": [],
      "blocking": true
    },
    "message": {
      "contextId": "2066124d-06fe-4c0c-8dbe-a4ea99bdf4e0",
      "kind": "message",
      "messageId": "e9c461b7-ff2f-4949-8759-214e9356d012",
      "parts": [
        {
          "kind": "text",
          "text": "What systems have critical vulnerabilities?"
        }
      ],
      "role": "user"
    }
  },
  "id": "request-123"
}
```

**Response:**

SSE stream with A2A events. The stream includes artifact chunks and a final status update:

**Artifact Chunk (first):**
```json
{
  "jsonrpc": "2.0",
  "id": "request-123",
  "result": {
    "kind": "artifact-update",
    "task_id": "3c503b3e-74f2-4825-9a84-31c7d6b64a18",
    "context_id": "2066124d-06fe-4c0c-8dbe-a4ea99bdf4e0",
    "artifact": {
      "artifact_id": "16172d78-faa5-4cbb-893e-f891512bfb0d",
      "parts": [
        {
          "kind": "text",
          "text": "I found the following systems with critical vulnerabilities:\n\n"
        }
      ]
    },
    "last_chunk": false
  }
}
```

**Artifact Chunk (last):**
```json
{
  "jsonrpc": "2.0",
  "id": "request-123",
  "result": {
    "kind": "artifact-update",
    "task_id": "3c503b3e-74f2-4825-9a84-31c7d6b64a18",
    "context_id": "2066124d-06fe-4c0c-8dbe-a4ea99bdf4e0",
    "artifact": {
      "artifact_id": "16172d78-faa5-4cbb-893e-f891512bfb0d",
      "parts": [
        {
          "kind": "text",
          "text": "1. server-01.example.com - CVE-2024-1234\n2. server-02.example.com - CVE-2024-5678"
        }
      ]
    },
    "last_chunk": true
  }
}
```

**Completion Event:**
```json
{
  "jsonrpc": "2.0",
  "id": "request-123",
  "result": {
    "kind": "status-update",
    "task_id": "3c503b3e-74f2-4825-9a84-31c7d6b64a18",
    "context_id": "2066124d-06fe-4c0c-8dbe-a4ea99bdf4e0",
    "status": {
      "state": "completed"
    },
    "final": true
  }
}
```

### GET /tasks/{task_id}

Get the status of a previously submitted task.

**Authentication**: Required

**Response:**

```json
{
  "id": "task-456",
  "status": {
    "state": "completed",
    "timestamp": "2024-01-15T10:30:00Z"
  },
  "artifacts": [
    {
      "parts": [
        {
          "type": "text",
          "text": "Task result..."
        }
      ]
    }
  ]
}
```

### DELETE /tasks/{task_id}

Cancel a running task.

**Authentication**: Required

**Response:**

```json
{
  "id": "task-456",
  "status": {
    "state": "canceled",
    "timestamp": "2024-01-15T10:31:00Z"
  }
}
```

## Dynamic Client Registration (DCR)

The system supports Dynamic Client Registration for Google Marketplace / Gemini Enterprise integration.

**Important**: DCR endpoints are on the **Marketplace Handler** service (port 8001), not the Agent service (port 8000).

### Endpoints

| Service | Endpoint | Description |
|---------|----------|-------------|
| Handler (8001) | `POST /dcr` | Hybrid endpoint for Pub/Sub and DCR requests |

The `/dcr` endpoint on the handler accepts both Pub/Sub procurement events and DCR registration requests, routing based on content.

### POST /dcr

Register a new OAuth client dynamically. Gemini Enterprise sends a signed JWT containing the order information.

**Authentication**: Signed `software_statement` JWT from Google

**Request:**

```json
{
  "software_statement": "eyJhbGciOiJSUzI1NiIsImtpZCI6Ii4uLiJ9..."
}
```

The `software_statement` JWT contains:

| Claim | Description |
|-------|-------------|
| `iss` | Google's certificate URL |
| `aud` | Agent's provider URL |
| `sub` | Procurement Account ID |
| `google.order` | Marketplace Order ID |
| `auth_app_redirect_uris` | Redirect URIs for OAuth flow |

**Response (Success):**

```json
{
  "client_id": "client_224a96f9-5b79-4b94-a8ea-c3bc3976a8e0",
  "client_secret": "generated-secret-here",
  "client_secret_expires_at": 0
}
```

**Important**: Per Google's specification, the same `client_id` and `client_secret` are returned for repeat requests with the same order ID. This allows Gemini Enterprise to invoke DCR multiple times for the same order.

**Security Note**: The handler validates that the `google.order` claim in the JWT exists in the database (was received via Pub/Sub procurement). This prevents registration of clients for orders not associated with this service.

**Response (Error):**

```json
{
  "error": "invalid_software_statement",
  "error_description": "JWT has expired"
}
```

### DCR Error Codes

| Error Code | Description |
|------------|-------------|
| `invalid_software_statement` | JWT is malformed, expired, or has invalid signature |
| `unapproved_software_statement` | Order ID or Account ID is not valid |
| `server_error` | Internal server error |

### Configuration

DCR requires an encryption key to securely store client secrets:

```bash
# Generate a Fernet encryption key
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'

# Set in environment
export DCR_ENCRYPTION_KEY="your-generated-key"
```

## Health Endpoints

### GET /health

Health check endpoint for load balancers and orchestrators.

**Authentication**: Not required

**Response:**

```json
{
  "status": "healthy",
  "agent": "lightspeed-agent"
}
```

### GET /ready

Readiness check endpoint indicating the service is ready to accept requests.

**Authentication**: Not required

**Response:**

```json
{
  "status": "ready",
  "agent": "lightspeed-agent"
}
```

## Error Codes

### HTTP Status Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 400 | Bad Request - Invalid input |
| 401 | Unauthorized - Missing or invalid authentication |
| 403 | Forbidden - Insufficient permissions |
| 404 | Not Found - Resource doesn't exist |
| 429 | Too Many Requests - Rate limit exceeded |
| 500 | Internal Server Error |
| 503 | Service Unavailable - Temporarily unavailable |

### JSON-RPC Error Codes

| Code | Message | Description |
|------|---------|-------------|
| -32700 | Parse error | Invalid JSON |
| -32600 | Invalid Request | Invalid JSON-RPC request |
| -32601 | Method not found | Unknown method |
| -32602 | Invalid params | Invalid method parameters |
| -32603 | Internal error | Internal server error |
| -32000 | Task not found | Referenced task doesn't exist |
| -32001 | Task canceled | Task was canceled |

## Rate Limiting

The API enforces global rate limits to prevent abuse:

| Limit | Value | Window |
|-------|-------|--------|
| Requests per minute | 60 | 1 minute |
| Requests per hour | 1000 | 1 hour |

When rate limited, the API returns:

```json
{
  "error": "rate_limit_exceeded",
  "message": "Rate limit exceeded (per_minute)",
  "retry_after": 60
}
```

With headers:

```
Retry-After: 60
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 0
```

## Examples

### Python

```python
import httpx

# Get access token (simplified - use OAuth flow in production)
token = "your-access-token"

# Send message to agent
response = httpx.post(
    "http://localhost:8000/",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "jsonrpc": "2.0",
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": "List my systems"}]
            }
        },
        "id": "1"
    }
)

result = response.json()
print(result["result"]["artifacts"][0]["parts"][0]["text"])
```

### curl

```bash
# Get AgentCard
curl http://localhost:8000/.well-known/agent.json

# Health check
curl http://localhost:8000/health

# Send message (with auth)
curl -X POST http://localhost:8000/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message/send",
    "params": {
      "message": {
        "role": "user",
        "parts": [{"type": "text", "text": "Show system recommendations"}]
      }
    },
    "id": "1"
  }'
```

### JavaScript

```javascript
const response = await fetch('http://localhost:8000/', {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    jsonrpc: '2.0',
    method: 'message/send',
    params: {
      message: {
        role: 'user',
        parts: [{ type: 'text', text: 'What CVEs affect my systems?' }]
      }
    },
    id: '1'
  })
});

const result = await response.json();
console.log(result.result.artifacts[0].parts[0].text);
```
