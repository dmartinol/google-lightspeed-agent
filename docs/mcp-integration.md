# Red Hat Lightspeed MCP Server Integration

This document explains how the Lightspeed Agent integrates with the Red Hat Lightspeed MCP server to access console.redhat.com APIs.

## Overview

The agent uses the [Red Hat Lightspeed MCP Server](https://github.com/RedHatInsights/insights-mcp) as a sidecar to access Red Hat Insights APIs. The MCP (Model Context Protocol) server provides tools that the agent can call to retrieve data from:

- **Advisor**: System configuration recommendations
- **Inventory**: Registered systems and host information
- **Vulnerability**: CVE data and security analysis
- **Remediations**: Playbook management and issue resolution
- **Patch**: System update information
- **Image Builder**: Custom RHEL image creation

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Deployment Pod                          │
│                                                                 │
│  ┌─────────────────────┐      ┌─────────────────────────────┐   │
│  │  Lightspeed Agent   │      │   Red Hat Lightspeed MCP    │   │
│  │                     │      │   Server                    │   │
│  │   ┌─────────────┐   │ HTTP │   ┌─────────────────────┐   │   │
│  │   │   Gemini    │   │◄────►│   │   MCP Tools         │   │   │
│  │   │   Model     │   │:8080 │   │   - advisor         │   │   │
│  │   └─────────────┘   │      │   │   - inventory       │   │   │
│  │          │          │      │   │   - vulnerability   │   │   │
│  │          ▼          │      │   │   - remediations    │   │   │
│  │   ┌─────────────┐   │      │   └─────────────────────┘   │   │
│  │   │  ADK Agent  │   │      │             │               │   │
│  │   └─────────────┘   │      │             │               │   │
│  │                     │      │             ▼               │   │
│  │   Port 8000         │      │   ┌─────────────────────┐   │   │
│  └─────────────────────┘      │   │   OAuth2 Client     │   │   │
│                               │   │   (Lightspeed)      │   │   │
│                               │   └──────────┬──────────┘   │   │
│                               │              │              │   │
│                               └──────────────┼──────────────┘   │
│                                              │                  │
└──────────────────────────────────────────────┼──────────────────┘
                                               │
                                               ▼
                                    ┌─────────────────────┐
                                    │  console.redhat.com │
                                    │                     │
                                    │  - Advisor API      │
                                    │  - Inventory API    │
                                    │  - Vulnerability API│
                                    │  - Remediations API │
                                    │  - Patch API        │
                                    │  - Image Builder API│
                                    └─────────────────────┘
```

## Credential Flow

### JWT Token Pass-Through

The agent forwards the caller's JWT token to the MCP server via the `Authorization: Bearer <token>` header. The MCP server uses this token to authenticate with console.redhat.com on behalf of the calling user.

### Environment Variables

The MCP server connection requires these environment variables on the agent:

| Variable | Description |
|----------|-------------|
| `MCP_TRANSPORT_MODE` | Transport mode: `stdio`, `http`, or `sse` |
| `MCP_SERVER_URL` | MCP server URL (for http/sse modes) |
| `MCP_READ_ONLY` | Enable read-only mode (recommended: `true`) |

The MCP server itself requires:

| Variable | Description |
|----------|-------------|
| `MCP_SERVER_MODE` | Server mode: `http` for HTTP transport |
| `MCP_SERVER_PORT` | Port to listen on (default: 8080) |
| `READ_ONLY` | Enable read-only mode (recommended: `true`) |

### Authentication Flow

```
1. User sends request to Agent with Bearer token
   │
   ▼
2. Agent receives tool call request
   │
   ▼
3. Agent forwards caller's JWT token to MCP server
   │  via Authorization: Bearer header
   │
   ▼
4. MCP server calls console.redhat.com API with the token
   │
   ▼
5. Returns results to Agent
```

## Transport Modes

The agent can connect to the MCP server using different transport modes:

### HTTP Transport (Recommended for Production)

The MCP server runs as an HTTP server, and the agent connects to it.

**Podman deployment** (port 8081 to avoid A2A Inspector conflict):
```yaml
# Agent configuration
MCP_TRANSPORT_MODE: http
MCP_SERVER_URL: http://localhost:8081

# MCP server configuration
MCP_SERVER_MODE: http
MCP_SERVER_PORT: 8081
```

**Cloud Run deployment** (port 8080 for sidecar):
```yaml
# Agent configuration
MCP_TRANSPORT_MODE: http
MCP_SERVER_URL: http://localhost:8080

# MCP server configuration
MCP_SERVER_MODE: http
MCP_SERVER_PORT: 8080
```

### stdio Transport (Development)

The agent spawns the MCP server as a subprocess and communicates via stdin/stdout.

```yaml
MCP_TRANSPORT_MODE: stdio
```

This mode runs the MCP server container using podman:

```bash
podman run --interactive --rm ghcr.io/redhatinsights/red-hat-lightspeed-mcp:latest
```

## Deployment Configuration

### Podman Pod

The `lightspeed-agent-pod.yaml` includes the MCP server as a container:

```yaml
containers:
  - name: insights-mcp
    image: ghcr.io/redhatinsights/red-hat-lightspeed-mcp:latest
    env:
      - name: MCP_SERVER_MODE
        value: "http"
      - name: MCP_SERVER_PORT
        value: "8080"
    ports:
      - containerPort: 8080
```

### Cloud Run

The MCP server runs as a sidecar container in the Cloud Run service:

```yaml
containers:
  - name: lightspeed-agent
    # ... agent configuration ...
    env:
      - name: MCP_TRANSPORT_MODE
        value: "http"
      - name: MCP_SERVER_URL
        value: "http://localhost:8080"

  - name: insights-mcp
    image: ghcr.io/redhatinsights/red-hat-lightspeed-mcp:latest
```

## Available Tools

The MCP server provides these tools to the agent:

### Advisor Tools

| Tool | Description |
|------|-------------|
| `advisor_get_recommendations` | Get recommendations for a system |
| `advisor_list_rules` | List all advisor rules |
| `advisor_get_rule` | Get details of a specific rule |

### Inventory Tools

| Tool | Description |
|------|-------------|
| `inventory_list_hosts` | List registered hosts |
| `inventory_get_host` | Get host details by ID |
| `inventory_search_hosts` | Search hosts by criteria |

### Vulnerability Tools

| Tool | Description |
|------|-------------|
| `vulnerability_list_cves` | List CVEs affecting systems |
| `vulnerability_get_cve` | Get CVE details |
| `vulnerability_get_affected_systems` | Get systems affected by a CVE |

### Remediation Tools

| Tool | Description |
|------|-------------|
| `remediations_list` | List available remediations |
| `remediations_get` | Get remediation details |
| `remediations_create` | Create a new remediation (if not read-only) |

### Patch Tools

| Tool | Description |
|------|-------------|
| `patch_list_advisories` | List available patches |
| `patch_get_advisory` | Get patch details |
| `patch_get_systems` | Get systems needing patches |

## Troubleshooting

### MCP Server Not Responding

1. Check if the MCP server container is running:
   ```bash
   podman logs lightspeed-agent-pod-insights-mcp
   ```

2. Verify the health endpoint:
   ```bash
   curl http://localhost:8080/health
   ```

3. Check environment variables are set correctly

### Authentication Failures

1. Verify the caller's JWT token is valid and not expired
2. Check if the user has access to the required Insights services
3. Look for OAuth errors in MCP server logs:
   ```bash
   podman logs lightspeed-agent-pod-insights-mcp | grep -i auth
   ```

### API Errors

1. Check if you have access to the required Insights services
2. Verify your Red Hat account has the necessary subscriptions
3. Check the MCP server logs for API error responses

### Connection Refused

1. Ensure MCP server is listening on the correct port
2. Verify `MCP_SERVER_URL` in agent matches MCP server configuration
3. Check network connectivity between containers

## Security Considerations

1. **Token Security**: JWT tokens are forwarded per-request and not stored persistently
2. **Read-Only Mode**: Enable `READ_ONLY=true` to prevent write operations
3. **Network Isolation**: The MCP server only needs to reach console.redhat.com
4. **Least Privilege**: Ensure users have only the necessary permissions in console.redhat.com
