#!/bin/bash
# =============================================================================
# Google Cloud Run Deployment Setup Script
# =============================================================================
#
# This script sets up all required GCP services for the Lightspeed Agent:
# - Enables required APIs
# - Creates runtime service account with appropriate permissions
# - Creates Pub/Sub Invoker service account (for push subscription auth)
# - Creates secrets in Secret Manager
# - Creates Pub/Sub topic for marketplace events
#
# Usage:
#   ./deploy/cloudrun/setup.sh
#
# Prerequisites:
#   - gcloud CLI installed and authenticated
#   - GCP project created with billing enabled
#
# =============================================================================

set -euo pipefail

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# =============================================================================
# Configuration
# =============================================================================

# Required: Set these before running
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-lightspeed-agent}"
SERVICE_ACCOUNT_NAME="${SERVICE_ACCOUNT_NAME:-${SERVICE_NAME}}"
HANDLER_SERVICE_NAME="${HANDLER_SERVICE_NAME:-marketplace-handler}"
DB_INSTANCE_NAME="${DB_INSTANCE_NAME:-lightspeed-agent-db}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# Pub/Sub Invoker Service Account (separate SA for push subscription auth)
PUBSUB_INVOKER_NAME="${PUBSUB_INVOKER_NAME:-pubsub-invoker}"
PUBSUB_INVOKER_SA="${PUBSUB_INVOKER_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# Optional features
ENABLE_MARKETPLACE="${ENABLE_MARKETPLACE:-true}"

# Validate required variables
if [[ -z "$PROJECT_ID" ]]; then
    log_error "GOOGLE_CLOUD_PROJECT environment variable is required"
    echo "  export GOOGLE_CLOUD_PROJECT=your-project-id"
    exit 1
fi

log_info "Setting up Cloud Run deployment for project: $PROJECT_ID"
log_info "Region: $REGION"
log_info "Service: $SERVICE_NAME"
log_info "Service account: $SERVICE_ACCOUNT_NAME"
log_info "Handler service: $HANDLER_SERVICE_NAME"
log_info "DB instance: $DB_INSTANCE_NAME"
log_info "Pub/Sub invoker SA: $PUBSUB_INVOKER_NAME"
log_info "Marketplace integration: $ENABLE_MARKETPLACE"

# =============================================================================
# Step 1: Enable Required APIs
# =============================================================================
log_info "Enabling required GCP APIs..."

# Required APIs and their purposes:
# - run: Cloud Run service hosting
# - cloudbuild: Build container images from source
# - secretmanager: Store and access secrets (API keys, credentials)
# - aiplatform: Access Vertex AI / Gemini models
# - cloudscheduler: Schedule usage reporting jobs
# - pubsub: Receive marketplace procurement events
# - servicecontrol: Report usage metrics for billing
# - servicemanagement: Manage service configuration
apis=(
    "run.googleapis.com"
    "cloudbuild.googleapis.com"
    "secretmanager.googleapis.com"
    "aiplatform.googleapis.com"
    "cloudscheduler.googleapis.com"
    "pubsub.googleapis.com"
    "servicecontrol.googleapis.com"
    "servicemanagement.googleapis.com"
)

for api in "${apis[@]}"; do
    log_info "  Enabling $api..."
    gcloud services enable "$api" --project="$PROJECT_ID" --quiet || true
done

# =============================================================================
# Step 2: Create Service Account
# =============================================================================
log_info "Creating service account: $SERVICE_ACCOUNT"

# Create service account if it doesn't exist
if ! gcloud iam service-accounts describe "$SERVICE_ACCOUNT" --project="$PROJECT_ID" &>/dev/null; then
    gcloud iam service-accounts create "$SERVICE_ACCOUNT_NAME" \
        --display-name="Lightspeed Agent Service Account" \
        --description="Service account for the Red Hat Lightspeed Agent for Google Cloud" \
        --project="$PROJECT_ID"
    log_info "Service account created"
else
    log_info "Service account already exists"
