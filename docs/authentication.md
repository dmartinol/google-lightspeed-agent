# Authentication

This document describes the authentication mechanisms used by the Lightspeed Agent.

## Overview

The system uses three distinct authentication flows:

1. **Dynamic Client Registration (DCR)** -- Handler creates per-order OAuth clients in Red Hat SSO
2. **Token Introspection** -- Agent validates access tokens via Keycloak introspection endpoint (RFC 7662) and checks for `agent:insights` scope
3. **MCP JWT Pass-Through** -- Agent forwards the caller's JWT token to the MCP sidecar, which uses it to call console.redhat.com APIs on behalf of the user

Clients obtain access tokens directly from Red Hat SSO (Keycloak) using their DCR-issued credentials. The agent acts purely as a **Resource Server** — it validates incoming tokens but does not proxy or participate in the OAuth authorization flow.

## Authentication Architecture

```
 Google Cloud Marketplace                                           End User / Gemini
 (Gemini Enterprise)                                                   (Client App)
   |                  |                                                     |
   | 1. Pub/Sub       | 2. DCR Request                                      | 5. Obtain token
   |    event         |    (software_statement)                             |    directly from
   v                  v                                                     |    Red Hat SSO
+--------------------------------------------------------+                 |
|            Marketplace Handler (8001)                  |                  |
|                                                        |                  |
|  +---------------------------------------------------+ |                  |
|  |             Hybrid /dcr Endpoint                  | |                  |
|  |                                                   | |                  |
|  |  Pub/Sub path:         DCR path:                  | |                  |
|  |  - Decode msg          - Validate Google JWT  [3] | |                  |
|  |  - Filter by product   - Verify order in DB       | |                  |
|  |  - Approve entitlement - Create OAuth client  [4] | |                  |
|  |    via Procurement API - Return client_id +       | |                  |
|  |  - Store entitlement     client_secret            | |                  |
|  +---+--------------------+----------+---------------+ |                  |
|      |                    |          |                 |                  |
|      v                    |          v                 |                  |
|  +----------+             |   +-------------+          |                  |
|  |PostgreSQL|             |   | Red Hat SSO |          |                  |
|  |(accounts,|             |   | (Keycloak)  |          |                  |
|  | orders,  |             |   | DCR endpoint|          |                  |
|  | dcr      |             |   +-------------+          |                  |
|  | clients) |             |          ^                 |                  |
|  +----------+             |          |                 |                  |
+--------------------------------------------------------+                  |
                            |          |                                    |
                            v          |                                    v
                   +----------------+  |                      +---------------------------+
                   | 3. Fetch       |  |                      |  Lightspeed Agent (8000)  |
                   |    Google      |  |                      |                           |
                   |    X.509       |  |                      | +-------+                 |
                   |    certs       |  |                      | | Agent |                 |
                   +----------------+  |                      | | Card  |                 |
                                       |                      | +-------+                 |
                          +------------+                      |                           |
                          |                                   | 6. Validate token         |
                          |  Red Hat SSO (sso.redhat.com)     |    on every A2A request   |
                          |  +-----------------------------+  |   (introspect)            |
                          +->| - OIDC / OAuth2 provider    |  |                 v         |
                             | - Token introspection       |<---+  +---------+------+    |
                             | - DCR endpoint              |  | |  | A2A Endpoint   |    |
                             +-----------------------------+  | |  | POST /         |    |
                                                              | |  | (authenticated)|    |
                                                              | |  +--------+-------+    |
                                                              | |           |             |
                                                              | |           v             |
                                                              | |  +-----------------------------+
                                                              | |  |  7. MCP Tool Calls          |
                                                              | |  |     Authorization: Bearer   |
                                                              | |  |     (caller's JWT token)    |
                                                              | |  +-------------+---------------+
                                                              | |                |               |
                                                              | |                v               |
                                                              | |  +----------------------------+|
                                                              | +--| MCP Sidecar (8081)         ||
                                                              |    | 8. Calls APIs using the    ||
                                                              |    |    forwarded JWT token     ||
                                                              |    +-------------+--------------+|
                                                              |                  |               |
                                                              +---------------------------+-----+
                                                                                 |
                                                                                 v
                                                                    +------------------------+
                                                                    | console.redhat.com     |
                                                                    | 9. API calls with      |
                                                                    |    Bearer token        |
                                                                    | (Advisor, Inventory,   |
                                                                    |  Vulnerability, Patch) |
                                                                    +------------------------+
```

