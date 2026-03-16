#!/usr/bin/env python3
"""Test client for DCR on a deployed Cloud Run marketplace handler.

Sends a signed software_statement JWT to the deployed marketplace handler's
/dcr endpoint, authenticating with a Google Cloud ID token for Cloud Run
access.

Unlike the local test_dcr.py script, this script:
  1. Targets a deployed Cloud Run service (not localhost)
  2. Obtains a Google Cloud ID token for Cloud Run authentication
  3. Supports authenticated and unauthenticated Cloud Run services

------------------------------------------------------------------------
Prerequisites
------------------------------------------------------------------------

1. Google Cloud CLI installed and authenticated:
       gcloud auth login
       gcloud auth application-default login

2. A deployed marketplace handler on Cloud Run
   (see deploy/cloudrun/README.md for deployment instructions)

3. A GCP service account for signing test JWTs (see below)

4. Python dependencies:
       pip install PyJWT cryptography requests google-auth

------------------------------------------------------------------------
JWT Signing Methods
------------------------------------------------------------------------

Method A: Local key file (recommended -- no IAM permissions needed)
    Download a service account key and sign locally with PyJWT.

    1. Create a service account:
           gcloud iam service-accounts create dcr-test \
               --display-name "DCR test signer" \
               --project=<PROJECT>

       NOTE: GCP may need a few seconds to propagate the new service account.
       If the next command fails with NOT_FOUND, wait ~10 seconds and retry.

    2. Download a key file:
           gcloud iam service-accounts keys create dcr-test-key.json \
               --iam-account=dcr-test@<PROJECT>.iam.gserviceaccount.com \
               --project=<PROJECT>

Method B: IAM Credentials API (remote signing)
    Uses the IAM Credentials API to sign without downloading a key.
    Requires roles/iam.serviceAccountTokenCreator on the service account.

------------------------------------------------------------------------
Usage
------------------------------------------------------------------------

    # Method A: Sign with a local key file
    export MARKETPLACE_HANDLER_URL=https://marketplace-handler-XXXX.run.app
    export TEST_SA_KEY_FILE=dcr-test-key.json
    python scripts/test_deployed_dcr.py

    # Method B: Sign via IAM Credentials API
    export MARKETPLACE_HANDLER_URL=https://marketplace-handler-XXXX.run.app
    export TEST_SERVICE_ACCOUNT=dcr-test@<PROJECT>.iam.gserviceaccount.com
    python scripts/test_deployed_dcr.py

    # Skip Cloud Run auth (if deployed with --allow-unauthenticated)
    export SKIP_CLOUD_RUN_AUTH=true
    python scripts/test_deployed_dcr.py

    # Override the audience (aud) claim
    export PROVIDER_URL=https://my-agent.example.com
    python scripts/test_deployed_dcr.py

------------------------------------------------------------------------
Environment variables
------------------------------------------------------------------------

    MARKETPLACE_HANDLER_URL  (required)
        Full URL of the deployed marketplace handler Cloud Run service.
        Example: https://marketplace-handler-528009937268.us-central1.run.app

    TEST_SA_KEY_FILE  (Method A -- recommended)
        Path to a service account key JSON file.  When set, the script
        signs JWTs locally with PyJWT.

    TEST_SERVICE_ACCOUNT  (Method B)
        Email of the GCP service account used to sign the JWT via the
        IAM Credentials API.  Ignored if TEST_SA_KEY_FILE is set.

    PROVIDER_URL  (default: https://www.redhat.com)
        Expected audience (aud) claim.  Must match the handler's
        AGENT_PROVIDER_ORGANIZATION_URL setting.

    SKIP_CLOUD_RUN_AUTH  (default: false)
        Set to "true" to skip Cloud Run ID token authentication.
        Use when the service is deployed with --allow-unauthenticated.

    TEST_ORDER_ID  (optional)
        Fixed marketplace order ID.  If unset a random UUID is generated.

    TEST_ACCOUNT_ID  (optional, default: test-procurement-account-001)
        Procurement account ID used as the JWT 'sub' claim.

    TEST_REDIRECT_URIS  (optional, default: https://gemini.google.com/callback)
        Comma-separated list of redirect URIs.

    TEST_CLIENT_ID  (optional -- static credentials mode)
        Pre-registered OAuth client ID.  When set together with
        TEST_CLIENT_SECRET, the script sends client_id and client_secret
        in the DCR request body alongside the software_statement.
        Used when DCR_ENABLED=false on the handler.

    TEST_CLIENT_SECRET  (optional -- static credentials mode)
        Pre-registered OAuth client secret.  Must be set together with
        TEST_CLIENT_ID.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid

import requests

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

HANDLER_URL = os.environ.get("MARKETPLACE_HANDLER_URL", "")
PROVIDER_URL = os.environ.get("PROVIDER_URL", "https://www.redhat.com")
TEST_SERVICE_ACCOUNT = os.environ.get("TEST_SERVICE_ACCOUNT")
TEST_SA_KEY_FILE = os.environ.get("TEST_SA_KEY_FILE")
SKIP_CLOUD_RUN_AUTH = os.environ.get("SKIP_CLOUD_RUN_AUTH", "false").lower() == "true"
TEST_CLIENT_ID = os.environ.get("TEST_CLIENT_ID")
TEST_CLIENT_SECRET = os.environ.get("TEST_CLIENT_SECRET")

CERT_BASE_URL = (
    "https://www.googleapis.com/service_accounts/v1/metadata/x509/"
)


# ---------------------------------------------------------------------------
# Signing method resolution
# ---------------------------------------------------------------------------


def _resolve_signing_method() -> tuple[str, str]:
    """Determine which signing method to use and return (method, sa_email).

    Returns:
        Tuple of ("key_file" | "iam_api", service_account_email).
    """
    if TEST_SA_KEY_FILE:
        key_data = _load_key_file(TEST_SA_KEY_FILE)
        return "key_file", key_data["client_email"]

    if TEST_SERVICE_ACCOUNT:
        return "iam_api", TEST_SERVICE_ACCOUNT

    print(
        "ERROR: No signing method configured.\n"
        "\n"
        "Set one of the following environment variables:\n"
        "\n"
        "  Method A (local key file -- recommended, no IAM permissions needed):\n"
        "    export TEST_SA_KEY_FILE=dcr-test-key.json\n"
        "\n"
        "  Method B (IAM Credentials API -- needs serviceAccountTokenCreator):\n"
        "    export TEST_SERVICE_ACCOUNT=dcr-test@my-project.iam.gserviceaccount.com",
        file=sys.stderr,
    )
    sys.exit(1)


def _load_key_file(path: str) -> dict:
    """Load and validate a service account key JSON file."""
    try:
        with open(path) as f:
            key_data = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: Key file not found: {path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in key file {path}: {e}", file=sys.stderr)
        sys.exit(1)

    required = ("client_email", "private_key", "private_key_id")
    missing = [k for k in required if k not in key_data]
    if missing:
        print(
            f"ERROR: Key file {path} missing required fields: {', '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(1)

    return key_data


# ---------------------------------------------------------------------------
# JWT creation
# ---------------------------------------------------------------------------


def sign_jwt_with_key_file(payload: dict, key_file_path: str) -> str:
    """Sign a JWT locally using a service account key file.

    Args:
        payload: JWT claims dict.
        key_file_path: Path to the service account key JSON file.

    Returns:
        The signed JWT string.
    """
    import jwt  # PyJWT

    key_data = _load_key_file(key_file_path)
    private_key = key_data["private_key"]
    key_id = key_data["private_key_id"]

    print(f"  Signing JWT locally with key file: {key_file_path}")
    print(f"  Service account: {key_data['client_email']}")

    return jwt.encode(
        payload,
        private_key,
        algorithm="RS256",
        headers={"kid": key_id},
    )


def sign_jwt_with_iam_api(payload: dict, service_account_email: str) -> str:
    """Sign a JWT using the GCP IAM Credentials API.

    Args:
        payload: JWT claims dict.
        service_account_email: The service account to sign with.

    Returns:
        The signed JWT string.
    """
    from google.cloud import iam_credentials_v1

    client = iam_credentials_v1.IAMCredentialsClient()
    name = f"projects/-/serviceAccounts/{service_account_email}"

    print(f"  Signing JWT via IAM Credentials API: {service_account_email}")
    response = client.sign_jwt(name=name, payload=json.dumps(payload))
    return response.signed_jwt


def build_software_statement(
    service_account_email: str,
    signing_method: str,
    order_id: str,
    account_id: str,
    redirect_uris: list[str],
) -> str:
    """Build and sign a software_statement JWT.

    Args:
        service_account_email: GCP SA email for signing.
        signing_method: "key_file" or "iam_api".
        order_id: Marketplace order ID.
        account_id: Procurement account ID.
        redirect_uris: OAuth redirect URIs.

    Returns:
        Signed JWT string.
    """
    now = int(time.time())

    payload = {
        "iss": CERT_BASE_URL + service_account_email,
        "iat": now,
        "exp": now + 3600,
        "aud": PROVIDER_URL,
        "sub": account_id,
        "auth_app_redirect_uris": redirect_uris,
        "google": {
            "order": order_id,
        },
    }

    print(f"  JWT claims:\n{json.dumps(payload, indent=4)}")

    if signing_method == "key_file":
        return sign_jwt_with_key_file(payload, TEST_SA_KEY_FILE)
    else:
        return sign_jwt_with_iam_api(payload, service_account_email)


# ---------------------------------------------------------------------------
# Cloud Run authentication
# ---------------------------------------------------------------------------


def get_cloud_run_id_token(audience: str) -> str | None:
    """Obtain a Google Cloud ID token for invoking a Cloud Run service.

    Tries two methods:
      1. google-auth library (uses Application Default Credentials)
      2. gcloud CLI fallback (uses current gcloud login)

    Args:
        audience: The Cloud Run service URL (used as the token audience).

    Returns:
        ID token string, or None if both methods fail.
    """
    # Method 1: google-auth library
    try:
        import google.auth.transport.requests
        from google.oauth2 import id_token

        auth_req = google.auth.transport.requests.Request()
        token = id_token.fetch_id_token(auth_req, audience)
        print("  Obtained ID token via google-auth library")
        return token
    except Exception as e:
        print(f"  google-auth failed ({e}), trying gcloud fallback...")

    # Method 2: gcloud CLI
    try:
        token = subprocess.check_output(
            ["gcloud", "auth", "print-identity-token"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        print("  Obtained ID token via gcloud CLI")
        return token
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"  gcloud fallback failed: {e}", file=sys.stderr)

    return None


# ---------------------------------------------------------------------------
# DCR request
# ---------------------------------------------------------------------------


def send_dcr_request(
    software_statement: str,
    id_token: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
) -> None:
    """POST the software_statement to the /dcr endpoint.

    Args:
        software_statement: Signed JWT string.
        id_token: Optional Cloud Run ID token for authentication.
        client_id: Optional static OAuth client ID.
        client_secret: Optional static OAuth client secret.
    """
    url = f"{HANDLER_URL.rstrip('/')}/dcr"
    body: dict[str, str] = {"software_statement": software_statement}
    if client_id:
        body["client_id"] = client_id
    if client_secret:
        body["client_secret"] = client_secret

    headers = {"Content-Type": "application/json"}
    if id_token:
        headers["Authorization"] = f"Bearer {id_token}"

    print(f"\n>>> POST {url}")
    if client_id:
        print(f"    (static credentials: client_id={client_id})")
    if id_token:
        print(f"    Authorization: Bearer {id_token[:40]}...")
    response = requests.post(
        url,
        json=body,
        headers=headers,
        timeout=30,
    )

    print(f"<<< {response.status_code}")
    try:
        print(json.dumps(response.json(), indent=2))
    except Exception:
        print(response.text)

    if response.status_code == 201:
        print("\nDCR succeeded.")
    else:
        print("\nDCR failed.", file=sys.stderr)
        if response.status_code in (401, 403):
            print(
                "\n[NOTE] Authentication/Authorization failed.\n"
                "Ensure your identity has 'roles/run.invoker' on the Cloud Run service,\n"
                "or deploy with --allow-unauthenticated and set SKIP_CLOUD_RUN_AUTH=true.",
                file=sys.stderr,
            )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    if not HANDLER_URL:
        print(
            "ERROR: MARKETPLACE_HANDLER_URL is required.\n"
            "\n"
            "Set it to the URL of your deployed marketplace handler:\n"
            "  export MARKETPLACE_HANDLER_URL=https://marketplace-handler-XXXX.run.app\n"
            "\n"
            "Get the URL with:\n"
            "  gcloud run services describe marketplace-handler \\\n"
            "    --region=$GOOGLE_CLOUD_LOCATION \\\n"
            "    --project=$GOOGLE_CLOUD_PROJECT \\\n"
            "    --format='value(status.url)'",
            file=sys.stderr,
        )
        sys.exit(1)

    signing_method, sa_email = _resolve_signing_method()

    order_id = os.environ.get("TEST_ORDER_ID") or f"order-{uuid.uuid4()}"
    account_id = os.environ.get("TEST_ACCOUNT_ID", "test-procurement-account-001")
    redirect_uris = os.environ.get(
        "TEST_REDIRECT_URIS", "https://gemini.google.com/callback"
    ).split(",")

    # Static credentials (optional)
    client_id = TEST_CLIENT_ID
    client_secret = TEST_CLIENT_SECRET

    print("=" * 60)
    print("DCR Test Client (Deployed Cloud Run)")
    print("=" * 60)
    print(f"  Handler URL    : {HANDLER_URL}")
    print(f"  Provider URL   : {PROVIDER_URL}")
    print(f"  Signing method : {signing_method}")
    print(f"  Service account: {sa_email}")
    print(f"  Order ID       : {order_id}")
    print(f"  Account ID     : {account_id}")
    print(f"  Redirect URIs  : {redirect_uris}")
    print(f"  Cloud Run auth : {'skip' if SKIP_CLOUD_RUN_AUTH else 'enabled'}")
    if client_id:
        print(f"  Static client  : {client_id}")
        print(f"  Static secret  : {'*' * min(len(client_secret), 8) if client_secret else '(not set)'}")
    else:
        print("  Mode           : dynamic (no static credentials)")
    print()

    # --- Step 1: Build software_statement JWT ---
    print("--- Building software_statement JWT ---")
    software_statement = build_software_statement(
        sa_email, signing_method, order_id, account_id, redirect_uris
    )
    print(f"\n  JWT (first 80 chars): {software_statement[:80]}...")

    # --- Step 2: Get Cloud Run ID token ---
    id_token = None
    if not SKIP_CLOUD_RUN_AUTH:
        print("\n--- Obtaining Cloud Run ID token ---")
        id_token = get_cloud_run_id_token(HANDLER_URL)
        if not id_token:
            print(
                "\nERROR: Could not obtain Cloud Run ID token.\n"
                "Options:\n"
                "  1. Run 'gcloud auth login' or set Application Default Credentials\n"
                "  2. Set SKIP_CLOUD_RUN_AUTH=true if the service allows unauthenticated access",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        print("\n--- Skipping Cloud Run authentication ---")

    # --- Step 3: Send DCR request ---
    print("\n--- Sending DCR request ---")
    send_dcr_request(software_statement, id_token, client_id, client_secret)

    # --- Step 4: Idempotency test ---
    print("\n--- Sending duplicate DCR request (idempotency test) ---")
    send_dcr_request(software_statement, id_token, client_id, client_secret)


if __name__ == "__main__":
    main()