fi

# Grant required roles
log_info "Granting IAM roles to service account..."

# IAM roles and their purposes:
# - secretmanager.secretAccessor: Read secrets (API keys, credentials)
# - aiplatform.user: Access Vertex AI / Gemini models
# - pubsub.subscriber: Receive marketplace procurement events
# - pubsub.publisher: Publish events (if needed for async processing)
# - servicemanagement.serviceController: Report usage to Service Control API
# - logging.logWriter: Write logs to Cloud Logging
# - monitoring.metricWriter: Write metrics to Cloud Monitoring
# - cloudsql.client: Connect to Cloud SQL instances
#
# Note: roles/run.invoker is NOT granted here. It is granted to the
# separate Pub/Sub Invoker SA on the marketplace-handler service
# (see deploy.sh). This follows the principle of least privilege.
roles=(
    "roles/secretmanager.secretAccessor"
    "roles/aiplatform.user"
    "roles/pubsub.subscriber"
    "roles/pubsub.publisher"
    "roles/servicemanagement.serviceController"
    "roles/logging.logWriter"
    "roles/monitoring.metricWriter"
    "roles/cloudsql.client"
)

for role in "${roles[@]}"; do
    log_info "  Granting $role..."
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:$SERVICE_ACCOUNT" \
        --role="$role" \
        --quiet || true
done

# =============================================================================
# Step 3: Create Secrets in Secret Manager
# =============================================================================
log_info "Setting up Secret Manager secrets..."

# Required secrets
secrets=(
    "google-api-key"
    "redhat-sso-client-id"
    "redhat-sso-client-secret"
)

# DCR (Dynamic Client Registration) secrets
# Required when DCR_ENABLED=true (default)
dcr_secrets=(
    "dcr-initial-access-token"  # Keycloak IAT for creating OAuth clients
    "dcr-encryption-key"        # Fernet key for encrypting client secrets
)

# Database secrets (PostgreSQL for production - REQUIRED)
db_secrets=(
    "database-url"              # Marketplace DB: postgresql+asyncpg://user:pass@/db?host=/cloudsql/...
    "session-database-url"      # Session DB: postgresql+asyncpg://user:pass@/db?host=/cloudsql/...
)

# Combine all optional secrets
optional_secrets=("${dcr_secrets[@]}" "${db_secrets[@]}")

for secret in "${secrets[@]}"; do
    if ! gcloud secrets describe "$secret" --project="$PROJECT_ID" &>/dev/null; then
        log_info "  Creating secret: $secret"
        echo -n "PLACEHOLDER" | gcloud secrets create "$secret" \
            --data-file=- \
            --project="$PROJECT_ID" \
            --replication-policy="automatic"
        log_warn "  Secret '$secret' created with placeholder value. Update it with:"
        log_warn "    echo -n 'your-value' | gcloud secrets versions add $secret --data-file=- --project=$PROJECT_ID"
    else
        log_info "  Secret '$secret' already exists"
    fi

    # Grant access to service account
    gcloud secrets add-iam-policy-binding "$secret" \
        --member="serviceAccount:$SERVICE_ACCOUNT" \
        --role="roles/secretmanager.secretAccessor" \
        --project="$PROJECT_ID" \
        --quiet || true
done

# Create DCR and database secrets
log_info "Setting up DCR and database secrets..."
for secret in "${optional_secrets[@]}"; do
    if ! gcloud secrets describe "$secret" --project="$PROJECT_ID" &>/dev/null; then
        log_info "  Creating secret: $secret"
        echo -n "PLACEHOLDER" | gcloud secrets create "$secret" \
            --data-file=- \
            --project="$PROJECT_ID" \
            --replication-policy="automatic"
        log_warn "  Secret '$secret' created with placeholder. Update after Cloud SQL setup."
    else
        log_info "  Secret '$secret' already exists"
    fi

    # Grant access to service account
    gcloud secrets add-iam-policy-binding "$secret" \
        --member="serviceAccount:$SERVICE_ACCOUNT" \
        --role="roles/secretmanager.secretAccessor" \
        --project="$PROJECT_ID" \
        --quiet || true
