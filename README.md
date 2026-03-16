# Red Hat Lightspeed Agent for Google Cloud

An A2A-ready agent for Red Hat Insights built with Google Agent Development Kit (ADK).

## Overview

This agent provides AI-powered access to Red Hat Insights services, enabling natural language interaction with:

- **Advisor**: System configuration assessment and recommendations
- **Inventory**: System management and tracking
- **Vulnerability**: Security threat analysis and CVE information
- **Remediations**: Issue resolution guidance and playbook management
- **Planning**: RHEL upgrade and migration planning
- **Image Builder**: Custom RHEL image creation

## Features

- Built with Google ADK and Gemini 2.5 Flash
- A2A protocol support with SSE streaming for multi-agent ecosystems
- OAuth 2.0 authentication via Red Hat SSO
- Dynamic Client Registration (DCR) with Red Hat SSO (Keycloak)
- Google Cloud Marketplace integration (Gemini Enterprise)
- PostgreSQL persistence for production deployments
- Usage tracking and reporting to Google Cloud Service Control
- Global rate limiting (requests per minute/hour)
- Integrated MCP server for Red Hat Insights API access

## Architecture

The system consists of **two separate services**:

```
                        Google Cloud Marketplace
                                 │
           ┌─────────────────────┴─────────────────────┐
           │ Pub/Sub Events                            │ DCR Requests
           ▼                                           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                  Marketplace Handler (Port 8001)                        │
│                  ────────────────────────────────                       │
│  - Always running to receive Pub/Sub provisioning events                │
│  - Hybrid /dcr endpoint (Pub/Sub + DCR)                                 │
│  - Creates OAuth clients in Red Hat SSO via DCR                         │
│  - Stores accounts, entitlements, clients in PostgreSQL                 │
└─────────────────────────────────────────────────────────────────────────┘
                                 │
                                 │ Shared PostgreSQL
                                 ▼
┌────────────────────────────────────────────────────────────────────────┐
│                    Lightspeed Agent (Port 8000)                        │
│                     ──────────────────────────                         │
│  ┌─────────────────────┐      ┌─────────────────────────────┐          │
│  │  Lightspeed Agent   │ HTTP │   Red Hat Lightspeed MCP    │          │
│  │   (Gemini + ADK)    │◄────►│   Server (Sidecar)          │          │
│  │                     │      │                             │          │
│  │   - A2A protocol    │      │   - Advisor, Inventory      │          │
│  │   - OAuth 2.0       │      │   - Vulnerability, Patch    │          │
│  │   - Session mgmt    │      │   - Remediations            │          │
│  └─────────────────────┘      └──────────────┬──────────────┘          │
└──────────────────────────────────────────────┼─────────────────────────┘
                                               │
                                               ▼
                                       ┌───────────────────┐
                                       │ console.redhat.com│
                                       │ (Insights APIs)   │
                                       └───────────────────┘
```

### Service Responsibilities

| Service | Port | Purpose | Scaling |
|---------|------|---------|---------|
| **Marketplace Handler** | 8001 | Pub/Sub events, DCR, provisioning | Always on (minScale=1) |
| **Lightspeed Agent** | 8000 | A2A queries, user interactions, MCP | Scale to zero when idle |

### Deployment Order

1. **Deploy Marketplace Handler first** - Must be running to receive provisioning events
2. **Deploy Agent after provisioning** - Can be deployed when customers are ready

See [docs/architecture.md](docs/architecture.md) for detailed architecture documentation.

## Quick Start

### Prerequisites

- Python 3.12+
- Google API key or Vertex AI access
- Red Hat Insights service account credentials

### Installation

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd lightspeed-agent
   ```

2. Create a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Linux/macOS
   # or
   .venv\Scripts\activate     # Windows
   ```

3. Install dependencies:
   ```bash
   pip install -e ".[agent]"
   ```

4. Configure environment:
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

### Running the Agent

The Lightspeed Agent requires the Red Hat Lightspeed MCP server to be running to access Insights APIs. Choose one of the following approaches:

#### Option 1: Development Mode (with MCP Server)

