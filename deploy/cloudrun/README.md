# Google Cloud Run Deployment

Deploy the Red Hat Lightspeed Agent for Google Cloud to Google Cloud Run for production use.

## Table of Contents

- [Architecture](#architecture)
- [Service Accounts](#service-accounts)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
  - [1. Set Environment Variables](#1-set-environment-variables)
  - [2. Run Setup Script](#2-run-setup-script)
  - [3. Set Up Cloud SQL Database](#3-set-up-cloud-sql-database)
  - [4. Redis Setup for Rate Limiting](#4-redis-setup-for-rate-limiting)
  - [5. Configure Secrets](#5-configure-secrets)
  - [6. Copy MCP Image to GCR](#6-copy-mcp-image-to-gcr)
  - [7. Deploy](#7-deploy)
- [Service Configuration](#service-configuration)
  - [Agent Container](#agent-container)
  - [Rate Limiting (Redis)](#rate-limiting-redis)
  - [MCP Output Size Guard](#mcp-output-size-guard)
  - [MCP Server Sidecar](#mcp-server-sidecar)
  - [Scaling](#scaling)
- [How the MCP Server Works](#how-the-mcp-server-works)
- [Authentication](#authentication)
- [Endpoints](#endpoints)
- [Testing the Deployment](#testing-the-deployment)
- [Database Architecture](#database-architecture)
- [Custom Domain](#custom-domain)
- [Testing the Agent](#testing-the-agent)
- [Testing DCR on Cloud Run](#testing-dcr-on-cloud-run)
- [Audit Logging](#audit-logging)
- [Monitoring](#monitoring)
- [Troubleshooting](#troubleshooting)
- [Cleanup / Teardown](#cleanup--teardown)

## Architecture

The deployment consists of **two separate Cloud Run services** plus **Cloud Memorystore for Redis** (for rate limiting):

```
                              Google Cloud Marketplace
                                       │
                 ┌─────────────────────┴─────────────────────┐
                 │                                           │
                 ▼                                           ▼
      ┌──────────────────────┐                ┌──────────────────────────────────┐
      │  Pub/Sub (Events)    │                │  Gemini Enterprise (DCR)         │
      └──────────┬───────────┘                └──────────────────┬───────────────┘
                 │                                               │
                 ▼                                               ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    Marketplace Handler Service (Port 8001)                      │
│                    ───────────────────────────────────────                      │
│  - Always running (minScale=1) to receive Pub/Sub events                        │
│  - Handles entitlement approvals via Procurement API (filtered by product)      │
│  - Handles DCR requests (creates OAuth clients in Red Hat SSO)                  │
│  - Stores data in PostgreSQL                                                    │
└──────────┬──────────────────────────────────────────────────────────────────────┘
           │                                                 │
           │ Shared PostgreSQL Database                      │ DCR (create OAuth clients)
           ▼                                                 ▼
┌──────────────────────────────────────────────┐    ┌──────────────────────┐
│   Lightspeed Agent Service (Port 8000)       │    │  Red Hat SSO         │
│   ─────────────────────────────────────      │    │  (GMA SSO API)       │
│  ┌──────────────────┐   ┌──────────────────┐ │    │                      │
│  │ Lightspeed Agent │   │ Lightspeed MCP   │ │    │  Production:         │
│  │                  │   │ Server (8081)    │ │    │   sso.redhat.com     │
│  │  - Gemini 2.5    │   │                  │ │    │                      │
│  │  - A2A protocol  │◄-►│ - Advisor tools  │ │    │                      │
│  │  - OAuth 2.0     │   │ - Inventory tools│ │    │                      │
│  │                  │   │ - Vuln. tools    │ │    │                      │
│  └──────────────────┘   └────────┬─────────┘ │    └──────────────────────┘
│                                  │           │
└──────────────────────────────────┼───────────┘
                                   │
                                   ▼
                          ┌──────────────────┐
                          │console.redhat.com│
                          │ (Insights APIs)  │
                          └──────────────────┘
```

### Service Responsibilities

| Service | Port | Purpose | Scaling |
|---------|------|---------|---------|
| **Marketplace Handler** | 8001 | Pub/Sub events, DCR | Always on (minScale=1) |
| **Lightspeed Agent** | 8000 | A2A queries, user interactions | Scale to zero |

### Deployment Order

1. **Set up Cloud Memorystore Redis and VPC connector** - Required for agent rate limiting (see [Redis Setup](#redis-setup-for-rate-limiting))
2. **Deploy Marketplace Handler first** - Must be running to receive provisioning events
3. **Deploy Agent after provisioning** - Can be deployed when customers are ready to use the agent

The MCP server runs as a sidecar in the Agent service. The agent forwards the caller's JWT token to the MCP server, which uses it to authenticate with console.redhat.com on behalf of the user (see [MCP Authentication](#mcp-authentication)).

## Service Accounts

The deployment uses **two separate service accounts** following the principle of least privilege:

| Service Account | Name | Purpose | Permissions |
|-----------------|------|---------|-------------|
| **Runtime SA** | `lightspeed-agent` | Cloud Run service identity for both services | Secret Manager access, Vertex AI, Pub/Sub, Cloud SQL, Service Usage, logging, monitoring |
| **Pub/Sub Invoker SA** | `pubsub-invoker` | Authenticates Pub/Sub push subscriptions to invoke Cloud Run | `roles/run.invoker` on marketplace-handler service only |

**Why two service accounts?**

- The **Runtime SA** runs as the identity of both Cloud Run services and needs access to secrets, AI models, databases, etc. It does **not** need `roles/run.invoker`.
- The **Pub/Sub Invoker SA** is used exclusively by the Pub/Sub push subscription to authenticate when delivering marketplace events to the handler. It only has `roles/run.invoker` on the marketplace-handler service (not project-wide).
- This separation ensures that if one SA is compromised, the blast radius is limited.

Both are created automatically by `setup.sh`. The Pub/Sub Invoker SA is only created when `ENABLE_MARKETPLACE=true` (the default).

## Prerequisites

- [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) installed and authenticated
- GCP project with billing enabled
- Required permissions:
  - Cloud Run Admin
  - Cloud Build Editor
  - Secret Manager Admin
  - Service Account Admin

## Quick Start

### 1. Set Environment Variables

```bash
export GOOGLE_CLOUD_PROJECT="your-project-id"
export GOOGLE_CLOUD_LOCATION="us-central1"  # Cloud Run deployment region
export SERVICE_NAME="lightspeed-agent"

# Vertex AI model location (use "global" for pay-as-you-go access).
# This is separate from GOOGLE_CLOUD_LOCATION, which sets the Cloud Run
# deployment region. Defaults to "global" if not set.
# export VERTEXAI_LOCATION="global"

# Optional: use a different name for the GCP service account
# export SERVICE_ACCOUNT_NAME="my-custom-sa"

# Optional: disable Pub/Sub marketplace integration
export ENABLE_MARKETPLACE="false"
```

**Google Cloud Marketplace deployments:** If you are deploying with marketplace
integration (`ENABLE_MARKETPLACE=true`, the default), you **must** set the
following variables **before** running `setup.sh` or `deploy.sh`:

```bash
# Required: managed service name from the Producer Portal (the product-level
# identifier). Used by the handler to approve entitlements via the Procurement
# API and to filter Pub/Sub events by product. You can find it under
# APIs & Services > Endpoints, or by running:
#   gcloud endpoints services list --project=$GOOGLE_CLOUD_PROJECT
export SERVICE_CONTROL_SERVICE_NAME="<service-name>.endpoints.<project-id>.cloud.goog"

# Required: fully-qualified Pub/Sub topic provided by Google Cloud Marketplace
export PUBSUB_TOPIC="projects/<marketplace-project>/topics/<your-marketplace-topic>"

# Required when using a fully-qualified topic: the subscription name is derived
# from the topic by default (appending "-sub"), which produces an invalid name
# when the topic is a fully-qualified path.
export PUBSUB_SUBSCRIPTION="marketplace-events-sub"
```

If `SERVICE_CONTROL_SERVICE_NAME` is not set, the handler will skip
entitlement approval and subscriptions will remain pending in the Google
Cloud console.

If `PUBSUB_TOPIC` is not set, the scripts default to creating a local topic
named `marketplace-entitlements`, which does **not** receive events from the
marketplace. Orders will remain stuck in `pending` status because
entitlement approval events never reach the handler.

### 2. Run Setup Script

The setup script enables required APIs, creates service accounts (runtime + Pub/Sub invoker), and sets up secrets:

```bash
./deploy/cloudrun/setup.sh
```

**Environment variables:**
| Variable | Default | Description |
|----------|---------|-------------|
| `GOOGLE_CLOUD_PROJECT` | (required) | GCP project ID |
| `GOOGLE_CLOUD_LOCATION` | `us-central1` | Cloud Run deployment region |
| `VERTEXAI_LOCATION` | `global` | Vertex AI model location (use `global` for pay-as-you-go) |
| `SERVICE_NAME` | `lightspeed-agent` | Cloud Run service name |
| `SERVICE_ACCOUNT_NAME` | `${SERVICE_NAME}` | GCP service account name (allows a different name than the Cloud Run service) |
| `HANDLER_SERVICE_NAME` | `marketplace-handler` | Marketplace handler Cloud Run service name |
| `DB_INSTANCE_NAME` | `lightspeed-agent-db` | Cloud SQL instance name |
| `VPC_CONNECTOR_NAME` | `lightspeed-redis-conn` | Serverless VPC Access connector for Redis |
| `PUBSUB_INVOKER_NAME` | `pubsub-invoker` | Pub/Sub invoker SA name |
| `PUBSUB_TOPIC` | `marketplace-entitlements` | Pub/Sub topic for marketplace events. **Must** be set to the fully-qualified topic from Google Cloud Marketplace for production deployments. See [Set Environment Variables](#1-set-environment-variables). |
| `PUBSUB_SUBSCRIPTION` | `${PUBSUB_TOPIC}-sub` | Pub/Sub subscription name. **Must** be set explicitly when `PUBSUB_TOPIC` is a fully-qualified path, since the default derivation produces an invalid name. |
| `SERVICE_CONTROL_SERVICE_NAME` | - | Managed service name from the Producer Portal. **Required** for marketplace deployments — used for entitlement approval and product-level event filtering. |
| `ENABLE_MARKETPLACE` | `true` | Create Pub/Sub invoker SA and topic for marketplace integration |

### 3. Set Up Cloud SQL Database

Cloud Run requires PostgreSQL for production. Create a Cloud SQL instance with two databases:

```bash
# Create Cloud SQL instance (using smallest Enterprise tier)
gcloud sql instances create $DB_INSTANCE_NAME \
  --database-version=POSTGRES_16 \
  --edition=ENTERPRISE \
  --tier=db-g1-small \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT \
  --ssl-mode=ENCRYPTED_ONLY

# Generate random passwords for database users
MARKETPLACE_DB_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
SESSION_DB_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
echo "Marketplace DB password: $MARKETPLACE_DB_PASSWORD"
echo "Session DB password: $SESSION_DB_PASSWORD"
# Save these — you'll need them for the database-url secrets below

# Create marketplace database and user
gcloud sql databases create lightspeed_agent \
  --instance=$DB_INSTANCE_NAME \
  --project=$GOOGLE_CLOUD_PROJECT

gcloud sql users create insights \
  --instance=$DB_INSTANCE_NAME \
  --password=$MARKETPLACE_DB_PASSWORD \
  --project=$GOOGLE_CLOUD_PROJECT

# Create session database and user
gcloud sql databases create agent_sessions \
  --instance=$DB_INSTANCE_NAME \
  --project=$GOOGLE_CLOUD_PROJECT

gcloud sql users create sessions \
  --instance=$DB_INSTANCE_NAME \
  --password=$SESSION_DB_PASSWORD \
  --project=$GOOGLE_CLOUD_PROJECT

# Get the connection name for later use
CONNECTION_NAME=$(gcloud sql instances describe $DB_INSTANCE_NAME \
  --project=$GOOGLE_CLOUD_PROJECT --format='value(connectionName)')
echo "Connection name: $CONNECTION_NAME"
```

### 4. Redis Setup for Rate Limiting

The agent uses Redis for distributed rate limiting. On Cloud Run, use **Cloud Memorystore for Redis** with a **Serverless VPC Access connector** so the agent can reach the Redis instance.

**Step 1: Create a VPC connector** (if you don't have one):

```bash
# Create a Serverless VPC Access connector in the same region as Cloud Run
# Use the default network or your custom VPC. The subnet range must not overlap with existing subnets.
# Check available ranges: gcloud compute networks subnets list --network=default --filter="region:$GOOGLE_CLOUD_LOCATION"
gcloud compute networks vpc-access connectors create lightspeed-redis-conn \
  --region=$GOOGLE_CLOUD_LOCATION \
  --network=default \
  --range=10.8.0.0/28 \
  --project=$GOOGLE_CLOUD_PROJECT
```

**Step 2: Create a Redis instance** in the same VPC network:

```bash
# Create a Basic tier Redis instance (smallest, cost-effective for rate limiting)
gcloud redis instances create lightspeed-redis \
  --size=1 \
  --region=$GOOGLE_CLOUD_LOCATION \
  --redis-version=redis_7_0 \
  --network=default \
  --project=$GOOGLE_CLOUD_PROJECT

# Get the Redis host IP
REDIS_HOST=$(gcloud redis instances describe lightspeed-redis \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT \
  --format='value(host)')
echo "Redis host: $REDIS_HOST"
```

**Step 3: Store the Redis URL in Secret Manager**:

```bash
# Redis uses port 6379 by default
echo -n "redis://${REDIS_HOST}:6379/0" | \
  gcloud secrets versions add rate-limit-redis-url --data-file=- --project=$GOOGLE_CLOUD_PROJECT
```

**Step 4: Set the VPC connector name** (if different from default):

```bash
# Default is lightspeed-redis-conn; override if you used a different name
export VPC_CONNECTOR_NAME="lightspeed-redis-conn"
```

See [Connect to Redis from Cloud Run](https://cloud.google.com/run/docs/integrate/redis-memorystore) for more details.

### 5. Configure Secrets

Update the placeholder secrets with actual values:

```bash
# Red Hat SSO credentials
echo -n 'your-sso-client-id' | \
  gcloud secrets versions add redhat-sso-client-id --data-file=- --project=$GOOGLE_CLOUD_PROJECT

echo -n 'your-sso-client-secret' | \
  gcloud secrets versions add redhat-sso-client-secret --data-file=- --project=$GOOGLE_CLOUD_PROJECT

# DCR (Dynamic Client Registration) - Required for Gemini Enterprise integration
# GMA SSO API credentials for tenant creation
echo -n 'your-gma-client-id' | \
  gcloud secrets versions add gma-client-id --data-file=- --project=$GOOGLE_CLOUD_PROJECT
echo -n 'your-gma-client-secret' | \
  gcloud secrets versions add gma-client-secret --data-file=- --project=$GOOGLE_CLOUD_PROJECT

# Fernet encryption key for DCR client secrets
# Generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
echo -n 'your-fernet-key' | \
  gcloud secrets versions add dcr-encryption-key --data-file=- --project=$GOOGLE_CLOUD_PROJECT

# Database URLs (use CONNECTION_NAME and passwords from step 3)
# Marketplace database: stores orders, entitlements, DCR clients
echo -n "postgresql+asyncpg://insights:$MARKETPLACE_DB_PASSWORD@/lightspeed_agent?host=/cloudsql/$CONNECTION_NAME" | \
  gcloud secrets versions add database-url --data-file=- --project=$GOOGLE_CLOUD_PROJECT

# Session database: stores agent sessions (required for persistence)
echo -n "postgresql+asyncpg://sessions:$SESSION_DB_PASSWORD@/agent_sessions?host=/cloudsql/$CONNECTION_NAME" | \
  gcloud secrets versions add session-database-url --data-file=- --project=$GOOGLE_CLOUD_PROJECT

# Rate limit Redis URL (required). As instructed in Redis Setup step 3 after creating the Redis instance.
# REDIS_HOST=$(gcloud redis instances describe lightspeed-redis --region=$GOOGLE_CLOUD_LOCATION --project=$GOOGLE_CLOUD_PROJECT --format='value(host)')
# echo -n "redis://${REDIS_HOST}:6379/0" | gcloud secrets versions add rate-limit-redis-url --data-file=- --project=$GOOGLE_CLOUD_PROJECT
```

### 6. Copy MCP Image to GCR

Cloud Run doesn't support Quay.io directly. Copy the MCP server image to GCR.

**Authenticate to GCR first:**

```bash
# Authenticate your container runtime to gcr.io using gcloud
gcloud auth print-access-token | docker login -u oauth2accesstoken --password-stdin gcr.io
```

If you're using **Podman** instead of Docker:

```bash
gcloud auth print-access-token | podman login -u oauth2accesstoken --password-stdin gcr.io
```

Podman stores the resulting credentials in `${XDG_RUNTIME_DIR}/containers/auth.json` (typically `/run/user/$UID/containers/auth.json`). You can verify the login succeeded with:

```bash
cat ${XDG_RUNTIME_DIR}/containers/auth.json
```

**Pull, tag, and push:**

```bash
# Pull from Quay.io
docker pull quay.io/redhat-services-prod/insights-management-tenant/insights-mcp/red-hat-lightspeed-mcp:latest

# Tag and push to GCR
docker tag quay.io/redhat-services-prod/insights-management-tenant/insights-mcp/red-hat-lightspeed-mcp:latest \
  gcr.io/$GOOGLE_CLOUD_PROJECT/red-hat-lightspeed-mcp:latest
docker push gcr.io/$GOOGLE_CLOUD_PROJECT/red-hat-lightspeed-mcp:latest
```

### 7. Deploy

The agent's AgentCard advertises the DCR endpoints served by the
marketplace-handler service. Because of this, the **handler must be
deployed first** so its URL is known when the agent is configured.

**Step 1: Deploy the marketplace handler**

```bash
./deploy/cloudrun/deploy.sh --service handler --allow-unauthenticated
```

**Step 2: Get the handler URL and set `MARKETPLACE_HANDLER_URL`**

```bash
# Get the marketplace handler URL
HANDLER_URL=$(gcloud run services describe ${HANDLER_SERVICE_NAME:-marketplace-handler} \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT \
  --format='value(status.url)')
echo "Handler URL: $HANDLER_URL"

# Export it so deploy.sh can set it on the agent service
export MARKETPLACE_HANDLER_URL="$HANDLER_URL"
```

**Step 3: Deploy the agent**

The deploy script automatically sets `AGENT_PROVIDER_URL` (agent base URL)
and `MARKETPLACE_HANDLER_URL` on the agent service using the actual
Cloud Run URLs after deployment. `AGENT_PROVIDER_ORGANIZATION_URL`
(the provider's website, used as the JWT audience for DCR) is set in the
YAML configs and does not change per deployment.

```bash
./deploy/cloudrun/deploy.sh --service agent --allow-unauthenticated
```

After deployment, verify the AgentCard DCR endpoints point to the handler:

```bash
AGENT_URL=$(gcloud run services describe ${SERVICE_NAME:-lightspeed-agent} \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT \
  --format='value(status.url)')
curl -s $AGENT_URL/.well-known/agent.json | jq '.capabilities.extensions'
```

**Other examples:**

```bash
# Deploy only the agent with a custom image
./deploy/cloudrun/deploy.sh --service agent --image gcr.io/my-project/lightspeed-agent:v1.0

# Deploy the handler with a custom image
./deploy/cloudrun/deploy.sh --service handler --handler-image gcr.io/my-project/marketplace-handler:v1.0
```

**Deploy script options:**

| Flag | Description |
|------|-------------|
| `--service <service>` | Which service to deploy: `all` (default), `handler`, `agent` |
| `--image <image>` | Container image for the agent (default: `gcr.io/$PROJECT_ID/lightspeed-agent:latest`) |
| `--handler-image <image>` | Container image for the marketplace handler (default: `gcr.io/$PROJECT_ID/marketplace-handler:latest`) |
| `--mcp-image <image>` | Container image for the MCP server (default: `gcr.io/$PROJECT_ID/red-hat-lightspeed-mcp:latest`) |
| `--build` | Build the image(s) before deploying |
| `--allow-unauthenticated` | Allow public access (required for A2A and Pub/Sub) |

**Service deployment:**

| Service | YAML Config | Description |
|---------|-------------|-------------|
| `handler` | `marketplace-handler.yaml` | Pub/Sub events, DCR requests |
| `agent` | `service.yaml` | A2A queries with MCP sidecar |
| `all` | Both | Deploy both services |

The deploy script performs variable substitution on the YAML configs
(`${PROJECT_ID}`, `${REGION}`, image references, etc.) and deploys using
`gcloud run services replace`. For manual
deployment without the script, substitute all `${...}` variables in the YAML before running
`gcloud run services replace`:

```bash
sed -e "s|\${PROJECT_ID}|$GOOGLE_CLOUD_PROJECT|g" \
    -e "s|\${REGION}|$GOOGLE_CLOUD_LOCATION|g" \
    -e "s|\${VERTEXAI_LOCATION}|${VERTEXAI_LOCATION:-global}|g" \
    -e "s|\${DB_INSTANCE_NAME}|${DB_INSTANCE_NAME:-lightspeed-agent-db}|g" \
    -e "s|\${VPC_CONNECTOR_NAME}|${VPC_CONNECTOR_NAME:-lightspeed-redis-conn}|g" \
    -e "s|\${SERVICE_NAME}|${SERVICE_NAME:-lightspeed-agent}|g" \
    -e "s|\${SERVICE_ACCOUNT_NAME}|${SERVICE_ACCOUNT_NAME:-lightspeed-agent}|g" \
    -e "s|\${MCP_IMAGE}|${MCP_IMAGE:-gcr.io/$GOOGLE_CLOUD_PROJECT/insights-mcp:latest}|g" \
    deploy/cloudrun/service.yaml | \
    gcloud run services replace - --region=$GOOGLE_CLOUD_LOCATION --project=$GOOGLE_CLOUD_PROJECT
```

## Service Configuration

### Agent Container

| Setting | Value | Description |
|---------|-------|-------------|
| CPU | 2 | vCPUs allocated |
| Memory | 2Gi | Memory limit |
| Port | 8000 | Container port |

### Rate Limiting (Redis)

The agent uses Cloud Memorystore for Redis for distributed rate limiting. Required configuration:

| Variable | Source | Description |
|----------|--------|-------------|
| `RATE_LIMIT_REDIS_URL` | Secret `rate-limit-redis-url` | Redis connection URL (e.g. `redis://10.x.x.x:6379/0`) |
| `RATE_LIMIT_REDIS_TIMEOUT_MS` | Env | Redis operation timeout (default: 200) |
| `RATE_LIMIT_KEY_PREFIX` | Env | Key prefix for rate limit keys |
| `RATE_LIMIT_REQUESTS_PER_MINUTE` | Env | Max requests per minute per principal |
| `RATE_LIMIT_REQUESTS_PER_HOUR` | Env | Max requests per hour per principal |

The service uses a VPC connector to reach the Redis instance. Set `VPC_CONNECTOR_NAME` (default: `lightspeed-redis-conn`) when deploying. See [Rate Limiting — Testing](../../docs/rate-limiting.md#testing-rate-limiting) for how to validate rate limiting.

### MCP Output Size Guard

MCP tools can return very large responses (e.g., listing all advisories or inventory systems),
which inflate the LLM input context and may trigger Vertex AI token-per-minute (TPM) rate limits
(HTTP 429 `RESOURCE_EXHAUSTED`).

The agent includes an **MCP output size guard** that detects oversized tool results and replaces
them with an actionable message. Instead of sending the full payload to the LLM, the agent tells
the model the result was too large and asks it to guide the user toward narrowing down their query
or using pagination.

**Configuration:**

| Variable | Default | Description |
|----------|---------|-------------|
| `TOOL_RESULT_MAX_CHARS` | `51200` | Maximum character length for MCP tool results. Results exceeding this are replaced with guidance. Set to `0` to disable. |

**To adjust the limit on Cloud Run:**

```bash
# Allow larger results (e.g., 100K characters)
gcloud run services update lightspeed-agent \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT \
  --set-env-vars TOOL_RESULT_MAX_CHARS=100000

# Disable the guard entirely (not recommended — may cause 429 errors)
gcloud run services update lightspeed-agent \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT \
  --set-env-vars TOOL_RESULT_MAX_CHARS=0
```

**How it works:**

1. An MCP tool executes and returns a result
2. The `MCPOutputSizeGuardPlugin` serializes the result and checks its character length
3. If the result exceeds `TOOL_RESULT_MAX_CHARS`, it is replaced with:
   ```json
   {
     "error": "tool_result_too_large",
     "message": "The tool 'get_advisories' returned a result that is too large to process (270,000 characters, limit is 51,200). Please ask the user to narrow down their query or use pagination/filtering parameters to reduce the result size.",
     "original_size_chars": 270000,
     "limit_chars": 51200
   }
   ```
4. The LLM receives this message and can inform the user to refine their request

**Tuning tips:**

- **51,200 characters** (default, 50 KiB) is a conservative limit that keeps input tokens well within
  Vertex AI TPM quotas for `gemini-2.5-flash`
- If you have higher TPM quotas, increase the limit to allow richer responses
- The optimal limit depends on the model's context window and expected session length — longer
  multi-turn sessions accumulate more context, leaving less room for individual tool results.
  Short single-turn sessions can tolerate a higher limit.
- Monitor the `Tool result too large` warning logs to see which tools trigger the guard
  and how often

See [Configuration — MCP Output Size Guard](../../docs/configuration.md#mcp-output-size-guard) for more details.

### MCP Server Sidecar

| Setting | Value | Description |
|---------|-------|-------------|
| CPU | 1 | vCPUs allocated |
| Memory | 512Mi | Memory limit |
| Port | 8080 | Internal MCP port |
| Image | `gcr.io/$PROJECT_ID/red-hat-lightspeed-mcp:latest` | MCP server image (copied from Quay.io) |

### Copying the MCP Image to GCR

Cloud Run doesn't support pulling images directly from Quay.io. You must copy the MCP server image to Google Container Registry (GCR) before deploying:

```bash
# Pull from Quay.io locally
docker pull quay.io/redhat-services-prod/insights-management-tenant/insights-mcp/red-hat-lightspeed-mcp:latest

# Tag for GCR
docker tag quay.io/redhat-services-prod/insights-management-tenant/insights-mcp/red-hat-lightspeed-mcp:latest \
  gcr.io/$GOOGLE_CLOUD_PROJECT/red-hat-lightspeed-mcp:latest

# Push to GCR
docker push gcr.io/$GOOGLE_CLOUD_PROJECT/red-hat-lightspeed-mcp:latest
```

This step is required before running `deploy.sh`. The deploy script defaults to `gcr.io/$PROJECT_ID/red-hat-lightspeed-mcp:latest`.

**To update the MCP server**, repeat the above steps with a new tag or `:latest`.

**Costs (GCR):**
| Cost Type | Rate | Notes |
|-----------|------|-------|
| Storage | $0.026/GB/month | ~$0.005/month for a 200MB image |
| Network egress | Standard GCP rates | Free within same region |
| Requests | No charge | Pull requests are free |

### Customizing MCP Server Configuration

The MCP server configuration is hardcoded in `deploy/cloudrun/service.yaml` because Cloud Run does not support environment variable expansion in the `args` field (unlike Kubernetes/Podman).

**Current MCP server settings:**
```yaml
args:
  - "--readonly"      # Run in read-only mode
  - "http"            # Use HTTP transport
  - "--port"
  - "8080"            # Listen on port 8080
  - "--host"
  - "0.0.0.0"         # Bind to all interfaces
```

**To change MCP server settings:**

1. Edit `deploy/cloudrun/service.yaml` directly:
   ```bash
   vim deploy/cloudrun/service.yaml
   # Find the "insights-mcp" container section
   # Modify the args array as needed
   ```

2. Common customizations:
   - **Change port**: Modify `"8080"` to your desired port (also update `MCP_SERVER_URL` in the agent container env)
   - **Enable write operations**: Remove `"--readonly"` flag (not recommended for production)
   - **Change transport**: Modify `"http"` to `"sse"` or `"stdio"` (requires corresponding agent changes)

3. Redeploy after making changes:
   ```bash
   ./deploy/cloudrun/deploy.sh --service agent
   ```

**Note**: If you change the MCP server port, you must also update the `MCP_SERVER_URL` environment variable in the agent container to match.

### Alternative: Use Docker Hub

Instead of GCR, you can copy the image to Docker Hub (free storage, but has rate limits):

```bash
# Pull from Quay.io
docker pull quay.io/redhat-services-prod/insights-management-tenant/insights-mcp/red-hat-lightspeed-mcp:latest

# Tag for Docker Hub (replace YOUR_USERNAME with your Docker Hub username)
docker tag quay.io/redhat-services-prod/insights-management-tenant/insights-mcp/red-hat-lightspeed-mcp:latest \
  docker.io/YOUR_USERNAME/red-hat-lightspeed-mcp:latest

# Login and push to Docker Hub
docker login
docker push docker.io/YOUR_USERNAME/red-hat-lightspeed-mcp:latest

# Deploy with Docker Hub image
./deploy/cloudrun/deploy.sh --mcp-image docker.io/YOUR_USERNAME/red-hat-lightspeed-mcp:latest
```

**Docker Hub Rate Limits:**
| Account Type | Pull Limit | Cost |
|--------------|------------|------|
| Anonymous | 100 pulls / 6 hours | Free |
| Free (authenticated) | 200 pulls / 6 hours | Free |
| Pro | 5,000 pulls / day | $5/month |
| Team | Unlimited | $9/user/month |

**When to use Docker Hub:**
- Development or low-traffic deployments
- You already have a Docker Hub account

**When to use GCR (recommended for production):**
- Auto-scaling deployments (rate limits could cause failures)
- High availability requirements
- Cost is negligible (~$0.005/month)

### Scaling

| Setting | Value | Description |
|---------|-------|-------------|
| Min Instances | 0 | Scale to zero when idle |
| Max Instances | 10 | Maximum concurrent instances |
| Concurrency | 80 | Requests per instance |
| Timeout | 300s | Request timeout |

## How the MCP Server Works

The MCP server runs as a sidecar container alongside the agent:

1. **Agent Container** (port 8000): Handles A2A requests, uses Gemini for AI
2. **MCP Server Container** (port 8080): Provides tools for Red Hat Insights APIs

When the agent needs to access Insights data (e.g., system vulnerabilities, recommendations):
1. Agent calls MCP tools via HTTP to `localhost:8080`
2. Agent forwards credentials to the MCP server via HTTP headers (see below)
3. MCP server authenticates with console.redhat.com
4. MCP server calls the appropriate Insights API
5. Results are returned to the agent for processing

### MCP Authentication

The agent forwards the caller's JWT token to the MCP server via the
`Authorization: Bearer` header. The MCP server uses this token to call
console.redhat.com APIs on behalf of the user.

### Credential Flow

```
Client                     Agent                   MCP Server        console.redhat.com
  │                          │                         │                     │
  │  POST / (A2A)            │                         │                     │
  │  Authorization: Bearer T │                         │                     │
  ├─────────────────────────►│                         │                     │
  │                          │  MCP tool call          │                     │
  │                          │  Authorization: Bearer T│                     │
  │                          ├────────────────────────►│                     │
  │                          │                         │  API Request + T    │
  │                          │                         ├────────────────────►│
  │                          │                         │  API Response       │
  │                          │                         │◄────────────────────┤
  │                          │  Tool result            │                     │
  │                          │◄────────────────────────┤                     │
  │  A2A Response            │                         │                     │
  │◄─────────────────────────┤                         │                     │
```

## Authentication

The agent uses **Red Hat SSO** for authentication via **token
introspection** (RFC 7662).  Requests to the A2A endpoint (POST /) require a
Bearer token that is active and carries the `api.console` and `api.ocm` scopes.

### Authentication Flow

```
┌──────────┐    ┌───────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐
│  Client  │    │Lightspeed Agt │    │ Red Hat SSO  │    │  MCP Server  │    │console.redhat.com│
│(Gemini)  │    │  (port 8000)  │    │ (Red Hat SSO)│    │  (port 8080) │    │ (Insights APIs)  │
└────┬─────┘    └──────┬────────┘    └──────┬───────┘    └──────┬───────┘    └────────┬─────────┘
     │                 │                    │                   │                     │
     │  ── Obtain Token (directly from SSO) ──                 │                     │
     │                 │                    │                   │                     │
     │ 1. client_credentials grant         │                   │                     │
     ├─────────────────────────────────────►│                   │                     │
     │ 2. Access token                     │                   │                     │
     │◄────────────────────────────────────-┤                   │                     │
     │                 │                    │                   │                     │
     │  ── A2A Request with Tool Call ──    │                   │                     │
     │                 │                    │                   │                     │
     │ 3. POST / (A2A) │                    │                   │                     │
     │    Bearer token │                    │                   │                     │
     ├────────────────►│ 4. Introspect      │                   │                     │
     │                 │    token + check   │                   │                     │
     │                 │    required scopes │                   │                     │
     │                 ├───────────────────►│                   │                     │
     │                 │                    │                   │                     │
     │                 │ 5. MCP tool call   │                   │                     │
     │                 │  + Bearer token    │                   │                     │
     │                 ├───────────────────────────────────────►│                     │
     │                 │                    │                   │ 6. Insights API     │
     │                 │                    │                   │    (using token)    │
     │                 │                    │                   ├────────────────────►│
     │                 │                    │                   │ 7. API response     │
     │                 │                    │                   │◄────────────────────┤
     │                 │ 8. Tool result     │                   │                     │
     │                 │◄──────────────────────────────────────-┤                     │
     │ 9. A2A Response │                    │                   │                     │
     │◄────────────────┤                    │                   │                     │
```

**Credential sets:**
- **Red Hat SSO credentials** (`RED_HAT_SSO_CLIENT_ID/SECRET`): Used by the agent as Resource Server credentials for token introspection (step 4)
- **MCP authentication** (step 5): The caller's Bearer token is forwarded to the MCP server (see [MCP Authentication](#mcp-authentication))

### Configuration

| Secret / Env Var | Description |
|------------------|-------------|
| `redhat-sso-client-id` | Resource Server client ID (used for token introspection) |
| `redhat-sso-client-secret` | Resource Server client secret |
| `MARKETPLACE_HANDLER_URL` | URL of the marketplace-handler service. Used to build the DCR endpoints in the AgentCard. If empty, falls back to `AGENT_PROVIDER_URL`. Set automatically by `deploy.sh`. |
| `AGENT_PROVIDER_ORGANIZATION_URL` | Provider's organization website URL (default: `https://www.redhat.com`). Used in AgentCard `provider.url` and as the expected JWT audience for Google DCR `software_statement` validation. Set in YAML configs, not changed by `deploy.sh`. |
| `AGENT_REQUIRED_SCOPE` | Comma-separated OAuth scopes required in tokens (default: `api.console,api.ocm`) |
| `AGENT_ALLOWED_SCOPES` | Comma-separated allowlist of permitted scopes (default: `openid,profile,email,api.console,api.ocm`). Tokens with scopes outside this list are rejected (403). |

### Development Mode

Set `SKIP_JWT_VALIDATION=true` to disable token introspection for local
development.  The agent still extracts the Bearer token from the request and
forwards it to the MCP server (JWT pass-through continues to work).  Requests
without a Bearer token are also allowed.

## Endpoints

After deployment, the following endpoints are available:

### Marketplace Handler Service

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /ready` | Readiness check |
| `POST /dcr` | Hybrid endpoint (Pub/Sub events + DCR requests) |

### Lightspeed Agent Service

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /ready` | Readiness check |
| `GET /.well-known/agent.json` | A2A AgentCard (public) |
| `POST /` | A2A JSON-RPC endpoint (message/send, message/stream) |

## Testing the Deployment

```bash
# Get service URLs
HANDLER_URL=$(gcloud run services describe marketplace-handler \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT \
  --format='value(status.url)')

AGENT_URL=$(gcloud run services describe lightspeed-agent \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT \
  --format='value(status.url)')

# Test marketplace handler health
curl $HANDLER_URL/health

# Test agent health
curl $AGENT_URL/health

# Get AgentCard (public endpoint)
curl $AGENT_URL/.well-known/agent.json

# View logs for each service
gcloud run services logs read marketplace-handler \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT

gcloud run services logs read lightspeed-agent \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT
```

## Database Architecture

Cloud Run deployments **require PostgreSQL** (Cloud SQL) for production. The system uses **two databases** for security isolation:

| Database | Purpose | Service |
|----------|---------|---------|
| Marketplace DB | Orders, entitlements, DCR clients | Both handler and agent |
| Session DB | ADK agent sessions | Agent only |

This separation ensures:
- Agent sessions cannot access marketplace/auth data
- Compromised agents cannot access DCR credentials
- Different retention policies can be applied

> **Setup:** See [Step 3. Set Up Cloud SQL Database](#3-set-up-cloud-sql-database) in Quick Start.

### Adding Cloud SQL to Existing Services

If you deployed services before setting up Cloud SQL, add the connection:

```bash
CONNECTION_NAME=$(gcloud sql instances describe $DB_INSTANCE_NAME \
  --project=$GOOGLE_CLOUD_PROJECT --format='value(connectionName)')

# Add to marketplace handler
gcloud run services update marketplace-handler \
  --add-cloudsql-instances=$CONNECTION_NAME \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT

# Add to insights agent
gcloud run services update lightspeed-agent \
  --add-cloudsql-instances=$CONNECTION_NAME \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT
```

### Session Database Behavior

- If `SESSION_DATABASE_URL` is set: Uses PostgreSQL for session persistence
- If `SESSION_DATABASE_URL` is not set: Uses in-memory storage (sessions lost on restart)

For production, always configure `SESSION_DATABASE_URL` to ensure session persistence across container restarts and scaling events.

## Custom Domain

Map a custom domain to your Cloud Run service:

```bash
gcloud run domain-mappings create \
  --service=lightspeed-agent \
  --domain=agent.yourdomain.com \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT
```

Follow the instructions to verify domain ownership and configure DNS.

## Testing the Agent

Once deployed, you can test the agent using a local proxy that handles Google Cloud Run authentication.

### Test Agent Card

Verify the agent is running and accessible:

```bash
# Get the agent URL
AGENT_URL=$(gcloud run services describe lightspeed-agent \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT \
  --format='value(status.url)')

# Test agent card endpoint (requires authentication)
curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  $AGENT_URL/.well-known/agent-card.json | jq .
```

### Test A2A Requests with Local Proxy

The local proxy handles Google Cloud Run authentication, allowing you to test with just your Red Hat SSO token.

**Important:** The MCP sidecar inside Cloud Run uses port 8080. To avoid port conflicts, run the proxy on a different port (e.g., 8099).

**1. Start the local proxy:**

```bash
# Start proxy on localhost:8099 (NOT 8080 - that's used by MCP sidecar)
gcloud run services proxy lightspeed-agent \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT \
  --port=8099
```

This command will keep running in your terminal. The proxy authenticates all requests to Cloud Run using your current `gcloud` credentials.

**2. Configure AGENT_PROVIDER_URL for local testing:**

The agent card needs to advertise the proxy URL so tools like A2A Inspector connect to it:

```bash
# In a new terminal, set the agent URL to point to your local proxy
gcloud run services update lightspeed-agent \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT \
  --update-env-vars="AGENT_PROVIDER_URL=http://localhost:8099"

# Wait for the update to complete (takes ~30 seconds)
# The proxy automatically handles the connection to Cloud Run
```

**Important:** This makes the agent advertise itself as `http://localhost:8099/` to ALL clients. This is fine for local testing, but remember to restore the real URL when done (see cleanup section below).

**3. Get a Red Hat SSO access token:**

In a new terminal, use one of these methods:

**Option A: Using `ocm` CLI (Easiest)**

If you have the [ocm CLI](https://github.com/openshift-online/ocm-cli) installed:

```bash
# Login to OCM (if not already logged in)
ocm login --use-auth-code

# Get access token
export RED_HAT_TOKEN=$(ocm token)

# Verify token is valid
echo $RED_HAT_TOKEN | cut -d. -f2 | base64 -d 2>/dev/null | jq .
```

**4. Test the A2A endpoint:**

The agent uses the A2A (Agent-to-Agent) protocol, which is based on JSON-RPC 2.0. All requests must include:
- `jsonrpc`: "2.0"
- `method`: "message/send" (for non-streaming) or "message/stream" (for streaming)
- `params`: Contains the message object with `messageId`
- `id`: Unique request identifier

```bash
# Send a test message to the agent (note: using port 8099, not 8080)
curl -X POST http://localhost:8099/ \
  -H "Authorization: Bearer $RED_HAT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message/send",
    "params": {
      "message": {
        "messageId": "1",
        "role": "user",
        "parts": [{"type": "text", "text": "What are the latest CVEs affecting my systems?"}]
      }
    },
    "id": "1"
  }' | jq .
```

**Expected response:**

```json
{
  "jsonrpc": "2.0",
  "result": {
    "id": "task-abc123",
    "status": {
      "state": "completed"
    },
    "artifacts": [
      {
        "parts": [
          {
            "type": "text",
            "text": "Based on your systems, here are the latest CVEs..."
          }
        ]
      }
    ]
  },
  "id": "1"
}
```

**5. Test other endpoints:**

```bash
# Check health endpoint (no auth required)
curl http://localhost:8099/health | jq .

# Get agent card (should show http://localhost:8099/)
curl http://localhost:8099/.well-known/agent-card.json | jq -r '.url'
```

### Test with A2A Inspector

The [A2A Inspector](https://github.com/a2aproject/a2a-inspector) provides a web-based UI for testing A2A agents.

**1. Prerequisites:**

```bash
# Make sure the proxy is running (from step 1 above)
# Make sure AGENT_PROVIDER_URL is set to http://localhost:8099 (from step 2 above)
# Make sure you have a Red Hat SSO token (from step 3 above)
```

**2. Start A2A Inspector:**

```bash
# Clone and run A2A Inspector (if not already installed)
git clone https://github.com/a2aproject/a2a-inspector.git /tmp/a2a-inspector
cd /tmp/a2a-inspector
uv sync
npm install -C frontend
./scripts/run.sh  # Usually runs on port 5001
```

**3. Configure A2A Inspector:**

In the A2A Inspector web UI (usually at `http://localhost:5001`):

1. **Agent URL**: Enter `http://localhost:8099/`
2. **Authentication**:
   - Select "Bearer Token" or "OAuth"
   - Paste your Red Hat SSO token: `$(ocm token)`
3. Click "Connect" - it will fetch the agent card from `http://localhost:8099/.well-known/agent-card.json`

The A2A Inspector will read the agent card and see `"url": "http://localhost:8099/"`, which points back to your local proxy. All messages will flow through the proxy to Cloud Run.

**4. Send test messages:**

In the A2A Inspector UI:
- Type: "What are my RHEL systems?"
- Type: "Show CVEs affecting my infrastructure"
- Type: "What is the lifecycle for RHEL 8?"

The inspector will send properly formatted JSON-RPC requests with `messageId` fields automatically.

### Cleanup After Testing

When you're done testing, clean up the local proxy and restore the production configuration.

**1. Restore AGENT_PROVIDER_URL to the real Cloud Run URL:**

```bash
# Get the actual Cloud Run service URL
SERVICE_URL=$(gcloud run services describe lightspeed-agent \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT \
  --format='value(status.url)')

# Restore the agent card to advertise the real Cloud Run URL
gcloud run services update lightspeed-agent \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT \
  --update-env-vars="AGENT_PROVIDER_URL=$SERVICE_URL"

# Verify the agent card now shows the correct URL
curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  $SERVICE_URL/.well-known/agent-card.json | jq -r '.url'
# Should show: https://lightspeed-agent-xxxxx.run.app/
```

**2. Stop the proxy:**

Press `Ctrl+C` in the terminal where the proxy is running.

**3. Clean up port (if needed):**

If the port is still in use:

```bash
# Find and kill process using port 8099
lsof -ti:8099 | xargs kill -9

# Or on systems without lsof
fuser -k 8099/tcp
```

**Note:** The proxy doesn't create any cloud resources - it only runs locally on your machine. Stopping the proxy (Ctrl+C) is sufficient to clean up.

**Why port 8099 instead of 8080?**

The MCP sidecar inside Cloud Run uses port 8080 internally. If you run the proxy on port 8080, the agent will try to connect to the proxy instead of the MCP sidecar, causing "Failed to create MCP session" errors. Using port 8099 (or any other port except 8080) avoids this conflict.

### Testing Without Proxy (Direct Cloud Run Access)

If you prefer to test without the proxy, you'll need to:

1. **Allow unauthenticated access** (requires admin permissions):
   ```bash
   gcloud run services add-iam-policy-binding lightspeed-agent \
     --region=$GOOGLE_CLOUD_LOCATION \
     --project=$GOOGLE_CLOUD_PROJECT \
     --member="allUsers" \
     --role="roles/run.invoker"
   ```

2. **Test directly** with the Cloud Run URL:
   ```bash
   # Get Red Hat SSO token (using ocm or OAuth flow)
   export RED_HAT_TOKEN=$(ocm token)

   curl -X POST $AGENT_URL/ \
     -H "Authorization: Bearer $RED_HAT_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "jsonrpc": "2.0",
       "method": "message/send",
       "params": {
         "message": {
           "messageId": "1",
           "role": "user",
           "parts": [{"type": "text", "text": "Hello"}]
         }
       },
       "id": "1"
     }'
   ```

**Security Note:** Allowing unauthenticated access makes the service publicly accessible. Only use this for development/testing environments, not production.

### Troubleshooting Testing Issues

**"Method Not Allowed" or "detail": "Method Not Allowed"**

This usually means you're testing the endpoint without proper authentication or the request format is incorrect:

```bash
# Make sure you're using the proxy and have a valid token
export RED_HAT_TOKEN=$(ocm token)

# Verify token is valid (should show decoded JWT payload)
echo $RED_HAT_TOKEN | cut -d. -f2 | base64 -d 2>/dev/null | jq .

# Make sure proxy is running
# You should see: "Listening on http://localhost:8080"
gcloud run services proxy lightspeed-agent --region=us-central1 --port=8080
```

**"Invalid Authorization header format"**

The agent expects a Red Hat SSO Bearer token, not a Google Cloud identity token. Make sure:
- You're using `ocm token` or completing the OAuth flow
- The token is a valid JWT from Red Hat SSO
- You're including it as: `-H "Authorization: Bearer $RED_HAT_TOKEN"`

**"Field required" error (e.g. "messageId", "method")**

The A2A protocol requires specific fields. A common mistake is omitting
`messageId` from the message object. Make sure your request includes:

```json
{
  "jsonrpc": "2.0",
  "method": "message/send",
  "params": {
    "message": {
      "messageId": "1",
      "role": "user",
      "parts": [{"type": "text", "text": "Hello"}]
    }
  },
  "id": "1"
}
```

**"Token is missing required scope(s): api.console, api.ocm"**

The agent requires the `api.console` and `api.ocm` scopes in the access token
by default. If your Red Hat SSO client is not configured to issue these scopes,
you will see:

```json
{"jsonrpc":"2.0","error":{"code":-32003,"message":"Forbidden","data":{"detail":"Token is missing required scope(s): api.console, api.ocm"}},"id":null}
```

To temporarily disable the scope check for testing, set `AGENT_REQUIRED_SCOPE`
to an empty string on the agent service:

```bash
gcloud run services update ${SERVICE_NAME:-lightspeed-agent} \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT \
  --update-env-vars="AGENT_REQUIRED_SCOPE="
```

To restore the scope requirement:

```bash
gcloud run services update ${SERVICE_NAME:-lightspeed-agent} \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT \
  --update-env-vars="AGENT_REQUIRED_SCOPE=api.console,api.ocm"
```

This setting is also configurable in `service.yaml` via the
`AGENT_REQUIRED_SCOPE` environment variable.

**"Token carries disallowed scope(s): ..."**

The agent enforces a scope allowlist (`AGENT_ALLOWED_SCOPES`) to prevent tokens
with elevated privileges from being forwarded to downstream services.  If the
token carries scopes not in the allowlist, you will see a 403 error.

Add the missing scope(s) to the allowlist:

```bash
gcloud run services update ${SERVICE_NAME:-lightspeed-agent} \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT \
  --update-env-vars="AGENT_ALLOWED_SCOPES=openid,profile,email,api.console,api.ocm,your.extra.scope"
```

This setting is also configurable in `service.yaml` via the
`AGENT_ALLOWED_SCOPES` environment variable.

**Empty response or connection refused**

- Ensure the proxy is running in a separate terminal
- Verify the agent is deployed and healthy:
  ```bash
  gcloud run services describe lightspeed-agent \
    --region=us-central1 \
    --format='value(status.conditions.status)'
  # Should show: True;True;True
  ```

## Testing DCR on Cloud Run

This section explains how to test the DCR (Dynamic Client Registration) flow
against a deployed marketplace handler using **static credentials** mode.
The caller provides `client_id` and `client_secret` in the DCR request body.

```
┌──────────────┐
│  Test Script │
│  (local)     │
└──────┬───────┘
       │ POST /dcr
       │ (software_statement JWT
       │  + client_id + client_secret)
       │ + Cloud Run ID token
       ▼
┌──────────────────────┐
│  Marketplace Handler │
│  (Cloud Run)         │
│                      │
│  DCR_ENABLED=false   │
│  Validates, stores   │
│  and returns creds   │
└──────────────────────┘
```

Both options require `SKIP_JWT_VALIDATION=true` on the handler to accept test
JWTs not signed by Google's production `cloud-agentspace` service account.

Both options also require a signing service account. Create one if you don't
have one already:

```bash
gcloud iam service-accounts create dcr-test \
  --display-name "DCR test signer" \
  --project=$GOOGLE_CLOUD_PROJECT

# NOTE: GCP may need a few seconds to propagate the new service account.
# If the next command fails with NOT_FOUND, wait ~10 seconds and retry.
sleep 10

gcloud iam service-accounts keys create dcr-test-key.json \
  --iam-account=dcr-test@$GOOGLE_CLOUD_PROJECT.iam.gserviceaccount.com \
  --project=$GOOGLE_CLOUD_PROJECT
```

### Static Credentials Mode

This mode skips GMA SSO API client creation. The caller provides `client_id` and
`client_secret` in the DCR request body alongside the `software_statement`.
The handler validates them (skipped with `SKIP_JWT_VALIDATION=true`), encrypts
and stores them linked to the order, and returns them. No pre-seeding required.

**1. Configure the handler:**

```bash
# Generate and store Fernet encryption key (if not already set)
FERNET_KEY=$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
echo -n "$FERNET_KEY" | \
  gcloud secrets versions add dcr-encryption-key \
    --data-file=- --project=$GOOGLE_CLOUD_PROJECT

# Update handler env vars (deploys a new revision)
gcloud run services update marketplace-handler \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT \
  --update-env-vars="\
DCR_ENABLED=false,\
SKIP_JWT_VALIDATION=true"
```

**2. Run the test script with static credentials:**

```bash
HANDLER_URL=$(gcloud run services describe marketplace-handler \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT \
  --format='value(status.url)')

export MARKETPLACE_HANDLER_URL=$HANDLER_URL
export TEST_SA_KEY_FILE=dcr-test-key.json
export TEST_CLIENT_ID=test-client-id
export TEST_CLIENT_SECRET=test-client-secret
# Generate a fresh order ID for each test run
export TEST_ORDER_ID="order-$(uuidgen || python3 -c 'import uuid; print(uuid.uuid4())')"
# Don't set SKIP_CLOUD_RUN_AUTH -- script fetches an ID token automatically

python scripts/test_deployed_dcr.py
```

The script sends `client_id` and `client_secret` in the request body. The
handler stores them and returns them. The second request verifies idempotency
(same credentials returned for the same order).

> **Note:** If the handler was deployed with `--allow-unauthenticated`, you can
> set `export SKIP_CLOUD_RUN_AUTH=true` to skip ID token authentication.

**3. Restore production configuration:**

```bash
gcloud run services update marketplace-handler \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT \
  --update-env-vars="\
DCR_ENABLED=true,\
SKIP_JWT_VALIDATION=false"
```

#### Admin Tool: seed_dcr_clients.py

The `seed_dcr_clients.py` script is still available as an admin tool for
managing DCR client records in the database (listing, deleting, or bulk
inserting credentials). It is no longer required as a prerequisite for the
static credentials flow.

```bash
# Connect to Cloud SQL first (requires Cloud SQL Auth Proxy)
./cloud-sql-proxy --port 5432 ${GOOGLE_CLOUD_PROJECT}:${GOOGLE_CLOUD_LOCATION}:${DB_INSTANCE_NAME:-lightspeed-agent-db}

# Fetch DATABASE_URL and DCR_ENCRYPTION_KEY from Secret Manager
CLOUD_DB_URL=$(gcloud secrets versions access latest \
  --secret=database-url --project=$GOOGLE_CLOUD_PROJECT)
DB_PASSWORD=$(echo "$CLOUD_DB_URL" | sed -n 's|.*://insights:\([^@]*\)@.*|\1|p')
export DATABASE_URL="postgresql+asyncpg://insights:${DB_PASSWORD}@localhost:5432/lightspeed_agent"
export DCR_ENCRYPTION_KEY=$(gcloud secrets versions access latest \
  --secret=dcr-encryption-key --project=$GOOGLE_CLOUD_PROJECT)

# List existing entries
python scripts/seed_dcr_clients.py list

# Delete an entry
python scripts/seed_dcr_clients.py delete --order-id order-12345 --confirm
```

### Test Script Reference

The test script at `scripts/test_deployed_dcr.py` is configurable via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `MARKETPLACE_HANDLER_URL` | (required) | Cloud Run handler URL |
| `TEST_SA_KEY_FILE` | - | Path to SA key file (Method A) |
| `TEST_SERVICE_ACCOUNT` | - | SA email for IAM API signing (Method B) |
| `PROVIDER_URL` | `https://www.redhat.com` | JWT audience claim (must match handler's `AGENT_PROVIDER_ORGANIZATION_URL`) |
| `SKIP_CLOUD_RUN_AUTH` | `false` | Skip Cloud Run ID token auth |
| `TEST_ORDER_ID` | random UUID | Fixed order ID |
| `TEST_ACCOUNT_ID` | `test-procurement-account-001` | Procurement account ID |
| `TEST_REDIRECT_URIS` | `https://gemini.google.com/callback` | Comma-separated redirect URIs |

## Audit Logging

The agent automatically produces structured audit logs that correlate each user session with Red Hat API requests. When `LOG_FORMAT=json` (the default in Cloud Run), every log record includes:

- **`user_id`** — authenticated user (JWT `sub` claim)
- **`org_id`** — Red Hat organization (JWT `org_id` claim)
- **`order_id`** — Google Cloud Marketplace order
- **`request_id`** — UUID4 correlation ID (unique per request)

Each agent lifecycle event carries an `event_type` tag (`request_authenticated`, `agent_run_started`, `tool_call_completed`, `mcp_jwt_forwarded`, etc.) and tool calls include a `data_source` field identifying which Red Hat Insights MCP tool retrieved the data.

This provides a full data lineage audit trail: every piece of information disclosed by the agent can be traced back to a specific authenticated user prompt and a verified Red Hat Insights data source. These persistent logs are independent of the ephemeral ADK session storage.

### Querying Audit Logs

Cloud Logging automatically parses JSON log fields. To filter logs from the Lightspeed Agent service specifically, add a `resource.labels.service_name` filter:

```bash
# All Lightspeed Agent logs (filter by Cloud Run service name)
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="lightspeed-agent"' \
  --project=$GOOGLE_CLOUD_PROJECT --limit=50

# All actions by a specific user (scoped to the agent service)
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="lightspeed-agent" AND jsonPayload.user_id="<user-id>"' \
  --project=$GOOGLE_CLOUD_PROJECT --limit=50

# All events in a single request (correlation)
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="lightspeed-agent" AND jsonPayload.request_id="<request-id>"' \
  --project=$GOOGLE_CLOUD_PROJECT

# All MCP data access for an organization
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="lightspeed-agent" AND jsonPayload.org_id="<org-id>" AND jsonPayload.message=~"mcp_jwt_forwarded"' \
  --project=$GOOGLE_CLOUD_PROJECT

# All tool calls with data source tracking
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="lightspeed-agent" AND jsonPayload.message=~"tool_call_completed"' \
  --project=$GOOGLE_CLOUD_PROJECT --limit=20
```

No additional configuration is required — audit logging is automatically active when `LOG_FORMAT=json`.

## Monitoring

View metrics in Google Cloud Console:
- **Cloud Run** → **Services** → **lightspeed-agent** → **Metrics**

Set up alerts:
```bash
gcloud monitoring policies create \
  --display-name="Lightspeed Agent Error Rate" \
  --condition-display-name="Error rate > 5%" \
  --condition-filter='resource.type="cloud_run_revision" AND metric.type="run.googleapis.com/request_count" AND metric.labels.response_code_class="5xx"' \
  --project=$GOOGLE_CLOUD_PROJECT
```

## Troubleshooting

### View Logs

```bash
gcloud run services logs read lightspeed-agent \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT \
  --limit=100
```

### Check Service Status

```bash
gcloud run services describe lightspeed-agent \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT
```

### Common Issues

1. **Secret access denied**: Ensure service account has `secretmanager.secretAccessor` role
2. **Container fails to start**: Check logs for missing environment variables
3. **Database connection timeout**: Ensure Cloud SQL connection is configured

### Orders Stuck in Pending Status

If marketplace subscriptions remain in `pending` status in the Google Cloud
console, check the handler logs for one of these messages:

**"SERVICE_CONTROL_SERVICE_NAME not set, skipping approval"** — The handler
is not configured with the managed service name. Set it on the handler:

```bash
gcloud run services update ${HANDLER_SERVICE_NAME:-marketplace-handler} \
  --region=$GOOGLE_CLOUD_LOCATION \
  --project=$GOOGLE_CLOUD_PROJECT \
  --update-env-vars="SERVICE_CONTROL_SERVICE_NAME=<your-service-name>.endpoints.<project-id>.cloud.goog"
```

You can find your managed service name via:

```bash
gcloud endpoints services list --project=$GOOGLE_CLOUD_PROJECT
```

**No events arriving at all** — The Pub/Sub
subscription is likely pointing to the wrong topic. This happens when
`PUBSUB_TOPIC` was not set to the fully-qualified marketplace topic before
deploying. See [Set Environment Variables](#1-set-environment-variables).

To verify and fix:

```bash
# Check which topic the subscription points to
gcloud pubsub subscriptions describe ${PUBSUB_SUBSCRIPTION:-marketplace-entitlements-sub} \
  --project=$GOOGLE_CLOUD_PROJECT \
  --format='yaml(topic, pushConfig.pushEndpoint)'
```

If the topic is wrong, the subscription must be deleted and recreated (the
topic cannot be changed on an existing subscription). The Pub/Sub Invoker SA
(linked in the Marketplace Producer Portal) must be used to create the
subscription because it holds the cross-project permissions on the
marketplace topic:

```bash
export PUBSUB_TOPIC="projects/<marketplace-project>/topics/<your-marketplace-topic>"
export PUBSUB_SUBSCRIPTION="marketplace-events-sub"
export PUBSUB_INVOKER_SA="${PUBSUB_INVOKER_NAME:-pubsub-invoker}@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"

# Delete the old subscription
gcloud pubsub subscriptions delete marketplace-entitlements-sub \
  --project=$GOOGLE_CLOUD_PROJECT --quiet

# Ensure the invoker SA has roles/pubsub.editor in your project
# (setup.sh grants this automatically for new deployments)
gcloud projects add-iam-policy-binding $GOOGLE_CLOUD_PROJECT \
  --member="serviceAccount:$PUBSUB_INVOKER_SA" \
  --role="roles/pubsub.editor" --quiet

# Ensure you can impersonate the invoker SA
gcloud iam service-accounts add-iam-policy-binding "$PUBSUB_INVOKER_SA" \
  --member="user:$(gcloud config get account)" \
  --role="roles/iam.serviceAccountTokenCreator" \
  --project=$GOOGLE_CLOUD_PROJECT --quiet

# Wait a couple of minutes for IAM propagation, then create the subscription
HANDLER_URL=$(gcloud run services describe ${HANDLER_SERVICE_NAME:-marketplace-handler} \
  --region=${GOOGLE_CLOUD_LOCATION:-us-central1} \
  --project=$GOOGLE_CLOUD_PROJECT \
  --format='value(status.url)')

gcloud pubsub subscriptions create "$PUBSUB_SUBSCRIPTION" \
  --topic="$PUBSUB_TOPIC" \
  --push-endpoint="${HANDLER_URL}/dcr" \
  --push-auth-service-account="$PUBSUB_INVOKER_SA" \
  --ack-deadline=60 \
  --project=$GOOGLE_CLOUD_PROJECT \
  --impersonate-service-account="$PUBSUB_INVOKER_SA"
```

Verify the fix:

```bash
gcloud pubsub subscriptions describe "$PUBSUB_SUBSCRIPTION" \
  --project=$GOOGLE_CLOUD_PROJECT \
  --format='yaml(topic, pushConfig.pushEndpoint)'
```

## Cleanup / Teardown

To remove all resources created by the setup and deploy scripts:

```bash
./deploy/cloudrun/cleanup.sh
```

This will delete:
- Cloud Run services (lightspeed-agent, marketplace-handler)
- Pub/Sub topic and subscription
- Secret Manager secrets
- Service accounts (runtime + Pub/Sub invoker) and IAM bindings

Use `--force` to skip the confirmation prompt:

```bash
./deploy/cloudrun/cleanup.sh --force
```

**Note**: The cleanup script does NOT delete container images in GCR or Cloud SQL instances. Delete these separately if needed:

```bash
# Delete container images
gcloud container images delete gcr.io/$GOOGLE_CLOUD_PROJECT/lightspeed-agent --force-delete-tags --quiet
gcloud container images delete gcr.io/$GOOGLE_CLOUD_PROJECT/red-hat-lightspeed-mcp --force-delete-tags --quiet

# Delete Cloud SQL instance (if created)
gcloud sql instances delete INSTANCE_NAME --project=$GOOGLE_CLOUD_PROJECT
```
