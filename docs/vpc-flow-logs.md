# VPC flow logs and Splunk export (SEC-NET-REQ-5)

This guide aligns Google Cloud deployments with internal **SEC-NET-REQ-5** expectations for monitoring ingress/egress traffic: enable **VPC Flow Logs** on relevant subnets, register with enterprise monitoring (Splunk), and route **VPC flow log entries** to a **dedicated Pub/Sub topic** for consumption (Dataflow, Splunk Add-on for GCP, or a small forwarder).

It complements [Cloud Run deployment](../deploy/cloudrun/README.md). It does **not** replace marketplace or application logging.

## Not the same as marketplace Pub/Sub

| Topic / path | Purpose |
|--------------|---------|
| `PUBSUB_TOPIC` in [setup.sh](../deploy/cloudrun/setup.sh) | Google Cloud Marketplace procurement events → marketplace handler |
| VPC flow log export topic (this doc) | `compute.googleapis.com/vpc_flows` → Splunk / monitoring |

Use **separate** topic names and sinks. Do not point the marketplace subscription at the VPC flow log topic.

## Step A — Enable VPC flow logs on subnets

In GCP, flow logs are configured **per subnet**. Enable them on **every subnet that hosts workloads** for this service (and any subnet your security policy requires), including for example:

- Subnets used by **Serverless VPC Access** (connector) and **Cloud Memorystore** if you followed [Redis setup](../deploy/cloudrun/README.md#4-redis-setup-for-rate-limiting)
- Subnets used for **Cloud SQL private IP** or other data-plane resources in the same project

**Recommended settings** (enterprise standard):

- **Aggregation interval:** 10 minutes  
- **Metadata:** Include all metadata  
- **Filter:** Capture all traffic (accept and reject)

If policy requires **Customer-Managed Encryption Keys (CMEK)** for logging buckets, ensure keys exist under **Security → Key Management** before enabling or exporting logs.

Configure via **VPC network → Subnets → Edit** in the console, or with `gcloud compute networks subnets update` (verify flag names against current `gcloud` documentation for your SDK version). Shared VPC and org-owned networks may be updated only by your platform team (Terraform, etc.).

## Step B — ITML / Splunk onboarding (manual)

Register the project so logs can be indexed in Splunk:

- **Support service:** Access to Monitoring Platform  
- **Platform:** Splunk  
- **Request type:** GCP Log Onboarding  

**Provide:**

- GCP **Project ID**  
- **Data type:** VPC Flow Logs  
- **Pub/Sub topic name** you will use in Step C (must match the topic created by [vpc-flow-log-export.sh](../deploy/cloudrun/vpc-flow-log-export.sh) or your own process)

**Outcome:** Splunk **HEC URL** and **HEC token**. Store secrets in **Secret Manager** if you deploy a forwarder (Cloud Function, Cloud Run, etc.). Forwarding to Splunk is **out of scope** for this repository; use [Dataflow Pub/Sub to Splunk](https://cloud.google.com/dataflow/docs/guides/templates/provided/pubsub-to-splunk), the Splunk Add-on for GCP, or your internal standard.

## Step C — Pub/Sub topic and log sink

Create a **dedicated** topic (for example `vpc-flow-logs-export`) and a **log router sink** whose **destination** is that topic.

**Inclusion filter** (replace `PROJECT_ID` with your project ID):

```text
resource.type="gce_subnetwork" AND logName:"projects/PROJECT_ID/logs/compute.googleapis.com%2Fvpc_flows"
```

The Cloud Logging **sink service account** must have **`roles/pubsub.publisher`** on the topic. The helper script [vpc-flow-log-export.sh](../deploy/cloudrun/vpc-flow-log-export.sh) creates the topic, sink, and binding when `ENABLE_VPC_FLOW_LOG_EXPORT=true`.

## Optional automation

From the repository root:

```bash
export GOOGLE_CLOUD_PROJECT="your-project-id"
export ENABLE_VPC_FLOW_LOG_EXPORT=true
# Optional overrides:
# export VPC_FLOW_LOGS_TOPIC="vpc-flow-logs-export"
# export VPC_FLOW_LOG_SINK_NAME="vpc-flow-logs-export-sink"

./deploy/cloudrun/vpc-flow-log-export.sh
```

See [Cloud Run README — VPC flow logs](../deploy/cloudrun/README.md#vpc-flow-logs-sec-net-req-5-and-splunk-export) for environment variables.

## References

- [VPC flow logs](https://cloud.google.com/vpc/docs/using-flow-logs) (Google Cloud)  
- [Export logs to Pub/Sub](https://cloud.google.com/logging/docs/export/pubsub)  
- [Pub/Sub to Splunk (Dataflow template)](https://cloud.google.com/dataflow/docs/guides/templates/provided/pubsub-to-splunk)  
- [Send GCP data to Splunk](https://docs.splunk.com/Documentation/AddOns/released/GoogleCloud/) (Splunk documentation)