done

# =============================================================================
# Step 4: Create Pub/Sub Invoker Service Account and Topic (Optional)
# =============================================================================
if [[ "$ENABLE_MARKETPLACE" == "true" ]]; then
    log_info "Setting up Pub/Sub for Marketplace integration..."

    # -------------------------------------------------------------------------
    # Create Pub/Sub Invoker Service Account
    # -------------------------------------------------------------------------
    # This is a SEPARATE service account from the runtime SA, used exclusively
    # to authenticate Pub/Sub push subscriptions when invoking Cloud Run.
    # Following the principle of least privilege, it only has roles/run.invoker
    # on the marketplace-handler service (granted in deploy.sh after the
    # handler is deployed).
    log_info "Creating Pub/Sub Invoker service account: $PUBSUB_INVOKER_SA"

    if ! gcloud iam service-accounts describe "$PUBSUB_INVOKER_SA" --project="$PROJECT_ID" &>/dev/null; then
        gcloud iam service-accounts create "$PUBSUB_INVOKER_NAME" \
            --display-name="Pub/Sub Invoker SA" \
            --description="Authorizes Pub/Sub push subscriptions to invoke Cloud Run services" \
            --project="$PROJECT_ID"
        log_info "Pub/Sub Invoker service account created"
    else
        log_info "Pub/Sub Invoker service account already exists"
    fi

    # Grant the Pub/Sub Invoker SA permission to act as itself.
    # Required because we authenticate AS this SA and create a subscription
    # that uses it as the push-auth identity.
    log_info "Granting Service Account User to Pub/Sub Invoker SA on itself..."
    gcloud iam service-accounts add-iam-policy-binding "$PUBSUB_INVOKER_SA" \
        --member="serviceAccount:$PUBSUB_INVOKER_SA" \
        --role="roles/iam.serviceAccountUser" \
        --project="$PROJECT_ID" \
        --quiet || true

    # -------------------------------------------------------------------------
    # Create Pub/Sub Topic
    # -------------------------------------------------------------------------
    PUBSUB_TOPIC="${PUBSUB_TOPIC:-marketplace-entitlements}"

    if ! gcloud pubsub topics describe "$PUBSUB_TOPIC" --project="$PROJECT_ID" &>/dev/null; then
        gcloud pubsub topics create "$PUBSUB_TOPIC" --project="$PROJECT_ID"
        log_info "Pub/Sub topic '$PUBSUB_TOPIC' created"
    else
        log_info "Pub/Sub topic '$PUBSUB_TOPIC' already exists"
    fi

    # Note: The push subscription is created in deploy.sh after the
    # marketplace-handler is deployed, because the push endpoint URL
    # (the handler's Cloud Run URL) is not known until then.
    log_info "Pub/Sub push subscription will be configured by deploy.sh"
else
    log_info "Skipping Pub/Sub setup (ENABLE_MARKETPLACE=false)"
fi

# =============================================================================
# Summary
# =============================================================================
echo ""
log_info "=========================================="
log_info "Setup complete!"
log_info "=========================================="
echo ""
echo "Service accounts created:"
echo "  Runtime SA:         $SERVICE_ACCOUNT"
if [[ "$ENABLE_MARKETPLACE" == "true" ]]; then
    echo "  Pub/Sub Invoker SA: $PUBSUB_INVOKER_SA"