**Flow summary:**

| Step | Direction | Description |
|------|-----------|-------------|
| 1 | Google -> Handler | Pub/Sub procurement event (entitlement approval, filtered by product) |
| 2 | Google -> Handler | DCR request with `software_statement` JWT |
| 3 | Handler -> Google | Fetch X.509 certificates to validate JWT signature |
| 4 | Handler -> Red Hat SSO | Create OAuth client via Keycloak DCR endpoint |
| 5 | Client -> Red Hat SSO | Client obtains access token directly from Keycloak (e.g., `client_credentials` grant) |
| 6 | Agent -> Red Hat SSO | Introspect token on every A2A request; check `agent:insights` scope |
| 7 | Agent -> MCP Sidecar | Tool call with caller's JWT token in Authorization header |
| 8 | MCP Sidecar -> console.redhat.com | Call Insights APIs using the forwarded JWT token |

## Dynamic Client Registration (DCR)

DCR is handled by the **Marketplace Handler** service (port 8001). It creates per-order OAuth clients in Red Hat SSO so that each marketplace customer gets isolated credentials.

### How DCR Works

1. Admin configures the agent in Gemini Enterprise
2. Gemini sends `POST /dcr` to the Handler with a `software_statement` JWT signed by Google
3. Handler validates the JWT:
   - Fetches Google's X.509 certificates from the issuer URL
   - Verifies RS256 signature, expiration, and audience
   - Extracts `google.order` (order ID) and `sub` (account ID)
4. Handler verifies the order exists in the marketplace database (security check)
5. Handler calls Red Hat SSO's DCR endpoint to create an OAuth client
6. Handler stores the encrypted client credentials in PostgreSQL
7. Handler returns `{client_id, client_secret, client_secret_expires_at: 0}` to Gemini

For repeat requests with the same order ID, the same credentials are returned (idempotent).

### Software Statement JWT Claims

The `software_statement` JWT from Google contains:

| Claim | Description |
|-------|-------------|
| `iss` | Google certificate URL (for signature verification) |
| `aud` | Agent's provider URL (`AGENT_PROVIDER_URL`) |
| `sub` | Procurement Account ID |
| `google.order` | Marketplace Order ID (validated against database) |
| `auth_app_redirect_uris` | Redirect URIs for the OAuth client |
| `iat` / `exp` | Issued-at and expiration timestamps |

### DCR Modes

| Mode | Setting | Behavior |
|------|---------|----------|
| **Real DCR** | `DCR_ENABLED=true` (default) | Creates actual OAuth clients in Red Hat SSO via Keycloak DCR |
| **Static credentials** | `DCR_ENABLED=false` | Accepts `client_id` and `client_secret` from the DCR request body, validates them against the Red Hat SSO token endpoint, stores them linked to the order, and returns them |

Real DCR requires a `DCR_INITIAL_ACCESS_TOKEN` from the Red Hat SSO admin. Static mode requires the caller to provide pre-registered OAuth credentials in the request body alongside the `software_statement`.

### DCR Configuration

```bash
# Real DCR mode
DCR_ENABLED=true
DCR_INITIAL_ACCESS_TOKEN="<token-from-keycloak-admin>"
DCR_CLIENT_NAME_PREFIX="gemini-order-"
DCR_ENCRYPTION_KEY="<fernet-key>"   # Encrypts stored client secrets

# Red Hat SSO -- DCR endpoint derived as {issuer}/clients-registrations/openid-connect
RED_HAT_SSO_ISSUER="https://sso.redhat.com/auth/realms/redhat-external"
```

### Testing DCR Locally

