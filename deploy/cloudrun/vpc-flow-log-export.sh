#!/bin/bash
# =============================================================================
# VPC flow log export — Pub/Sub topic + Logging sink (SEC-NET-REQ-5 / Splunk prep)
# =============================================================================
#
# Creates a dedicated Pub/Sub topic and a log router sink that exports only
# VPC flow logs (compute.googleapis.com/vpc_flows) for Splunk / Dataflow / add-on
# consumption. Separate from marketplace Pub/Sub (PUBSUB_TOPIC).
#
# Usage:
#   export GOOGLE_CLOUD_PROJECT=your-project
#   export ENABLE_VPC_FLOW_LOG_EXPORT=true
#   ./deploy/cloudrun/vpc-flow-log-export.sh
#
# Optional environment variables:
#   VPC_FLOW_LOGS_TOPIC     Pub/Sub topic id (default: vpc-flow-logs-export)
#   VPC_FLOW_LOG_SINK_NAME  Log sink name (default: ${VPC_FLOW_LOGS_TOPIC}-sink)
#
# Prerequisites:
#   - gcloud CLI authenticated
#   - GOOGLE_CLOUD_PROJECT set
#   - ENABLE_VPC_FLOW_LOG_EXPORT=true (opt-in safety gate)
#
# See docs/vpc-flow-logs.md for subnet flow logging and ITML / Splunk steps.
#
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-}"
VPC_FLOW_LOGS_TOPIC="${VPC_FLOW_LOGS_TOPIC:-vpc-flow-logs-export}"

if [[ -z "${VPC_FLOW_LOG_SINK_NAME:-}" ]]; then
    VPC_FLOW_LOG_SINK_NAME="${VPC_FLOW_LOGS_TOPIC}-sink"
fi

if [[ "${ENABLE_VPC_FLOW_LOG_EXPORT:-}" != "true" ]]; then
    log_error "Refusing to run: set ENABLE_VPC_FLOW_LOG_EXPORT=true to create VPC flow log export resources."
    exit 1
fi

if [[ -z "$PROJECT_ID" ]]; then
    log_error "GOOGLE_CLOUD_PROJECT environment variable is required"
    exit 1
fi

TOPIC_FULL="projects/${PROJECT_ID}/topics/${VPC_FLOW_LOGS_TOPIC}"
DESTINATION="pubsub.googleapis.com/${TOPIC_FULL}"

# Inclusion filter per internal VPC / Splunk onboarding doc (SEC-NET-REQ-5).
LOG_FILTER='resource.type="gce_subnetwork" AND logName:"projects/'"${PROJECT_ID}"'/logs/compute.googleapis.com%2Fvpc_flows"'

log_info "VPC flow log export setup for project: $PROJECT_ID"
log_info "  Topic: $VPC_FLOW_LOGS_TOPIC"
log_info "  Sink:  $VPC_FLOW_LOG_SINK_NAME"

# -----------------------------------------------------------------------------
# Enable Logging API (idempotent)
# -----------------------------------------------------------------------------
log_info "Ensuring logging.googleapis.com is enabled..."
gcloud services enable logging.googleapis.com --project="$PROJECT_ID" --quiet
log_info "  logging.googleapis.com enabled"

# -----------------------------------------------------------------------------
# Pub/Sub topic (same project only)
# -----------------------------------------------------------------------------
log_info "Ensuring Pub/Sub topic exists..."
if gcloud pubsub topics describe "$VPC_FLOW_LOGS_TOPIC" --project="$PROJECT_ID" &>/dev/null; then
    log_info "  Topic '$VPC_FLOW_LOGS_TOPIC' already exists"
else
    gcloud pubsub topics create "$VPC_FLOW_LOGS_TOPIC" --project="$PROJECT_ID"
    log_info "  Topic '$VPC_FLOW_LOGS_TOPIC' created"
fi

# -----------------------------------------------------------------------------
# Log sink (create or update filter / destination)
# -----------------------------------------------------------------------------
if gcloud logging sinks describe "$VPC_FLOW_LOG_SINK_NAME" --project="$PROJECT_ID" &>/dev/null; then
    log_info "Updating existing log sink '$VPC_FLOW_LOG_SINK_NAME'..."
    gcloud logging sinks update "$VPC_FLOW_LOG_SINK_NAME" \
        "$DESTINATION" \
        --log-filter="$LOG_FILTER" \
        --project="$PROJECT_ID"
else
    log_info "Creating log sink '$VPC_FLOW_LOG_SINK_NAME'..."
    gcloud logging sinks create "$VPC_FLOW_LOG_SINK_NAME" \
        "$DESTINATION" \
        --log-filter="$LOG_FILTER" \
        --project="$PROJECT_ID"
fi

# -----------------------------------------------------------------------------
# Grant sink writer identity permission to publish to the topic
# -----------------------------------------------------------------------------
WRITER_IDENTITY=$(gcloud logging sinks describe "$VPC_FLOW_LOG_SINK_NAME" \
    --project="$PROJECT_ID" \
    --format='value(writerIdentity)')

if [[ -z "$WRITER_IDENTITY" ]]; then
    log_error "Could not read writerIdentity for sink '$VPC_FLOW_LOG_SINK_NAME'"
    exit 1
fi

log_info "Granting roles/pubsub.publisher on topic to: $WRITER_IDENTITY"
# writerIdentity is typically "serviceAccount:service-...@gcp-sa-logging.iam.gserviceaccount.com"
gcloud pubsub topics add-iam-policy-binding "$VPC_FLOW_LOGS_TOPIC" \
    --member="$WRITER_IDENTITY" \
    --role="roles/pubsub.publisher" \
    --project="$PROJECT_ID" \
    --quiet

log_info "=========================================="
log_info "VPC flow log export configuration complete"
log_info "=========================================="
echo ""
echo "Next steps (manual):"
echo "  1. Enable VPC flow logs on each relevant subnet (see docs/vpc-flow-logs.md)."
echo "  2. File ITML / Splunk GCP Log Onboarding with topic name: $VPC_FLOW_LOGS_TOPIC"
echo "  3. Connect Splunk (Dataflow template, add-on, or forwarder) using HEC from ITML."
echo ""
