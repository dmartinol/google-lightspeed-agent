#!/usr/bin/env python3
"""Test client for the DCR (Dynamic Client Registration) endpoint.

Sends a signed software_statement JWT to the marketplace handler's /dcr
endpoint, simulating what Google Cloud Marketplace does when registering
a new OAuth client for a marketplace order.

The JWT is NOT signed by Google's production cloud-agentspace service
account, so the marketplace handler must be running with
SKIP_JWT_VALIDATION=true to accept it.

------------------------------------------------------------------------
Signing methods
------------------------------------------------------------------------

The script supports two ways to sign the JWT:

Method A: Local key file (recommended -- no IAM permissions needed)
    Download a service account key and sign locally with PyJWT.
    Only requires permission to create keys (iam.serviceAccountKeys.create).

Method B: IAM Credentials API (remote signing)
    Uses the IAM Credentials API to sign without downloading a key.
    Requires roles/iam.serviceAccountTokenCreator on the service account.

------------------------------------------------------------------------
Prerequisites
------------------------------------------------------------------------

Method A (local key file):

1. A Google Cloud project and service account:
       gcloud services enable iam.googleapis.com --project=<PROJECT>

       gcloud iam service-accounts create dcr-test \
           --display-name "DCR test signer" \
           --project=<PROJECT>

   NOTE: GCP may need a few seconds to propagate the new service account.
   If the next command fails with NOT_FOUND, wait ~10 seconds and retry.

2. Download a key file:
       gcloud iam service-accounts keys create dcr-test-key.json \
           --iam-account=dcr-test@<PROJECT>.iam.gserviceaccount.com \
           --project=<PROJECT>

3. Python dependencies:
       pip install PyJWT cryptography requests

Method B (IAM Credentials API):

1. A Google Cloud project with the IAM Credentials API enabled:
       gcloud services enable iamcredentials.googleapis.com --project=<PROJECT>

2. A GCP service account to sign JWTs:
       gcloud iam service-accounts create dcr-test \
           --display-name "DCR test signer" \
           --project=<PROJECT>

   NOTE: GCP may need a few seconds to propagate the new service account.
   If the next command fails with NOT_FOUND, wait ~10 seconds and retry.

   Grant yourself permission to sign JWTs on its behalf:
       gcloud iam service-accounts add-iam-policy-binding \
           dcr-test@<PROJECT>.iam.gserviceaccount.com \
           --member="user:<YOUR_EMAIL>" \
           --role="roles/iam.serviceAccountTokenCreator" \
           --project=<PROJECT>

3. Python dependencies:
       pip install google-cloud-iam requests

4. Authenticate with Google Cloud:
       gcloud auth application-default login

------------------------------------------------------------------------
Marketplace handler configuration
------------------------------------------------------------------------

The handler must be started with at least these environment variables:

    # Skip Google JWT signature and issuer verification (required)
    SKIP_JWT_VALIDATION=true

    # --- Static credentials mode ---
    # Client provides client_id and client_secret in the DCR request body.
    # Set TEST_CLIENT_ID and TEST_CLIENT_SECRET on this script.
    DCR_ENABLED=false

    # --- OR: Real DCR against a local Keycloak ---
    # DCR_ENABLED=true
    # RED_HAT_SSO_ISSUER=http://localhost:8180/realms/test-realm
    # DCR_INITIAL_ACCESS_TOKEN=<your-keycloak-IAT>

    # Always required
    DCR_ENCRYPTION_KEY=<generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'>
    DATABASE_URL=sqlite+aiosqlite:///./lightspeed_agent.db

    # Must match PROVIDER_URL below (or set AGENT_PROVIDER_ORGANIZATION_URL on the handler)
    AGENT_PROVIDER_ORGANIZATION_URL=https://www.redhat.com

------------------------------------------------------------------------
Local Keycloak setup (for real DCR testing)
------------------------------------------------------------------------

If you want to test the full DCR flow (real OAuth client creation in
Keycloak) without admin access to Red Hat SSO, you can run a local
Keycloak instance in Podman.  This is optional -- static credentials
mode (DCR_ENABLED=false) works without Keycloak.

1. Start Keycloak:

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

   IMPORTANT: KC_HOSTNAME_STRICT=true with KC_HOSTNAME=host.containers.internal
   ensures Keycloak uses a consistent issuer for all tokens regardless of how
   requests arrive (localhost vs host.containers.internal).  Without this, the
   IAT generated via localhost would have a mismatched issuer when the handler
   presents it via host.containers.internal, causing "Failed decode token".

2. Disable SSL and create the test realm:

   Since KC_HOSTNAME_STRICT=true treats localhost as external, you must
   disable SSL via kcadm.sh from inside the container:

       podman exec keycloak-test /opt/keycloak/bin/kcadm.sh \
         config credentials --server http://localhost:8080 \
         --realm master --user admin --password admin

       podman exec keycloak-test /opt/keycloak/bin/kcadm.sh \
         update realms/master -s sslRequired=NONE

       podman exec keycloak-test /opt/keycloak/bin/kcadm.sh \
         create realms -s realm=test-realm -s enabled=true -s sslRequired=NONE

3. Get an admin token:

       ADMIN_TOKEN=$(curl -s -X POST \
         "http://localhost:8180/realms/master/protocol/openid-connect/token" \
         -d "client_id=admin-cli" \
         -d "username=admin" \
         -d "password=admin" \
         -d "grant_type=password" \
         | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

4. Generate an Initial Access Token (IAT) for DCR:

       IAT=$(curl -s -X POST \
         "http://localhost:8180/admin/realms/test-realm/clients-initial-access" \
         -H "Authorization: Bearer $ADMIN_TOKEN" \
         -H "Content-Type: application/json" \
         -d '{"count": 100, "expiration": 86400}' \
         | python -c "import sys,json; print(json.load(sys.stdin)['token'])")
       echo "Initial Access Token: $IAT"

5. Configure the marketplace handler with:

       DCR_ENABLED=true
       SKIP_JWT_VALIDATION=true
       RED_HAT_SSO_ISSUER=http://host.containers.internal:8180/realms/test-realm
       DCR_INITIAL_ACCESS_TOKEN=<the IAT from step 4>

6. Run this script.  The handler will create a real OAuth client in
   your local Keycloak.  Verify at:
       http://localhost:8180/admin -> test-realm -> Clients

7. You can also test Keycloak DCR directly (without the handler):

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

8. Clean up:

       podman stop keycloak-test && podman rm keycloak-test

------------------------------------------------------------------------
Usage
------------------------------------------------------------------------

    # Method A: Sign with a local key file (no IAM permissions needed)
    export TEST_SA_KEY_FILE=dcr-test-key.json
    python scripts/test_dcr.py

    # Method B: Sign via IAM Credentials API (needs serviceAccountTokenCreator)
    export TEST_SERVICE_ACCOUNT=dcr-test@my-project.iam.gserviceaccount.com
    python scripts/test_dcr.py

    # Override the handler URL and audience
    export MARKETPLACE_HANDLER_URL=http://localhost:8001
    export PROVIDER_URL=https://my-agent.example.com
    python scripts/test_dcr.py

    # Provide a specific order ID instead of a random one
    export TEST_ORDER_ID=order-abc-123
    python scripts/test_dcr.py

------------------------------------------------------------------------
Environment variables
------------------------------------------------------------------------

    TEST_SA_KEY_FILE  (Method A -- recommended)
        Path to a service account key JSON file.  When set, the script
        signs JWTs locally with PyJWT.  The service account email is
        read from the key file.

    TEST_SERVICE_ACCOUNT  (Method B)
        Email of the GCP service account used to sign the JWT via the
        IAM Credentials API.  Ignored if TEST_SA_KEY_FILE is set.

    MARKETPLACE_HANDLER_URL  (default: http://localhost:8001)
        Base URL of the marketplace handler.

    PROVIDER_URL  (default: https://www.redhat.com)
        Expected audience (aud) claim.  Must match the handler's
        AGENT_PROVIDER_ORGANIZATION_URL setting.

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
import sys
import time
import uuid

import requests

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

HANDLER_URL = os.environ.get("MARKETPLACE_HANDLER_URL", "http://localhost:8001")
PROVIDER_URL = os.environ.get("PROVIDER_URL", "https://www.redhat.com")
TEST_SERVICE_ACCOUNT = os.environ.get("TEST_SERVICE_ACCOUNT")
TEST_SA_KEY_FILE = os.environ.get("TEST_SA_KEY_FILE")
TEST_CLIENT_ID = os.environ.get("TEST_CLIENT_ID")
TEST_CLIENT_SECRET = os.environ.get("TEST_CLIENT_SECRET")

CERT_BASE_URL = (
    "https://www.googleapis.com/service_accounts/v1/metadata/x509/"
)


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

    The claims mirror what Google Cloud Marketplace sends in production:
      - iss: certificate URL for the signing service account
      - aud: agent's provider URL
      - sub: procurement account ID
      - google.order: marketplace order ID
      - auth_app_redirect_uris: OAuth redirect URIs

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
# DCR request
# ---------------------------------------------------------------------------


def send_dcr_request(
    software_statement: str,
    client_id: str | None = None,
    client_secret: str | None = None,
) -> None:
    """POST the software_statement to the /dcr endpoint.

    Args:
        software_statement: Signed JWT string.
        client_id: Optional static OAuth client ID.
        client_secret: Optional static OAuth client secret.
    """
    url = f"{HANDLER_URL.rstrip('/')}/dcr"
    body: dict[str, str] = {"software_statement": software_statement}
    if client_id:
        body["client_id"] = client_id
    if client_secret:
        body["client_secret"] = client_secret

    print(f"\n>>> POST {url}")
    if client_id:
        print(f"    (static credentials: client_id={client_id})")
    response = requests.post(
        url,
        json=body,
        headers={"Content-Type": "application/json"},
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
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
    print("DCR Test Client")
    print("=" * 60)
    print(f"  Handler URL    : {HANDLER_URL}")
    print(f"  Provider URL   : {PROVIDER_URL}")
    print(f"  Signing method : {signing_method}")
    print(f"  Service account: {sa_email}")
    print(f"  Order ID       : {order_id}")
    print(f"  Account ID     : {account_id}")
    print(f"  Redirect URIs  : {redirect_uris}")
    if client_id:
        print(f"  Static client  : {client_id}")
        print(f"  Static secret  : {'*' * min(len(client_secret), 8) if client_secret else '(not set)'}")
    else:
        print("  Mode           : dynamic (no static credentials)")
    print()

    print("--- Building software_statement JWT ---")
    software_statement = build_software_statement(
        sa_email, signing_method, order_id, account_id, redirect_uris
    )
    print(f"\n  JWT (first 80 chars): {software_statement[:80]}...")

    print("\n--- Sending DCR request ---")
    send_dcr_request(software_statement, client_id, client_secret)

    # Send the same request again to test idempotency (should return
    # the same client_id/client_secret per Google's DCR spec)
    print("\n--- Sending duplicate DCR request (idempotency test) ---")
    send_dcr_request(software_statement, client_id, client_secret)


if __name__ == "__main__":
    main()
