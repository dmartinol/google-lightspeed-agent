#!/usr/bin/env python3
"""Get an OAuth token from DCR credentials and test the A2A agent.

Uses client_credentials grant — just like Gemini Enterprise does with the
client_id/client_secret returned by DCR.

------------------------------------------------------------------------
Usage
------------------------------------------------------------------------

    # Get a token and send a test message:
    python scripts/test_a2a_auth.py \
        --client-id  <CLIENT_ID> \
        --client-secret <CLIENT_SECRET> \
        --issuer http://localhost:8180/realms/test-realm \
        --agent-url http://localhost:8000 \
        --message "What systems have critical advisories?"

    # Just print the token (for pasting into A2A Inspector):
    python scripts/test_a2a_auth.py \
        --client-id <CLIENT_ID> \
        --client-secret <CLIENT_SECRET>

    # Or use environment variables:
    export OAUTH_CLIENT_ID=<CLIENT_ID>
    export OAUTH_CLIENT_SECRET=<CLIENT_SECRET>
    export RED_HAT_SSO_ISSUER=http://localhost:8180/realms/test-realm
    python scripts/test_a2a_auth.py

    # Dev mode (agent has SKIP_JWT_VALIDATION=true, no real token needed):
    python scripts/test_a2a_auth.py --dev-mode \
        --agent-url http://localhost:8000 \
        --message "What systems have critical advisories?"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def get_token(token_url: str, client_id: str, client_secret: str) -> str:
    """Get an access token via client_credentials grant."""
    data = urlencode({
        "grant_type": "client_credentials",
        "scope": "openid api.console api.ocm",
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode("ascii")
    request = Request(
        token_url,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
        data=data,
    )
    try:
        result = urlopen(request, timeout=30).read()
        return json.loads(result)["access_token"]
    except HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        print(f"Token request failed ({e.code}): {error_body}", file=sys.stderr)
        sys.exit(1)


def send_a2a_message(agent_url: str, token: str, message: str) -> None:
    """Send a JSON-RPC message/send to the agent."""
    url = agent_url.rstrip("/") + "/"
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": message}],
                "messageId": str(uuid.uuid4()),
            },
        },
    }).encode("utf-8")

    request = Request(
        url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
        data=payload,
    )

    print(f"\n>>> POST {url}")
    result = urlopen(request, timeout=120).read()
    response = json.loads(result)
    print(json.dumps(response, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Get OAuth token and test A2A agent")
    parser.add_argument("--client-id", default=os.environ.get("OAUTH_CLIENT_ID", ""))
    parser.add_argument("--client-secret", default=os.environ.get("OAUTH_CLIENT_SECRET", ""))
    parser.add_argument(
        "--issuer",
        default=os.environ.get("RED_HAT_SSO_ISSUER", "https://sso.redhat.com/auth/realms/redhat-external"),
    )
    parser.add_argument("--agent-url", default=os.environ.get("AGENT_URL", "http://localhost:8000"))
    parser.add_argument("--message", default="", help="Test message to send to the agent")
    parser.add_argument("--dev-mode", action="store_true", help="Use a dummy token (agent must have SKIP_JWT_VALIDATION=true)")
    args = parser.parse_args()

    if args.dev_mode:
        token = "dev-token"
        print("Using dummy dev token (agent must have SKIP_JWT_VALIDATION=true)")
    else:
        if not args.client_id or not args.client_secret:
            print("ERROR: --client-id and --client-secret required (or use --dev-mode)", file=sys.stderr)
            sys.exit(1)

        token_url = f"{args.issuer}/protocol/openid-connect/token"
        print(f"Getting token from {token_url} ...")
        token = get_token(token_url, args.client_id, args.client_secret)

    print(f"\nAccess token:\n{token}\n")
    print("Paste this into the A2A Inspector 'Bearer Token' field.")

    if args.message:
        send_a2a_message(args.agent_url, token, args.message)


if __name__ == "__main__":
    main()