For local testing without admin access to the production Red Hat SSO, see the [Testing DCR Locally](../README.md#testing-dcr-locally) section in the README. It covers:

- **Static credentials mode** -- caller provides `client_id` and `client_secret` in the request body (no Keycloak needed)
- **Local Keycloak in Podman** -- full DCR flow against a local instance

A test script is available at `scripts/test_dcr.py` that signs a software_statement JWT with a GCP service account you control. For static credentials mode, set `TEST_CLIENT_ID` and `TEST_CLIENT_SECRET` to include them in the request body. When the handler runs with `SKIP_JWT_VALIDATION=true`, it accepts JWTs from any service account and skips credential validation against Red Hat SSO.

### Security Considerations

- **Order ID validation**: The handler verifies the order exists in the database before creating a client. Without this check, any valid Google JWT (even for a different product) could register a client.
- **Secret encryption**: Client secrets are encrypted with Fernet before storage in PostgreSQL.
- **Initial Access Token**: Stored as a secret, never in code. Has limited uses (configurable in Keycloak).
- **Registration Access Tokens**: Encrypted and stored for future client management.

## MCP Sidecar Authentication

The agent forwards the caller's JWT token to the MCP sidecar via the `Authorization: Bearer <token>` header on every tool call. The MCP sidecar uses this token to authenticate with console.redhat.com APIs (Advisor, Inventory, Vulnerability, etc.) on behalf of the calling user. See [MCP Integration](mcp-integration.md) for full details.

## Token Introspection

All protected endpoints validate Bearer tokens via Keycloak token introspection
(RFC 7662) rather than local JWKS-based JWT verification.  This avoids audience
mismatch issues when tokens are issued by DCR-created clients (each has its own
`client_id` as audience).

### How It Works

1. **Extract Token**: Token extracted from `Authorization: Bearer <token>` header
2. **POST to Introspection Endpoint**: Agent sends the token to
   `{RED_HAT_SSO_ISSUER}/protocol/openid-connect/token/introspect`
3. **Authenticate as Resource Server**: Agent authenticates with its own
   `RED_HAT_SSO_CLIENT_ID` / `RED_HAT_SSO_CLIENT_SECRET` via HTTP Basic Auth
4. **Check Active**: Keycloak returns `{"active": true/false, ...}`.
   If `active` is `false`, the agent returns **401 Unauthorized**.
5. **Check Scope**: Agent checks that `agent:insights` is present in the
   token's `scope` field.  If missing, the agent returns **403 Forbidden**.
6. **Build User**: Agent maps the introspection response to an
   `AuthenticatedUser` for downstream use.

### Why Introspection Instead of JWKS?

With JWKS-based validation, the agent checks the token's `aud` claim against
its own `RED_HAT_SSO_CLIENT_ID`.  However, DCR-created clients each get their
own `client_id` as the audience in issued tokens.  This causes audience
mismatch errors.

Token introspection delegates validation to Keycloak, which knows about all
clients in the realm.  The agent only needs to confirm the token is active and
carries the required scope.

### Required Scope: `agent:insights`

Following the [reference implementation](https://github.com/ljogeiger/GE-A2A-Marketplace-Agent/tree/main/2_oauth)
pattern of `agent:time`, the agent requires the `agent:insights` scope.  This
scope must be:

1. Created as a Client Scope in the Keycloak realm
2. Assigned to the agent's Resource Server client
3. Included in DCR-created clients (via the `scope` field in the DCR request
   body per RFC 7591)

The required scope is configurable via `AGENT_REQUIRED_SCOPE` (default:
`agent:insights`).

### Introspection Response Fields

The agent extracts the following fields from the introspection response:

| Field | Description | Usage |
|-------|-------------|-------|
| `sub` | Subject (user ID) | User identification |
| `azp` / `client_id` | Client identifier | Usage tracking |
| `preferred_username` | Username | Display name |
| `email` | Email address | User contact |
| `org_id` | Organization ID | Multi-tenancy |
| `scope` | Space-separated scopes | Authorization (`agent:insights` check) |
| `exp` | Token expiry (unix timestamp) | Session management |

## Using Authentication in API Calls

### A2A Endpoints

All A2A endpoints require authentication:

```bash
# Get access token first (via client_credentials grant or ocm CLI)
ACCESS_TOKEN="your-access-token"

# Call A2A endpoint
curl -X POST http://localhost:8000/ \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message/send",
    "params": {
      "message": {
        "role": "user",
        "parts": [{"type": "text", "text": "List my systems"}]
      }
    },
    "id": "1"
  }'
```

### Protected vs Public Endpoints

| Endpoint | Authentication |
|----------|----------------|
| `GET /health` | Public |
| `GET /ready` | Public |
| `GET /.well-known/agent.json` | Public |
| `POST /` | Required (A2A JSON-RPC) |

## Red Hat SSO Configuration

### Required Settings

```bash
# Red Hat SSO issuer URL
RED_HAT_SSO_ISSUER=https://sso.redhat.com/auth/realms/redhat-external

# Resource Server credentials (used for token introspection)
RED_HAT_SSO_CLIENT_ID=your-client-id
RED_HAT_SSO_CLIENT_SECRET=your-client-secret

# Required scope checked during token introspection (default: agent:insights)
AGENT_REQUIRED_SCOPE=agent:insights
```

### Registering an OAuth Client

1. Go to [console.redhat.com](https://console.redhat.com)
2. Navigate to Settings → Service Accounts
3. Create a new service account
4. Note the client ID and secret
5. Configure redirect URIs

## Development Mode

For local development, JWT validation can be skipped:

```bash
# .env
SKIP_JWT_VALIDATION=true
DEBUG=true
```

**Warning**: Never enable this in production!

When validation is skipped, a default development user is created with the
`agent:insights` scope pre-granted:

```json
{
  "user_id": "dev-user",
  "client_id": "dev-client",
  "username": "developer",
  "email": "dev@example.com",
  "scopes": ["openid", "profile", "email", "agent:insights"]
}
```

## Local Testing Guide

This section provides step-by-step instructions for testing authentication locally.

### Prerequisites

Before testing, ensure you have:

1. The agent installed and configured
2. Red Hat SSO client credentials (or use development mode)
3. Python virtual environment activated

### Option 1: Testing with Development Mode (No Real SSO)

This is the easiest way to test without needing real Red Hat SSO credentials.

1. **Configure development mode** in `.env`:
   ```bash
   # Enable development mode
   SKIP_JWT_VALIDATION=true
   DEBUG=true

   # These can be placeholder values in dev mode
   RED_HAT_SSO_CLIENT_ID=dev-client
   RED_HAT_SSO_CLIENT_SECRET=dev-secret
   ```

2. **Start the API server**:
   ```bash
   python -m lightspeed_agent.main
   ```

3. **Test the health endpoint**:
   ```bash
   curl http://localhost:8000/health
   # Expected: {"status": "healthy"}
   ```

4. **Test A2A endpoint with authentication** (dev mode accepts any token):
   ```bash
   curl -X POST http://localhost:8000/ \
     -H "Authorization: Bearer dev-token" \
     -H "Content-Type: application/json" \
     -d '{
       "jsonrpc": "2.0",
       "method": "message/send",
       "params": {
         "message": {
           "role": "user",
           "parts": [{"type": "text", "text": "Hello"}]
         }
       },
       "id": "1"
     }'
   ```

### Option 2: Testing with Real Red Hat SSO

For integration testing with real Red Hat SSO authentication:

1. **Configure real SSO credentials** in `.env`:
   ```bash
   # Disable development mode
   SKIP_JWT_VALIDATION=false
   DEBUG=false

   # Real Red Hat SSO configuration
   RED_HAT_SSO_ISSUER=https://sso.redhat.com/auth/realms/redhat-external
   RED_HAT_SSO_CLIENT_ID=your-registered-client-id
   RED_HAT_SSO_CLIENT_SECRET=your-client-secret
   AGENT_REQUIRED_SCOPE=agent:insights
   ```

2. **Start the API server**:
   ```bash
   python -m lightspeed_agent.main
   ```

3. **Obtain a token** from Red Hat SSO directly (e.g., using `ocm` CLI or `client_credentials` grant):
   ```bash
   # Option A: Using ocm CLI
   ocm login --use-auth-code
   ACCESS_TOKEN=$(ocm token)

   # Option B: Using client_credentials grant (for service accounts)
   ACCESS_TOKEN=$(curl -s -X POST \
     "https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token" \
     -d "client_id=YOUR_CLIENT_ID" \
     -d "client_secret=YOUR_CLIENT_SECRET" \
     -d "grant_type=client_credentials" \
     -d "scope=openid agent:insights" \
     | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
   ```

4. **Test the A2A endpoint**:
   ```bash
   curl -X POST http://localhost:8000/ \
     -H "Authorization: Bearer $ACCESS_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "jsonrpc": "2.0",
       "method": "message/send",
       "params": {
         "message": {
           "role": "user",
           "parts": [{"type": "text", "text": "List my systems"}]
         }
       },
       "id": "1"
     }'
   ```

### Testing Error Scenarios

Test how the system handles authentication errors:

1. **Missing Authorization header**:
   ```bash
   curl -X POST http://localhost:8000/ \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"message/send","params":{"message":{"role":"user","parts":[{"type":"text","text":"test"}]}},"id":"1"}'
   # Expected: 401 Unauthorized
   ```

2. **Invalid token**:
   ```bash
   curl -X POST http://localhost:8000/ \
     -H "Authorization: Bearer invalid-token" \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"message/send","params":{"message":{"role":"user","parts":[{"type":"text","text":"test"}]}},"id":"1"}'
   # Expected: 401 Unauthorized (in production mode)
   ```

3. **Malformed Authorization header**:
   ```bash
   curl -X POST http://localhost:8000/ \
     -H "Authorization: InvalidFormat token123" \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"message/send","params":{"message":{"role":"user","parts":[{"type":"text","text":"test"}]}},"id":"1"}'
   # Expected: 401 Unauthorized
   ```

### Troubleshooting

#### "Token validation failed" error

1. Check that `RED_HAT_SSO_ISSUER` is correct (introspection endpoint is derived from it)
2. Verify network connectivity to sso.redhat.com
3. Ensure the token hasn't expired
4. Check that `RED_HAT_SSO_CLIENT_ID` / `RED_HAT_SSO_CLIENT_SECRET` are valid (used to authenticate to the introspection endpoint)

#### "Insufficient scope" / 403 Forbidden

The token is valid but missing the `agent:insights` scope:
1. Ensure the `agent:insights` Client Scope exists in the Keycloak realm
2. Verify the scope is assigned to the client that issued the token
3. Check the token's scopes: `echo $TOKEN | cut -d. -f2 | base64 -d 2>/dev/null | jq .scope`

#### "CORS errors" in browser

If testing from a browser on a different origin, you may need to configure CORS. The agent should handle this, but verify your browser isn't blocking requests.

#### Server not starting

Check the logs for configuration errors:
```bash
# Run with debug logging
LOG_LEVEL=DEBUG python -m lightspeed_agent.main
```

### Automated Testing

The project includes unit tests for authentication:

```bash
# Run all auth tests
pytest tests/test_auth.py -v

# Run introspection tests only
pytest tests/test_auth.py::TestTokenIntrospector -v

# Run with coverage
pytest tests/test_auth.py --cov=lightspeed_agent.auth
```

## Error Handling

### Authentication Errors

| HTTP Status | Error | Description |
|-------------|-------|-------------|
| 401 | Missing credentials | No Authorization header |
| 401 | Token not active | Introspection returned `active: false` (expired, revoked, or invalid) |
| 401 | Introspection failed | HTTP error calling introspection endpoint |
| 403 | Insufficient scope | Token is active but missing `agent:insights` scope |

### Error Response Format

```json
{
  "detail": "Token has expired"
}
```

With WWW-Authenticate header:

```
WWW-Authenticate: Bearer
```

## Security Best Practices

1. **Always use HTTPS** in production
2. **Never log tokens** - use token IDs for debugging
3. **Validate all claims** - don't skip validation
4. **Use short token lifetimes** - refresh tokens as needed
5. **Rotate secrets regularly** - update client secrets periodically
6. **Monitor for anomalies** - track failed authentication attempts