fi
echo ""
echo "Next steps:"
echo ""
echo "1. Set up Cloud SQL database:"
echo "   # Create instance"
echo "   gcloud sql instances create $DB_INSTANCE_NAME --database-version=POSTGRES_16 --edition=ENTERPRISE --tier=db-g1-small --region=$REGION --project=$PROJECT_ID --ssl-mode=ENCRYPTED_ONLY"
echo ""
echo "   # Generate random passwords for database users"
echo "   MARKETPLACE_DB_PASSWORD=\$(python3 -c \"import secrets; print(secrets.token_urlsafe(24))\")"
echo "   SESSION_DB_PASSWORD=\$(python3 -c \"import secrets; print(secrets.token_urlsafe(24))\")"
echo "   echo \"Marketplace DB password: \$MARKETPLACE_DB_PASSWORD\""
echo "   echo \"Session DB password: \$SESSION_DB_PASSWORD\""
echo ""
echo "   # Create databases and users"
echo "   gcloud sql databases create lightspeed_agent --instance=$DB_INSTANCE_NAME --project=$PROJECT_ID"
echo "   gcloud sql users create insights --instance=$DB_INSTANCE_NAME --password=\$MARKETPLACE_DB_PASSWORD --project=$PROJECT_ID"
echo "   gcloud sql databases create agent_sessions --instance=$DB_INSTANCE_NAME --project=$PROJECT_ID"
echo "   gcloud sql users create sessions --instance=$DB_INSTANCE_NAME --password=\$SESSION_DB_PASSWORD --project=$PROJECT_ID"
echo ""
echo "2. Update secrets with actual values:"
echo ""
echo "   # Google API Key (for Vertex AI / Gemini)"
echo "   echo -n 'YOUR_API_KEY' | gcloud secrets versions add google-api-key --data-file=- --project=$PROJECT_ID"
echo ""
echo "   # Red Hat SSO credentials (for user authentication)"
echo "   echo -n 'YOUR_SSO_CLIENT_ID' | gcloud secrets versions add redhat-sso-client-id --data-file=- --project=$PROJECT_ID"
echo "   echo -n 'YOUR_SSO_CLIENT_SECRET' | gcloud secrets versions add redhat-sso-client-secret --data-file=- --project=$PROJECT_ID"
echo ""
echo "   # DCR (Dynamic Client Registration) credentials"
echo "   echo -n 'YOUR_INITIAL_ACCESS_TOKEN' | gcloud secrets versions add dcr-initial-access-token --data-file=- --project=$PROJECT_ID"
echo "   # Generate Fernet key: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
echo "   echo -n 'YOUR_FERNET_KEY' | gcloud secrets versions add dcr-encryption-key --data-file=- --project=$PROJECT_ID"
echo ""
echo "   # Database URLs (after Cloud SQL setup)"
echo "   CONNECTION_NAME=\$(gcloud sql instances describe $DB_INSTANCE_NAME --project=$PROJECT_ID --format='value(connectionName)')"
echo "   echo -n \"postgresql+asyncpg://insights:\$MARKETPLACE_DB_PASSWORD@/lightspeed_agent?host=/cloudsql/\$CONNECTION_NAME\" | gcloud secrets versions add database-url --data-file=- --project=$PROJECT_ID"
echo "   echo -n \"postgresql+asyncpg://sessions:\$SESSION_DB_PASSWORD@/agent_sessions?host=/cloudsql/\$CONNECTION_NAME\" | gcloud secrets versions add session-database-url --data-file=- --project=$PROJECT_ID"
echo ""
echo "3. Copy the MCP server image to GCR (Cloud Run doesn't support Quay.io):"
echo "   docker pull quay.io/redhat-services-prod/insights-management-tenant/insights-mcp/red-hat-lightspeed-mcp:latest"
echo "   docker tag quay.io/redhat-services-prod/insights-management-tenant/insights-mcp/red-hat-lightspeed-mcp:latest gcr.io/$PROJECT_ID/insights-mcp:latest"
echo "   docker push gcr.io/$PROJECT_ID/insights-mcp:latest"
echo ""
echo "4. Build and deploy the agent (includes MCP sidecar):"
echo "   ./deploy/cloudrun/deploy.sh --build --service all --allow-unauthenticated"
echo ""
echo "5. Get the service URL:"
echo "   gcloud run services describe $SERVICE_NAME --region=$REGION --project=$PROJECT_ID --format='value(status.url)'"
echo ""