1. **Start the MCP server** in a separate terminal:
   ```bash
   # Start the MCP server container
   # Note: Credentials are passed by the agent via HTTP headers, not env vars
   podman run -d --name insights-mcp \
     -p 8080:8080 \
     quay.io/redhat-services-prod/insights-management-tenant/insights-mcp/red-hat-lightspeed-mcp:latest \
     http --port 8080 --host 0.0.0.0
   ```

2. **Start Redis** (required for API Server rate limiting):
   ```bash
   # Option A: Podman (recommended)
   podman kube play deploy/podman/redis-pod.yaml

   # Option B: Standalone container
   podman run -d -p 6379:6379 --name redis docker.io/library/redis:7-alpine

   # Option C: Local Redis (if installed)
   redis-server
   ```

3. **Run the agent** using one of these methods:

   **Development UI (ADK Web):**
   ```bash
   adk web agents
   ```

   **Terminal Mode:**
   ```bash
   adk run agents/rh_lightspeed_agent
   ```

   **API Server:**
   ```bash
   python -m lightspeed_agent.main
   ```

#### Option 2: Full Stack with Podman Pod

For production-like deployment with all services (agent, MCP server, database):

```bash
# Deploy secrets first (see Container Deployment for setup)
podman kube play deploy/podman/my-secrets.yaml

# Start Redis first (required for rate limiting)
podman kube play deploy/podman/redis-pod.yaml

# Start all services
podman kube play \
  --configmap deploy/podman/lightspeed-agent-configmap.yaml \
  deploy/podman/lightspeed-agent-pod.yaml

# Access the agent at http://localhost:8000
```

See [Container Deployment](#container-deployment) for full details.

#### Option 3: Development without MCP (Limited)

If the MCP server is not running, the agent will start without tools (limited functionality):

```bash
# Run agent (will work but without Insights API access)
adk web agents
```

## Configuration

See `.env.example` for all available configuration options.

### Required Environment Variables

| Variable | Description |
|----------|-------------|
| `GOOGLE_API_KEY` | Google AI Studio API key |
| `RED_HAT_SSO_CLIENT_ID` | OAuth client ID for Red Hat SSO |
| `RED_HAT_SSO_CLIENT_SECRET` | OAuth client secret for Red Hat SSO |

### Obtaining Credentials

#### Red Hat SSO OAuth Credentials

For user authentication via OAuth 2.0:

1. Register your application with Red Hat SSO
2. Configure redirect URIs for your deployment
3. Obtain the client ID and secret

## Project Structure

```
lightspeed_agent/
├── agent.py                 # ADK CLI entry point
├── pyproject.toml          # Project configuration
├── .env.example            # Environment template
├── Containerfile           # Agent container build (UBI 9)
├── Containerfile.marketplace-handler  # Handler container build
├── Makefile                # Development commands
├── scripts/                # Testing and utility scripts
│   └── test_dcr.py         # DCR endpoint test client
├── docs/                   # Documentation
│   ├── architecture.md     # System architecture, DB schema, ADRs
│   ├── authentication.md   # OAuth 2.0, DCR, JWT, MCP auth
│   ├── api.md              # API reference
│   ├── configuration.md    # Config reference
│   ├── marketplace.md      # GCP Marketplace integration
│   ├── mcp-integration.md  # MCP server and console.redhat.com APIs
│   └── troubleshooting.md  # Troubleshooting guide
├── deploy/
│   ├── cloudrun/           # Cloud Run deployment
│   │   ├── service.yaml           # Agent service config
│   │   ├── marketplace-handler.yaml  # Handler service config
│   │   └── deploy.sh              # Deploy script (--service all|handler|agent)
│   └── podman/             # Podman/Kubernetes deployment
│       ├── redis-pod.yaml                # Redis pod for rate limiting (start first)
│       ├── marketplace-handler-pod.yaml  # Handler pod (start after Redis)
│       ├── lightspeed-agent-pod.yaml       # Agent pod
│       ├── lightspeed-agent-configmap.yaml
│       └── lightspeed-agent-secret.yaml
└── src/
    └── lightspeed_agent/
        ├── api/                # A2A endpoints and AgentCard
        │   └── a2a/            # A2A protocol setup
        ├── auth/               # OAuth 2.0 authentication
        ├── config/             # Settings management
        ├── core/               # Agent definition (ADK)
        ├── db/                 # Database models (SQLAlchemy)
        ├── dcr/                # Dynamic Client Registration
        │   ├── keycloak_client.py  # Red Hat SSO DCR client
        │   └── service.py          # DCR business logic
        ├── marketplace/        # Google Marketplace integration & handler service
        │   ├── app.py              # Handler FastAPI app (port 8001)
        │   ├── router.py           # Hybrid /dcr endpoint
        │   ├── service.py          # Procurement API
        │   └── repository.py       # PostgreSQL persistence
        ├── metering/           # Usage tracking
        └── tools/              # MCP integration
```

## Documentation

Comprehensive documentation is available in the [docs/](docs/) directory:

- [Architecture Overview](docs/architecture.md) - Two-service architecture and data flows
- [Authentication](docs/authentication.md) - OAuth 2.0, DCR, JWT validation, MCP authentication
- [MCP Server Integration](docs/mcp-integration.md) - Red Hat Lightspeed MCP server setup
- [Authentication Guide](docs/authentication.md) - OAuth 2.0 and JWT validation
- [API Reference](docs/api.md) - Endpoints and examples
- [Configuration Reference](docs/configuration.md) - All environment variables
- [Marketplace Integration](docs/marketplace.md) - GCP Marketplace, DCR, billing
- [Troubleshooting Guide](docs/troubleshooting.md) - Common issues and solutions

## Container Deployment (Podman)

The system is deployed as **three separate pods**:

1. **Redis Pod** (start first):
   - **redis**: Shared rate limiter backend for all agent replicas

2. **Marketplace Handler Pod** (start after Redis):
   - **marketplace-handler**: Handles Pub/Sub events and DCR requests
   - **postgres**: PostgreSQL database for marketplace data (orders, entitlements, DCR clients)

3. **Lightspeed Agent Pod** (start after handler):
   - **lightspeed-agent**: Main A2A agent (Gemini + Google ADK)
   - **insights-mcp**: Red Hat Lightspeed MCP server
   - **session-postgres**: PostgreSQL database for agent sessions (ADK session persistence)
   - **a2a-inspector**: Web UI for agent interaction (optional)

### Prerequisites

- Podman 4.0+
- Access to Red Hat container registry (for RHEL-based images)
- Google API key or Vertex AI access

### Build the Container Images

```bash
# Build the marketplace handler image
podman build -t localhost/marketplace-handler:latest -f Containerfile.marketplace-handler .

# Build the agent image
podman build -t localhost/lightspeed-agent:latest -f Containerfile .

# (Optional) Build the A2A Inspector for web UI
git clone https://github.com/a2aproject/a2a-inspector.git /tmp/a2a-inspector
podman build -t localhost/a2a-inspector:latest /tmp/a2a-inspector
```

### Configure Environment

1. Create the config directory:
   ```bash
   mkdir -p config
   ```

2. If using Vertex AI, copy your credentials:
   ```bash
   cp /path/to/vertex-credentials.json config/
   ```

3. Create your secrets file with credentials:
   ```bash
   # Copy the template
   cp deploy/podman/lightspeed-agent-secret.yaml deploy/podman/my-secrets.yaml

   # Edit with your actual credentials (plain text, no encoding needed)
   # IMPORTANT: Never commit my-secrets.yaml to version control!
   ```

   Edit `deploy/podman/my-secrets.yaml` and fill in:

   **API Credentials:**
   - `GOOGLE_API_KEY`: Your Google AI Studio API key
   - `RED_HAT_SSO_CLIENT_ID`: OAuth client ID for Red Hat SSO
   - `RED_HAT_SSO_CLIENT_SECRET`: OAuth client secret for Red Hat SSO

   **Database Passwords:**
   - `MARKETPLACE_DB_PASSWORD`: Password for marketplace PostgreSQL (default: `insights`)
   - `SESSION_DB_PASSWORD`: Password for session PostgreSQL (default: `sessions`)

   **Database URLs:**
   - `MARKETPLACE_DATABASE_URL`: Marketplace DB URL for marketplace-handler pod (uses `localhost:5432` since PostgreSQL is in the same pod)
   - `DATABASE_URL`: Marketplace DB URL for lightspeed-agent pod (uses `host.containers.internal:5432` to reach the marketplace-handler pod's PostgreSQL)
   - `SESSION_DATABASE_URL`: Session DB URL for lightspeed-agent pod (uses `localhost:5433` since session PostgreSQL is in the same pod)

4. (Optional) Customize configuration in `deploy/podman/lightspeed-agent-configmap.yaml`:
   - Database users and names (`MARKETPLACE_DB_USER`, `SESSION_DB_USER`, etc.)
   - Agent settings, logging, rate limiting
   - MCP server configuration

### Run the Pods

```bash
# First, deploy the secrets (creates a Kubernetes Secret in podman)
podman kube play deploy/podman/my-secrets.yaml

# Start Redis FIRST (required for rate limiting)
podman kube play deploy/podman/redis-pod.yaml

# Start the marketplace handler SECOND (contains PostgreSQL)
podman kube play \
  --configmap deploy/podman/lightspeed-agent-configmap.yaml \
  deploy/podman/marketplace-handler-pod.yaml

# Then start the agent pod (connects to handler's PostgreSQL and Redis)
podman kube play \
  --configmap deploy/podman/lightspeed-agent-configmap.yaml \
  deploy/podman/lightspeed-agent-pod.yaml

# View pod status
podman pod ps

# View container logs
podman logs lightspeed-redis-redis                     # Redis logs
podman logs marketplace-handler-marketplace-handler  # Handler logs
podman logs marketplace-handler-postgres             # Marketplace PostgreSQL logs
podman logs lightspeed-agent-pod-lightspeed-agent        # Agent logs
podman logs lightspeed-agent-pod-insights-mcp          # MCP server logs
podman logs lightspeed-agent-pod-session-postgres      # Session PostgreSQL logs

# Stop and remove all resources (reverse order)
podman kube down deploy/podman/lightspeed-agent-pod.yaml
podman kube down deploy/podman/marketplace-handler-pod.yaml
podman kube down deploy/podman/redis-pod.yaml
podman kube down deploy/podman/my-secrets.yaml
```

### Access the Services

**Marketplace Handler:**

| Service | URL | Description |
|---------|-----|-------------|
| Handler Health | http://localhost:8001/health | Handler health status |
| DCR Endpoint | http://localhost:8001/dcr | Pub/Sub + DCR hybrid endpoint |

**Lightspeed Agent:**

| Service | URL | Description |
|---------|-----|-------------|
| Agent API | http://localhost:8000 | Main A2A endpoint |
| Health Check | http://localhost:8000/health | Agent health status |
| AgentCard | http://localhost:8000/.well-known/agent.json | A2A discovery |
| MCP Server | http://localhost:8081 | MCP server (internal) |
| A2A Inspector | http://localhost:8080 | Web UI for agent interaction |

**Redis:**

| Service | URL | Description |
|---------|-----|-------------|
| Redis | redis://localhost:6379 | Rate limiter backend |

### Validate Rate Limiting (Burst Test)

Use this quick test to confirm throttling behavior and inspect Redis keys.

> **Note:** For local testing without OAuth tokens, set `SKIP_JWT_VALIDATION: "true"` in `deploy/podman/lightspeed-agent-configmap.yaml` and restart the agent pod.

```bash
# Send 70 requests quickly (default minute limit is 60)
for i in {1..70}; do
  code=$(curl -s -o /tmp/resp.json -w "%{http_code}" \
    -X POST http://localhost:8000/ \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","method":"message/send","id":"'$i'","params":{"message":{"messageId":"'$i'","role":"user","parts":[{"type":"text","text":"test"}]}}}')
  echo "$i -> $code"
done

# Inspect 429 details and headers
curl -i -X POST http://localhost:8000/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"message/send","id":"x","params":{"message":{"messageId":"x","role":"user","parts":[{"type":"text","text":"test"}]}}}'

# Inspect Redis rate-limit keys
podman exec -it lightspeed-redis-redis redis-cli KEYS "lightspeed:ratelimit:*"
```

Expected result:
- Requests `1..60` succeed, and `61+` return `429`
- `429` responses include `Retry-After`, `X-RateLimit-Limit`, and `X-RateLimit-Remaining`
- Redis shows `:m` and `:h` keys for the resolved principal (e.g., `ip:...`, `order:...`, `user:...`)

### Using the A2A Inspector (Web UI)

The [A2A Inspector](https://github.com/a2aproject/a2a-inspector) provides a web-based interface for interacting with the agent, similar to `adk web` but designed for deployed agents.

**Features:**
- View the agent's AgentCard and capabilities
- Chat interface with streaming responses
- JSON-RPC 2.0 debug console to inspect raw messages
- A2A protocol spec compliance validation

**To use the Inspector:**

1. Build the inspector image (see [Build the Container Images](#build-the-container-images))
2. Start the pod as usual
3. Open http://localhost:8080 in your browser
4. Enter `http://localhost:8000` as the agent URL
5. The inspector will fetch the AgentCard and enable chat

> **Note:** If you don't need the web UI, you can skip building the inspector image. The pod will start with a warning about the missing image but other containers will work normally.

### Authentication Testing

The agent supports OAuth 2.0 authentication via Red Hat SSO. For local testing, you can use the OCM CLI to obtain a valid token.

#### Prerequisites

Install the OCM CLI from https://console.redhat.com/openshift/token

#### Obtaining a Token

1. **Login with browser-based authentication:**
   ```bash
   ocm login --use-auth-code
   ```
   This opens a browser window for Red Hat SSO authentication.

2. **Get the access token:**
   ```bash
   ocm token
   ```
   This prints the current access token.

#### Configure the Agent for OCM Tokens

Update your `deploy/podman/my-secrets.yaml` to use `ocm-cli` as the client ID (matching the token's audience):

```yaml
RED_HAT_SSO_CLIENT_ID: "ocm-cli"
```

Then redeploy the secrets and restart the agent pod.

#### Testing with the Token

**Option 1: A2A Inspector**

1. Open http://localhost:8080
2. Enter `http://localhost:8000` as the agent URL
3. Add the Authorization header with your token in the Inspector settings

**Option 2: curl**

```bash
# Get token
TOKEN=$(ocm token)

# Test the A2A endpoint
curl -X POST http://localhost:8000/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message/send",
    "params": {
      "message": {
        "messageId": "1",
        "role": "user",
        "parts": [{"type": "text", "text": "Show my systems"}]
      }
    },
    "id": "1"
  }'
```

#### Development Mode (Skip Authentication)

For development without real tokens, set `SKIP_JWT_VALIDATION: "true"` in the configmap. Any token will be accepted.

### Testing DCR Locally

The Dynamic Client Registration (DCR) flow can be tested locally without admin access to the production Red Hat SSO. There are two modes: **static credentials** (no Keycloak needed) and **real DCR** against a local Keycloak instance.

Both modes require `SKIP_JWT_VALIDATION=true` on the marketplace handler so it accepts JWTs signed by your own GCP service account instead of Google's production `cloud-agentspace` account.

#### Prerequisites

1. A GCP service account:
   ```bash
   gcloud services enable iam.googleapis.com --project=<PROJECT>

   gcloud iam service-accounts create dcr-test \
     --display-name "DCR test signer" \
     --project=<PROJECT>
   ```

   > **Note:** GCP may need a few seconds to propagate the new service account.
   > If the next commands fail with `NOT_FOUND`, wait ~10 seconds and retry.

2. **Choose a signing method** for the test script:

   **Method A -- Local key file (recommended, no extra IAM permissions):**
   ```bash
   gcloud iam service-accounts keys create dcr-test-key.json \
     --iam-account=dcr-test@<PROJECT>.iam.gserviceaccount.com \
     --project=<PROJECT>

   pip install PyJWT cryptography requests
   ```

   **Method B -- IAM Credentials API (needs `serviceAccountTokenCreator` role):**
   ```bash
   gcloud services enable iamcredentials.googleapis.com --project=<PROJECT>

   gcloud iam service-accounts add-iam-policy-binding \
     dcr-test@<PROJECT>.iam.gserviceaccount.com \
     --member="user:<YOUR_EMAIL>" \
     --role="roles/iam.serviceAccountTokenCreator" \
     --project=<PROJECT>

   pip install google-cloud-iam requests
   gcloud auth application-default login
   ```

3. Generate a Fernet encryption key:
   ```bash
   python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
   ```

#### Option A: Static Credentials (No Keycloak)

This mode skips Keycloak client creation. Instead, the caller provides pre-registered `client_id` and `client_secret` in the DCR request body alongside the `software_statement`. The handler validates them (skipped with `SKIP_JWT_VALIDATION=true`), stores them linked to the order, and returns them.

1. **Copy the secrets template and edit it:**
   ```bash
   cp deploy/podman/lightspeed-agent-secret.yaml deploy/podman/my-secrets.yaml
   ```

   Edit `deploy/podman/my-secrets.yaml` and set at minimum:
   ```yaml
   stringData:
     DCR_ENCRYPTION_KEY: "<your-fernet-key>"
     MARKETPLACE_DATABASE_URL: "postgresql+asyncpg://insights:insights@localhost:5432/lightspeed_agent"
     MARKETPLACE_DB_PASSWORD: "insights"
   ```

2. **Set `DCR_ENABLED` to `false`** in `deploy/podman/lightspeed-agent-configmap.yaml`:
   ```yaml
   DCR_ENABLED: "false"
   SKIP_JWT_VALIDATION: "true"
   ```

3. **Start the marketplace handler pod:**
   ```bash
   podman kube play deploy/podman/my-secrets.yaml
   podman kube play \
     --configmap deploy/podman/lightspeed-agent-configmap.yaml \
     deploy/podman/marketplace-handler-pod.yaml
   ```

4. **Run the test script with static credentials:**
   ```bash
   # Method A (key file):
   export TEST_SA_KEY_FILE=dcr-test-key.json
   export TEST_CLIENT_ID=my-test-client
   export TEST_CLIENT_SECRET=my-test-secret
   python scripts/test_dcr.py

   # Method B (IAM API):
   export TEST_SERVICE_ACCOUNT=dcr-test@<PROJECT>.iam.gserviceaccount.com
   export TEST_CLIENT_ID=my-test-client
   export TEST_CLIENT_SECRET=my-test-secret
   python scripts/test_dcr.py
   ```

   The script sends `client_id` and `client_secret` in the request body. The handler stores them and returns them. The second request verifies idempotency (same credentials returned for the same order).

5. **Clean up:**
   ```bash
   podman kube down deploy/podman/marketplace-handler-pod.yaml
   ```

#### Option B: Real DCR with Local Keycloak

This mode exercises the full DCR flow -- real OAuth client creation in a locally-controlled Keycloak instance.

1. **Start Keycloak in Podman:**
   ```bash
   podman run -d \
     --name keycloak-test \
     -p 8180:8080 \
     -e KC_BOOTSTRAP_ADMIN_USERNAME=admin \
     -e KC_BOOTSTRAP_ADMIN_PASSWORD=admin \
     -e KC_HTTP_ENABLED=true \
     -e KC_HOSTNAME=host.containers.internal \
     -e KC_HOSTNAME_PORT=8180 \
     -e KC_HOSTNAME_STRICT=true \
     quay.io/keycloak/keycloak:26.0 start-dev --http-port=8080
   ```

   > **Why these hostname settings?** The marketplace handler container reaches
   > Keycloak via `host.containers.internal:8180`, but you interact with Keycloak
   > from the host via `localhost:8180`. With `KC_HOSTNAME_STRICT=true`, Keycloak
   > uses a consistent issuer (`http://host.containers.internal:8180/...`) for all
   > tokens regardless of which hostname the request arrives on. Without this, the
   > IAT (Initial Access Token) would have a `localhost` issuer that mismatches
   > when the handler presents it via `host.containers.internal`, causing
   > "Failed decode token" errors.

2. **Disable SSL requirement and create the test realm:**

   Since `KC_HOSTNAME_STRICT=true` treats `localhost` requests as external,
   you must disable the SSL requirement via `kcadm.sh` from inside the container:

   ```bash
   # Authenticate kcadm.sh (uses internal port 8080)
   podman exec keycloak-test /opt/keycloak/bin/kcadm.sh \
     config credentials --server http://localhost:8080 \
     --realm master --user admin --password admin

   # Disable SSL on master realm
   podman exec keycloak-test /opt/keycloak/bin/kcadm.sh \
     update realms/master -s sslRequired=NONE

   # Create test-realm with SSL disabled
   podman exec keycloak-test /opt/keycloak/bin/kcadm.sh \
     create realms -s realm=test-realm -s enabled=true -s sslRequired=NONE
   ```

3. **Get an admin token:**
   ```bash
   ADMIN_TOKEN=$(curl -s -X POST \
     "http://localhost:8180/realms/master/protocol/openid-connect/token" \
     -d "client_id=admin-cli" \
     -d "username=admin" \
     -d "password=admin" \
     -d "grant_type=password" \
     | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
   ```

4. **Generate an Initial Access Token (IAT) for DCR:**
   ```bash
   IAT=$(curl -s -X POST \
     "http://localhost:8180/admin/realms/test-realm/clients-initial-access" \
     -H "Authorization: Bearer $ADMIN_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"count": 100, "expiration": 86400}' \
     | python -c "import sys,json; print(json.load(sys.stdin)['token'])")
   echo "Initial Access Token: $IAT"
   ```

5. **Copy the secrets template and configure for local Keycloak:**
   ```bash
   cp deploy/podman/lightspeed-agent-secret.yaml deploy/podman/my-secrets.yaml
   ```

   Edit `deploy/podman/my-secrets.yaml`:
   ```yaml
   stringData:
     RED_HAT_SSO_CLIENT_ID: "lightspeed-agent"
     RED_HAT_SSO_CLIENT_SECRET: "dummy"
     DCR_INITIAL_ACCESS_TOKEN: "<the IAT from step 4>"
     DCR_ENCRYPTION_KEY: "<your-fernet-key>"
     MARKETPLACE_DATABASE_URL: "postgresql+asyncpg://insights:insights@localhost:5432/lightspeed_agent"
     MARKETPLACE_DB_PASSWORD: "insights"
   ```

6. **Update the configmap** in `deploy/podman/lightspeed-agent-configmap.yaml`:
   ```yaml
   DCR_ENABLED: "true"
   SKIP_JWT_VALIDATION: "true"
   RED_HAT_SSO_ISSUER: "http://host.containers.internal:8180/realms/test-realm"
   ```

   Note: Use `host.containers.internal` so the handler container can reach Keycloak running on the host.

7. **Start the marketplace handler pod:**
   ```bash
   podman kube play deploy/podman/my-secrets.yaml
   podman kube play \
     --configmap deploy/podman/lightspeed-agent-configmap.yaml \
     deploy/podman/marketplace-handler-pod.yaml
   ```

8. **Run the test script:**
   ```bash
   # Method A (key file):
   export TEST_SA_KEY_FILE=dcr-test-key.json
   python scripts/test_dcr.py

   # Method B (IAM API):
   export TEST_SERVICE_ACCOUNT=dcr-test@<PROJECT>.iam.gserviceaccount.com
   python scripts/test_dcr.py
   ```

   The handler will create a real OAuth client in your local Keycloak. You can verify it at http://localhost:8180/admin -> test-realm -> Clients.

9. **You can also test Keycloak DCR directly** (bypassing the handler entirely):
   ```bash
   curl -s -X POST \
     "http://localhost:8180/realms/test-realm/clients-registrations/openid-connect" \
     -H "Authorization: Bearer $IAT" \
     -H "Content-Type: application/json" \
     -d '{
       "client_name": "gemini-order-test-123",
       "redirect_uris": ["https://gemini.google.com/callback"],
       "grant_types": ["authorization_code", "refresh_token"],
       "token_endpoint_auth_method": "client_secret_basic",
       "application_type": "web"
     }'
   ```

10. **Clean up:**
    ```bash
    podman kube down deploy/podman/marketplace-handler-pod.yaml
    podman stop keycloak-test && podman rm keycloak-test
    ```

#### Test Script Reference

The test script at `scripts/test_dcr.py` is configurable via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `TEST_SA_KEY_FILE` | | Path to SA key JSON file (Method A, recommended) |
| `TEST_SERVICE_ACCOUNT` | | SA email for IAM Credentials API (Method B) |
| `MARKETPLACE_HANDLER_URL` | `http://localhost:8001` | Marketplace handler base URL |
| `PROVIDER_URL` | `https://www.redhat.com` | JWT audience (must match handler's `AGENT_PROVIDER_ORGANIZATION_URL`) |
| `TEST_ORDER_ID` | random UUID | Marketplace order ID |
| `TEST_ACCOUNT_ID` | `test-procurement-account-001` | Procurement account ID |
| `TEST_REDIRECT_URIS` | `https://gemini.google.com/callback` | Comma-separated redirect URIs |
| `TEST_CLIENT_ID` | | Static OAuth client ID (for `DCR_ENABLED=false` mode) |
| `TEST_CLIENT_SECRET` | | Static OAuth client secret (for `DCR_ENABLED=false` mode) |

The script sends two identical requests to verify idempotency — per Google's DCR spec, the handler must return the same `client_id`/`client_secret` for the same order. When `TEST_CLIENT_ID` and `TEST_CLIENT_SECRET` are set, the script includes them in the request body for static credentials mode.

### Pod Services

**Redis Pod:**

| Container | Port | Description |
|-----------|------|-------------|
| redis | 6379 | Shared Redis backend for rate limiting |

**Marketplace Handler Pod:**

| Container | Port | Description |
|-----------|------|-------------|
| marketplace-handler | 8001 | Pub/Sub events and DCR endpoint |
| postgres | 5432 | PostgreSQL for marketplace data (orders, entitlements, DCR clients) |

**Lightspeed Agent Pod:**

| Container | Port | Description |
|-----------|------|-------------|
| lightspeed-agent | 8000 | Main A2A agent API |
| insights-mcp | 8081 | Red Hat Lightspeed MCP server |
| session-postgres | 5433 | PostgreSQL for agent sessions (ADK session persistence) |
| a2a-inspector | 8080 | Web UI for agent interaction (optional) |

### Database Architecture

The system uses **two separate PostgreSQL databases** for security isolation:

| Database | Pod | Port | Purpose |
|----------|-----|------|---------|
| Marketplace DB | marketplace-handler | 5432 | Orders, entitlements, DCR clients |
| Session DB | lightspeed-agent | 5433 | ADK agent sessions |

This separation ensures:
- Agent sessions cannot access marketplace/auth data
- Compromised agents cannot access DCR credentials or order information
- Different retention and backup policies can be applied to each database

### How the MCP Server Works

The MCP server runs as a sidecar container and provides tools for the agent to interact with Red Hat Insights APIs:

1. **Agent receives a request** (e.g., "Show me my system vulnerabilities")
2. **Agent calls MCP tools** via HTTP to the MCP server (localhost:8081), forwarding the caller's JWT token in the Authorization header
3. **MCP server authenticates** with console.redhat.com using the forwarded JWT token
4. **MCP server calls Insights APIs** and returns results to the agent
5. **Agent formats the response** and returns it to the user

The agent forwards the caller's JWT token to the MCP server via the `Authorization: Bearer` header on each request. The MCP server itself does not need credentials configured.

### Persistent Storage

By default, both PostgreSQL databases use `emptyDir` volumes and data is lost when the pods are removed. To persist data:

1. **Marketplace Database**: Edit `deploy/podman/marketplace-handler-pod.yaml` and change the `postgres-data` volume from `emptyDir` to `hostPath`
2. **Session Database**: Edit `deploy/podman/lightspeed-agent-pod.yaml` and change the `session-pgdata` volume from `emptyDir` to `hostPath`

Example hostPath configuration:
```yaml
volumes:
  - name: postgres-data  # or session-pgdata
    hostPath:
      path: ./data/pgdata
      type: DirectoryOrCreate
```

## Google Cloud Run Deployment

For production deployment to Google Cloud Run, see [deploy/cloudrun/README.md](deploy/cloudrun/README.md).

The system deploys as **two separate Cloud Run services**:
- **marketplace-handler**: Always running (minScale=1) for Pub/Sub events
- **lightspeed-agent**: Scales to zero when idle

Quick deploy:

```bash
# Set up GCP project
export GOOGLE_CLOUD_PROJECT="your-project-id"
export GOOGLE_CLOUD_LOCATION="us-central1"

# Run setup script
./deploy/cloudrun/setup.sh

# Deploy both services
./deploy/cloudrun/deploy.sh --service all --build --allow-unauthenticated

# Or deploy individually:
./deploy/cloudrun/deploy.sh --service handler --allow-unauthenticated
./deploy/cloudrun/deploy.sh --service agent --allow-unauthenticated
```

## License

Apache License 2.0
