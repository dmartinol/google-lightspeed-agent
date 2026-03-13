# End-to-End Authentication Flow

This document describes the complete authentication lifecycle for the
Red Hat Lightspeed Agent on Google Cloud Marketplace, from subscription
through to authenticated API calls.

---

## Actors and Components

| Actor / Component | Role |
|---|---|
| **Customer Admin** | Subscribes to the agent and configures it in Gemini Enterprise |
| **Customer User** | End user who interacts with the agent through Gemini Enterprise |
| **Google Cloud Marketplace** | Subscription and entitlement management |
| **Gemini Enterprise** | Google's AI platform that acts as the **OAuth 2.0 Client** |
| **Red Hat SSO (Keycloak)** | The **OAuth 2.0 Authorization Server** that issues and validates tokens |
| **Lightspeed Agent** | The **OAuth 2.0 Resource Server** that serves A2A requests |
| **Agent (Marketplace Handler)** | Manages marketplace subscriptions, entitlements, and credential registration (DCR / static) |
| **Red Hat Lightspeed MCP Server** | Downstream tool server that provides access to Red Hat Lightspeed APIs |
---

## Step 1 — Subscription (Entitlement Creation)

The customer admin subscribes to the Red Hat Lightspeed Agent through Google
Cloud Marketplace. This creates the **order (entitlement)** that ties the
customer's organization to the agent.

```
Customer Admin                Google Cloud Marketplace                 Agent (Marketplace Handler)
     |                                  |                                          |
     |--- Subscribe to Agent ---------->|                                          |
     |                                  |                                          |
     |                                  |-- Pub/Sub: ACCOUNT_CREATION_REQUESTED -->|
     |                                  |                                          |-- Approve Account
     |                                  |                                          |   (Procurement API)
     |                                  |                                          |
     |                                  |-- Pub/Sub: ENTITLEMENT_CREATION_REQ ---->|
     |                                  |                                          |-- Filter by product
     |                                  |                                          |-- Auto-approve
     |                                  |                                          |   entitlement
     |                                  |                                          |
     |                                  |-- Pub/Sub: ENTITLEMENT_ACTIVE ---------->|
     |                                  |                                          |-- Store order
     |                                  |                                          |   (state=ACTIVE)
     |                                  |                                          |
     |<-- Subscription confirmed -------|                                          |
```

**What happens:**

1. The customer admin clicks "Subscribe" on the agent's Marketplace listing.
2. Google Cloud Marketplace emits a series of Pub/Sub events to the agent's
   marketplace handler service.
3. The handler filters events by product (using `SERVICE_CONTROL_SERVICE_NAME`).
   Account events pass through without filtering. For matching events:
   - `ACCOUNT_CREATION_REQUESTED` — auto-approves the account via Procurement API.
   - `ENTITLEMENT_CREATION_REQUESTED` — resolves the account ID (from the
     Procurement API if missing in the event), approves the account, then
     auto-approves the entitlement (order).
   - `ENTITLEMENT_ACTIVE` — marks the entitlement as active.
4. The result is an **`order_id`** (entitlement ID) in `ACTIVE` state, stored in
   the agent's database. This `order_id` is the key that links all subsequent
   authentication artifacts to this subscription.

**Error paths:**

| Failure | Behaviour |
|---|---|
| Invalid JSON in Pub/Sub message body | Handler returns `400 Invalid JSON body` |
| Pub/Sub message data is not valid base64 or UTF-8 JSON | Handler returns `400 Invalid message encoding` |
| Event has no `product` field (account event) | Handler processes the event normally (e.g. account approval); product filtering only applies to entitlement events |
| Event `product` does not match `SERVICE_CONTROL_SERVICE_NAME` | Handler returns `200` with `"Event not for this product"` (acknowledged; event belongs to a different agent) |
| Unknown event type (not a recognised `ProcurementEventType`) | Handler returns `200` with `"Unknown event: {type}"` (acknowledged so Pub/Sub does not retry) |
| Missing required fields in event payload | Handler returns `200` with `"Invalid event data"` (acknowledged) |
| Entitlement approval fails (Procurement API non-200) | Handler returns `500`; Pub/Sub will retry delivery |
| Plan change approval fails (Procurement API non-200) | Handler returns `500`; Pub/Sub will retry delivery |
| Network error or ADC credential failure during Procurement API call | Handler returns `500`; Pub/Sub will retry delivery |

> **Note:** Events that are structurally valid but not relevant (unknown
> type, wrong product, account-only) are acknowledged with `200` to prevent
> infinite retry loops. Procurement API failures (approval errors, network
> errors) return `500`, so Pub/Sub will redeliver the message automatically
> with exponential backoff.

---

## Step 2 — Agent Registration in Gemini Enterprise

After subscribing, the customer admin registers the agent inside Gemini
Enterprise. This step creates the OAuth 2.0 client credentials that Gemini
Enterprise will use to authenticate users against the agent.

There are **two modes** for credential provisioning:

### Option A — Dynamic Client Registration (DCR)

When `DCR_ENABLED=true`, Gemini Enterprise automatically creates OAuth client
credentials by calling the agent's DCR endpoint.

```
Gemini Enterprise                     Agent (Marketplace Handler)             Red Hat SSO (Keycloak)
     |                                          |                                    |
     |-- POST /dcr                              |                                    |
     |   { software_statement: <Google JWT> } ->|                                    |
     |                                          |                                    |
     |                                          |-- Validate Google JWT              |
     |                                          |   (verify signature, issuer,       |
     |                                          |    audience, extract claims:       |
     |                                          |    account_id, order_id,           |
     |                                          |    redirect_uris)                  |
     |                                          |                                    |
     |                                          |-- Validate account_id is ACTIVE    |
     |                                          |-- Validate order_id is ACTIVE      |
     |                                          |                                    |
     |                                          |-- POST /clients-registrations/     |
     |                                          |        openid-connect ------------>|
     |                                          |   (Initial Access Token auth)      |
     |                                          |                                    |-- Create OAuth
     |                                          |                                    |   client
     |                                          |<-- { client_id, client_secret } ---|
     |                                          |                                    |
     |                                          |-- Enable service accounts -------->|
     |                                          |   (Admin API)                      |
     |                                          |                                    |
     |                                          |-- Encrypt & store credentials      |
     |                                          |   (linked to order_id)             |
     |                                          |                                    |
     |<-- { client_id, client_secret,           |                                    |
     |      client_secret_expires_at: 0 } ------|                                    |
```

**What happens:**

1. Gemini Enterprise sends a `POST /dcr` request containing a
   `software_statement` — a JWT signed by Google's
   `cloud-agentspace@system.gserviceaccount.com` service account.
2. The agent validates the JWT:
   - Fetches Google's X.509 certificates and verifies the RS256 signature.
   - Checks the issuer and audience claims.
   - Extracts the `sub` (Procurement Account ID), `google.order` (Order ID),
     and `auth_app_redirect_uris`.
3. Cross-references with the marketplace database to confirm both the account
   and the order are in `ACTIVE` state.
4. Calls the Keycloak DCR endpoint to create a new OAuth client with:
   - `grant_types`: `authorization_code`, `refresh_token`, `client_credentials`
   - `token_endpoint_auth_method`: `client_secret_basic`
   - `redirect_uris`: from the Google JWT claims
   - `scope`: `agent:insights`
5. Enables service accounts on the new client via the Keycloak Admin API (for
   `client_credentials` grant support).
6. Encrypts the `client_secret` (Fernet symmetric encryption) and stores the
   mapping `order_id → client_id` in the database.
7. Returns the `client_id` and `client_secret` to Gemini Enterprise.

**Error paths (Option A):**

```
Gemini Enterprise                     Agent (Marketplace Handler)             Red Hat SSO (Keycloak)
     |                                          |                                    |
     |-- POST /dcr                              |                                    |
     |   { software_statement: <bad JWT> } ---->|                                    |
     |                                          |                                    |
     |                                          |-- Validate Google JWT              |
     |                                          |   FAIL:                            |
     |                                          |    - Invalid JWT format            |
     |                                          |    - Missing 'kid' in header       |
     |                                          |    - Unsupported algorithm          |
     |                                          |    - Signing key not found         |
     |                                          |    - JWT expired                   |
     |                                          |    - Invalid issuer                |
     |                                          |    - Missing google.order claim    |
     |                                          |                                    |
     |<-- 400 { error:                          |                                    |
     |   "invalid_software_statement",          |                                    |
     |   error_description: "..." } ------------|                                    |
```

| Failure | HTTP | Error code | Error description |
|---|---|---|---|
| JWT header cannot be decoded | 400 | `invalid_software_statement` | `Invalid JWT format` |
| JWT header missing `kid` | 400 | `invalid_software_statement` | `JWT header missing 'kid' claim` |
| Algorithm is not RS256 | 400 | `invalid_software_statement` | `Unsupported algorithm: {alg}. Expected RS256` |
| Signing key ID not in Google certificates | 400 | `invalid_software_statement` | `Key with ID '{kid}' not found in Google certificates` |
| JWT signature expired | 400 | `invalid_software_statement` | `JWT has expired` |
| JWT signature or claims invalid | 400 | `invalid_software_statement` | `{validation error}` |
| Issuer is not the expected Google service account | 400 | `invalid_software_statement` | `Invalid issuer. Expected: {GOOGLE_DCR_ISSUER}` |
| `google.order` claim missing from JWT | 400 | `invalid_software_statement` | `Missing google.order claim` |
| Claims cannot be parsed | 400 | `invalid_software_statement` | `Invalid claims format: {error}` |
| Account ID (`sub`) is not ACTIVE | 400 | `unapproved_software_statement` | `Invalid Procurement Account ID: {account_id}` |
| Order ID is not ACTIVE | 400 | `unapproved_software_statement` | `Invalid Order ID: {order_id}` |
| Initial Access Token not configured | 400 | `server_error` | `Failed to create OAuth client: DCR_INITIAL_ACCESS_TOKEN not configured` |
| Keycloak DCR endpoint returns error | 400 | `server_error` | `Failed to create OAuth client: Failed to create OAuth client: {keycloak_error}` |
| Network error calling Keycloak | 400 | `server_error` | `Failed to create OAuth client: HTTP error calling Keycloak DCR: {error}` |
| Unexpected error creating client or storing credentials | 400 | `server_error` | `Failed to create client: {error}` |
| Decryption of previously stored credentials fails | 400 | `server_error` | `Failed to retrieve existing credentials` |

> **Note:** All DCR errors return HTTP 400 regardless of the underlying cause.
> The `error` / `error_description` body follows RFC 7591. The "Decryption"
> error applies to both Option A and Option B — it triggers when a client
> already exists for the order but the stored secret cannot be decrypted.

### Option B — Static Credentials

When `DCR_ENABLED=false`, the customer admin must manually provision OAuth
client credentials in Red Hat SSO and provide them during registration. This
is the current default mode.

```
Customer Admin        Red Hat Google Form       Google Card Form         Gemini Enterprise        Agent (Marketplace Handler)        Red Hat SSO
     |                        |                        |                        |                            |                           |
     |-- Fill in request  --->|                        |                        |                            |                           |
     |   form (org details,   |                        |                        |                            |                           |
     |    contact info)       |                        |                        |                            |                           |
     |                        |-- Request processed    |                        |                            |                           |
     |                        |   by Red Hat team      |                        |                            |                           |
     |                        |                        |                        |                            |                           |
     |<-- Email with          |                        |                        |                            |                           |
     |   client_id and        |                        |                        |                            |                           |
     |   client_secret -------|                        |                        |                            |                           |
     |                        |                        |                        |                            |                           |
     |   [Credentials received — proceed to register]  |                        |                            |                           |
     |                        |                        |                        |                            |                           |
     |-- Open agent card -----|----------------------->|                        |                            |                           |
     |                        |                        |                        |                            |                           |
     |                        |                        |   (Card displays       |                            |                           |
     |                        |                        |    client_id and       |                            |                           |
     |                        |                        |    client_secret       |                            |                           |
     |                        |                        |    fields to fill in)  |                            |                           |
     |                        |                        |                        |                            |                           |
     |-- Enter client_id and  |                        |                        |                            |                           |
     |   client_secret from   |                        |                        |                            |                           |
     |   email ---------------|----------------------->|                        |                            |                           |
     |                        |                        |                        |                            |                           |
     |-- Submit form ---------|----------------------->|                        |                            |                           |
     |                        |                        |-- Register agent ----->|                            |                           |
     |                        |                        |                        |                            |                           |
     |                        |                        |                        |-- POST /dcr                |                           |
     |                        |                        |                        |   { software_statement,    |                           |
     |                        |                        |                        |     client_id,             |                           |
     |                        |                        |                        |     client_secret } ------>|                           |
     |                        |                        |                        |                            |                           |
     |                        |                        |                        |                            |-- Validate Google JWT     |
     |                        |                        |                        |                            |   (verify signature,      |
     |                        |                        |                        |                            |    issuer, audience,      |
     |                        |                        |                        |                            |    extract claims:        |
     |                        |                        |                        |                            |    account_id, order_id)  |
     |                        |                        |                        |                            |                           |
     |                        |                        |                        |                            |-- Validate account_id     |
     |                        |                        |                        |                            |   is ACTIVE               |
     |                        |                        |                        |                            |-- Validate order_id       |
     |                        |                        |                        |                            |   is ACTIVE               |
     |                        |                        |                        |                            |                           |
     |                        |                        |                        |                            |-- POST /token             |
     |                        |                        |                        |                            |   grant_type=             |
     |                        |                        |                        |                            |   client_credentials ---->|
     |                        |                        |                        |                            |                           |-- Validate
     |                        |                        |                        |                            |<-- 200 OK ----------------|   credentials
     |                        |                        |                        |                            |                           |
     |                        |                        |                        |                            |-- Encrypt & store         |
     |                        |                        |                        |                            |   credentials             |
     |                        |                        |                        |                            |   (linked to order_id)    |
     |                        |                        |                        |                            |                           |
     |                        |                        |                        |<-- { client_id,            |                           |
     |                        |                        |                        |      client_secret,        |                           |
     |                        |                        |                        |      client_secret_        |                           |
     |                        |                        |                        |      expires_at: 0 } ------|                           |
```

**What happens:**

1. **Credential request (prerequisite):** Before registering the agent, the
   customer admin must obtain OAuth client credentials from Red Hat. This is
   done by filling in the
   [Red Hat credential request form](https://forms.gle/PLACEHOLDER) with
   the required organization details and contact information. The Red Hat
   team processes the request, provisions the OAuth client in Red Hat SSO,
   and sends the `client_id` and `client_secret` to the customer admin
   **using Bitwarden Send url via email**.

   > **Note:** This is a one-time provisioning step. The customer admin
   > must complete this form and wait to receive the credentials by email
   > before proceeding with agent registration in Gemini Enterprise.

2. The customer admin opens the agent's card in Gemini Enterprise. The card
   displays a registration form with fields for `client_id` and
   `client_secret`.
3. The customer admin enters the `client_id` and `client_secret` received
   via email from Red Hat into the form.
4. Gemini Enterprise sends a `POST /dcr` request that includes both the
   `software_statement` JWT and the `client_id` / `client_secret` in the
   request body.
5. The agent validates the Google JWT and the account/order state (same as
   DCR mode).
6. The agent validates the provided credentials by performing a
   `client_credentials` grant against the Red Hat SSO token endpoint. If the
   grant succeeds, the credentials are confirmed valid.
7. The agent encrypts and stores the credentials linked to the `order_id`.
8. Returns the credentials back to Gemini Enterprise.

**Error paths (Option B):**

The same JWT validation and account/order state errors from Option A apply.
In addition, static credential mode has these specific errors:

```
Gemini Enterprise           Agent (Marketplace Handler)           Red Hat SSO (Keycloak)
     |                                 |                                    |
     |-- POST /dcr                     |                                    |
     |   { software_statement,         |                                    |
     |     client_id,                  |                                    |
     |     client_secret } ----------->|                                    |
     |                                 |                                    |
     |                                 |-- Validate Google JWT (same as A)  |
     |                                 |                                    |
     |                                 |-- Check client_id and              |
     |                                 |   client_secret present            |
     |                                 |   FAIL: one or both missing        |
     |                                 |                                    |
     |<-- 400 { error:                 |                                    |
     |   "invalid_client_metadata",    |                                    |
     |   error_description:            |                                    |
     |   "...both must be provided"}---|                                    |
     |                                 |                                    |
     |   --- OR ---                    |                                    |
     |                                 |                                    |
     |                                 |-- POST /token                      |
     |                                 |   grant_type=client_credentials -->|
     |                                 |                                    |-- Validate
     |                                 |<-- 401 (invalid credentials) ------|   FAIL
     |                                 |                                    |
     |<-- 400 { error:                 |                                    |
     |   "invalid_client_metadata",    |                                    |
     |   error_description:            |                                    |
     |   "Invalid client credentials"}-|                                    |
```

| Failure | HTTP | Error code | Error description |
|---|---|---|---|
| `client_id` or `client_secret` missing from request body | 400 | `invalid_client_metadata` | `Static credentials mode: both client_id and client_secret must be provided in the request body.` |
| Credentials fail `client_credentials` grant against SSO | 400 | `invalid_client_metadata` | `Invalid client credentials: client_id={client_id} failed validation against the OAuth server.` |
| Database error storing credentials | 400 | `server_error` | `Failed to store client credentials: {error}` |

---

## Step 3 — User Authentication (OAuth 2.0 Authorization Code Flow)

Once the agent is registered, customer users can interact with it through
Gemini Enterprise. Authentication uses the **OAuth 2.0 Authorization Code
flow** where:

- **Client**: Gemini Enterprise
- **Authorization Server**: Red Hat SSO (Keycloak)
- **Resource Server**: The Lightspeed Agent
- **Resource Owner**: The customer user (with Red Hat credentials)

```
Customer User          Gemini Enterprise            Red Hat SSO (Keycloak)           Lightspeed Agent
     |                       |                              |                              |
     |-- Use agent --------->|                              |                              |
     |                       |                              |                              |
     |   [OAuth 2.0 Authorization Code Flow]                |                              |
     |                       |                              |                              |
     |<-- 302 Redirect ------|                              |                              |
     |   to Red Hat SSO      |                              |                              |
     |   /auth?              |                              |                              |
     |   response_type=code  |                              |                              |
     |   client_id=<ge_id>   |                              |                              |
     |   redirect_uri=<uri>  |                              |                              |
     |   scope=openid        |                              |                              |
     |     agent:insights    |                              |                              |
     |   state=<csrf_state>  |                              |                              |
     |                       |                              |                              |
     |-- Follow redirect ----|----------------------------->|                              |
     |                       |                              |                              |
     |<-- Red Hat login   ---|------------------------------|                              |
     |                       |                              |                              |
     |-- Enter Red Hat    ---|----------------------------->|                              |
     |   credentials         |                              |                              |
     |   (username/password) |                              |-- Authenticate user          |
     |                       |                              |-- Verify scope consent       |
     |                       |                              |                              |
     |<-- 302 Redirect ------|------------------------------|                              |
     |   to redirect_uri     |                              |                              |
     |   ?code=<auth_code>   |                              |                              |
     |   &state=<csrf_state> |                              |                              |
     |                       |                              |                              |
     |-- Follow redirect --->|                              |                              |
     |                       |                              |                              |
     |                       |-- POST /token                |                              |
     |                       |   grant_type=                |                              |
     |                       |     authorization_code       |                              |
     |                       |   code=<auth_code>           |                              |
     |                       |   redirect_uri=<callback>    |                              |
     |                       |   client_id=<ge_client_id>   |                              |
     |                       |   client_secret=<ge_secret>  |                              |
     |                       |----------------------------->|                              |
     |                       |                              |                              |
     |                       |<-- {                         |                              |
     |                       |      access_token: <JWT>,    |                              |
     |                       |      refresh_token: <...>,   |                              |
     |                       |      token_type: "Bearer",   |                              |
     |                       |      expires_in: 300,        |                              |
     |                       |      scope: "openid          |                              |
     |                       |        agent:insights"       |                              |
     |                       |    } ------------------------|                              |
     |                       |                              |                              |
     |                       |   [Access token obtained — user is authenticated]           |
     |                       |                              |                              |
```

**What happens:**

1. The customer user initiates an interaction with the agent through Gemini
   Enterprise.
2. Gemini Enterprise redirects the user's browser to the Red Hat SSO
   authorization endpoint with:
   - `response_type=code` (authorization code flow)
   - `client_id` = the Gemini Enterprise client ID linked to this order
     (created via DCR or provided as static credentials)
   - `redirect_uri` = Gemini Enterprise's callback URL (from the registration
     `redirect_uris`)
   - `scope` = `openid agent:insights`
   - `state` = CSRF protection token
3. The user sees the Red Hat SSO login page and authenticates with their
   **Red Hat credentials** (username/password, or federated SSO).
4. After successful authentication, Red Hat SSO redirects the user back to
   Gemini Enterprise with an **authorization code**.
5. Gemini Enterprise exchanges the authorization code for tokens by calling
   the Red Hat SSO token endpoint with:
   - `grant_type=authorization_code`
   - The authorization code
   - The `client_id` and `client_secret` (HTTP Basic or POST body)
6. Red Hat SSO returns an **access token** (JWT), a refresh token, and token
   metadata. The access token contains the user's identity claims and the
   granted scopes (including `agent:insights`).

**Key point:** The user authenticates with their Red Hat identity. The access
token is issued by Red Hat SSO and represents both the user's identity and the
Gemini Enterprise client's authorization to act on their behalf.

**Error paths:**

The authorization code flow is executed between Gemini Enterprise (client) and
Red Hat SSO (authorization server). The Lightspeed Agent is not involved in
this step, so errors are handled by those two parties. Common failures include:

| Failure | Handled by | Behaviour |
|---|---|---|
| User enters invalid Red Hat credentials | Red Hat SSO | Login page shows an error; user can retry |
| User denies consent for requested scopes | Red Hat SSO | Redirects to `redirect_uri` with `error=access_denied` |
| Invalid `client_id` in authorization request | Red Hat SSO | Returns `error=unauthorized_client` to Gemini |
| `redirect_uri` does not match registered URIs | Red Hat SSO | Refuses to redirect; shows error page |
| Authorization code expired or already used | Red Hat SSO | Token endpoint returns `error=invalid_grant` |
| Invalid `client_secret` on token exchange | Red Hat SSO | Token endpoint returns `error=invalid_client` |

---

## Step 4 — Agent Authentication and Authorization (Token Introspection)

When Gemini Enterprise sends a request to the agent, it includes the access
token obtained in the previous step. The agent validates this token using
**RFC 7662 Token Introspection**, where the agent authenticates to the
introspection endpoint using its **own** client credentials (the Resource
Server pattern).

```
Gemini Enterprise                  Lightspeed Agent                      Red Hat SSO (Keycloak)
     |                                    |                                       |
     |-- POST /                           |                                       |
     |   Authorization: Bearer <token>    |                                       |
     |   (A2A JSON-RPC request) --------->|                                       |
     |                                    |                                       |
     |                                    |-- POST /token/introspect              |
     |                                    |   token=<bearer_token>                |
     |                                    |   token_type_hint=access_token        |
     |                                    |   Authorization: Basic                |
     |                                    |     base64(AGENT_CLIENT_ID            |
     |                                    |            :AGENT_CLIENT_SECRET) ---->|
     |                                    |                                       |
     |                                    |                                       |-- Validate token
     |                                    |                                       |-- Return claims
     |                                    |                                       |
     |                                    |<-- {                                  |
     |                                    |      "active": true,                  |
     |                                    |      "sub": "<user-id>",              |
     |                                    |      "azp": "<ge-client-id>",         |
     |                                    |      "scope": "openid agent:insights",|
     |                                    |      "preferred_username": "jdoe",    |
     |                                    |      "email": "jdoe@example.com",     |
     |                                    |      "org_id": "<org-id>",            |
     |                                    |      "exp": 1234567890                |
     |                                    |    } ---------------------------------|
     |                                    |                                       |
     |                                    |-- Verify "active" == true             |
     |                                    |-- Verify "agent:insights" in scopes   |
     |                                    |                                       |
     |                                    |-- Resolve order:                      |
     |                                    |   azp (ge-client-id)                  |
     |                                    |    → credentials DB → order_id        |
     |                                    |    → Entitlement DB → state == ACTIVE |
     |                                    |                                       |
     |                                    |-- Store token in ContextVar           |
     |                                    |   for downstream forwarding           |
     |                                    |                                       |
     |<-- A2A response -------------------|                                       |
```

**What happens:**

1. Gemini Enterprise sends an A2A request (JSON-RPC over HTTP) to the agent's
   root endpoint (`POST /`) with the access token in the `Authorization:
   Bearer <token>` header.

2. The agent's `AuthenticationMiddleware` intercepts the request and:

   a. **Extracts** the Bearer token from the `Authorization` header.

   b. **Introspects** the token by calling the Red Hat SSO introspection
      endpoint (`/protocol/openid-connect/token/introspect`). The agent
      authenticates this call using its **own** credentials
      (`RED_HAT_SSO_CLIENT_ID` / `RED_HAT_SSO_CLIENT_SECRET`), not the
      Gemini Enterprise credentials. This is the standard Resource Server pattern —
      the agent's client credentials give it permission to introspect any
      token issued within the realm.

   c. **Validates** the introspection response:
      - `active` must be `true` (token is not expired/revoked).
      - The `agent:insights` scope must be present in the token's scope list.
        If missing, the agent returns `403 Forbidden`.

   d. **Resolves the order**: Uses the `azp` (authorized party) claim from
      the introspection response — this is the Gemini Enterprise `client_id`
      — to look up the corresponding `order_id` in the credentials database.
      Then verifies the marketplace entitlement for that `order_id` is in
      `ACTIVE` state. If the order is not found or not active, the agent
      returns `403 Forbidden`.

   e. **Stores the token** in a request-scoped `ContextVar` so it can be
      forwarded to downstream MCP servers.

3. If all checks pass, the request proceeds to the agent's business logic.

**Error paths:**

```
Gemini Enterprise                  Lightspeed Agent                      Red Hat SSO (Keycloak)
     |                                    |                                       |
     |-- POST /                           |                                       |
     |   (no Authorization header) ------>|                                       |
     |                                    |                                       |
     |<-- 401 { code: -32001,             |                                       |
     |   message: "Unauthorized",         |                                       |
     |   detail: "Missing Authorization   |                                       |
     |            header" } --------------|                                       |
     |                                    |                                       |
     |   --- OR ---                       |                                       |
     |                                    |                                       |
     |-- POST /                           |                                       |
     |   Authorization: Bearer <token> -->|                                       |
     |                                    |-- POST /token/introspect              |
     |                                    |   token=<bearer_token> -------------->|
     |                                    |                                       |-- Token expired
     |                                    |<-- { "active": false } ---------------|   or revoked
     |                                    |                                       |
     |<-- 401 { code: -32001,             |                                       |
     |   message: "Unauthorized",         |                                       |
     |   detail: "Token is not active" }--|                                       |
     |                                    |                                       |
     |   --- OR ---                       |                                       |
     |                                    |                                       |
     |                                    |<-- { "active": true,                  |
     |                                    |      "scope": "openid" } -------------|
     |                                    |   (missing agent:insights scope)      |
     |                                    |                                       |
     |<-- 403 { code: -32003,             |                                       |
     |   message: "Forbidden",            |                                       |
     |   detail: "Token is missing        |                                       |
     |     required scope:                |                                       |
     |     agent:insights" } -------------|                                       |
     |                                    |                                       |
     |   --- OR ---                       |                                       |
     |                                    |                                       |
     |                                    |-- Look up azp → order_id              |
     |                                    |   FAIL: client not in credentials DB  |
     |                                    |         or order not ACTIVE           |
     |                                    |                                       |
     |<-- 403 { code: -32003,             |                                       |
     |   message: "Forbidden",            |                                       |
     |   detail: "No active order found   |                                       |
     |     for this client" } ------------|                                       |
```

| Failure | HTTP | JSON-RPC code | Detail |
|---|---|---|---|
| No `Authorization` header in request | 401 | -32001 | `Missing Authorization header` |
| Header does not start with `Bearer ` | 401 | -32001 | `Invalid Authorization header format` |
| Token is expired or revoked (`active: false`) | 401 | -32001 | `Token is not active` |
| Introspection endpoint returns non-200 | 401 | -32001 | `Introspection request failed (HTTP {status})` |
| Network error calling introspection endpoint | 401 | -32001 | `HTTP error calling introspection endpoint: {error}` |
| Token missing `agent:insights` scope | 403 | -32003 | `Token is missing required scope: agent:insights` |
| `azp` client ID not found in credentials DB | 403 | -32003 | `No active order found for this client` |
| Order ID not found in entitlements DB | 403 | -32003 | `No active order found for this client` |
| Order state is not `ACTIVE` | 403 | -32003 | `No active order found for this client` |
| Rate limit exceeded for order/user/client/IP | 429 | — | `Rate limit exceeded ({exceeded}) for {principal}` where `{exceeded}` is `per_minute` or `per_hour` (includes `Retry-After` header) |
| Rate limiter backend (Redis) unavailable | 503 | — | `Rate limiter backend unavailable` |

**Credentials distinction:**

| Credential | Owner | Purpose |
|---|---|---|
| `RED_HAT_SSO_CLIENT_ID` / `RED_HAT_SSO_CLIENT_SECRET` | The agent itself (Resource Server) | Authenticating to the introspection endpoint to validate incoming Bearer tokens |
| GE `client_id` / `client_secret` | Gemini Enterprise (OAuth Client) | Obtaining access tokens on behalf of users via the authorization code flow |

---

## Step 5 — Token Forwarding to MCP Server (Red Hat Lightspeed APIs)

After authenticating the request, the agent processes the user's query by
invoking tools on the MCP (Model Context Protocol) server. The MCP server
provides access to Red Hat Lightspeed APIs, which also require authentication.
The agent forwards the **same Bearer token** it received from Gemini
Enterprise to the MCP server, enabling transparent end-to-end authentication.

```
Lightspeed Agent                         MCP Server                    Red Hat Lightspeed APIs
     |                                       |                                  |
     |-- [Process user query]                |                                  |
     |-- [Invoke MCP tool]                   |                                  |
     |                                       |                                  |
     |   [Header provider forwards Bearer token from request context]           |
     |     Authorization: Bearer <token>     |                                  |
     |                                       |                                  |
     |-- MCP tool request ------------------>|                                  |
     |   (with auth headers)                 |                                  |
     |                                       |-- Call Lightspeed API ---------->|
     |                                       |   (forward credentials)          |
     |                                       |                                  |-- Authenticate
     |                                       |                                  |   request
     |                                       |                                  |
     |                                       |<-- API response -----------------|
     |                                       |                                  |
     |<-- MCP tool result -------------------|                                  |
     |                                       |                                  |
     |-- [Format and return A2A response]    |                                  |
```

**Token pass-through:**

The agent forwards the caller's Bearer token (stored in the request-scoped
`ContextVar` during middleware processing) as an `Authorization: Bearer <token>`
header. The MCP server and downstream Lightspeed APIs validate this token
independently. This mode preserves the user's identity end-to-end.

**Transport modes:**

- **HTTP/SSE transport**: Headers are injected directly into HTTP requests
  to the MCP server.
- **stdio transport**: Not applicable for the Marketplace deployment.

**Error paths:**

| Failure | Behaviour |
|---|---|
| No Bearer token in request context | Warning logged: `No MCP credentials available`; empty headers sent — MCP server will reject the unauthenticated request |
| Forwarded access token is expired | Warning logged: `Access token expired at {exp}`; token is still forwarded — MCP server will reject it and the error propagates back to the caller |
| MCP server or downstream Lightspeed API rejects the token | MCP tool call returns an error result; the agent surfaces this in the A2A response to Gemini Enterprise |

> **Note:** The agent intentionally forwards expired tokens rather than
> pre-emptively rejecting them. This avoids clock-skew issues and lets the
> downstream service be the authoritative validator.

---

## Complete End-to-End Flow (Summary)

```
 SUBSCRIPTION          REGISTRATION             USER AUTH                  AGENT AUTH              MCP/API AUTH
 ============          ============             =========                  ==========              ============

 Customer Admin        Customer Admin           Customer User              Gemini Enterprise       Lightspeed Agent
      |                     |                        |                          |                        |
 1. Subscribe to       2a. [DCR] Gemini          3. User opens             4. Gemini sends          5. Agent calls
    agent on GCP           auto-creates             agent in                  request to agent         MCP server
    Marketplace            OAuth client             Gemini                    with Bearer token        with same
      |                    in Red Hat SSO              |                        |                      Bearer token
      v                     |                    3a. Redirect to           4a. Agent introspects         |
 order_id created      2b. [Static] Admin            Red Hat SSO               token using its      5a. MCP server
 (ACTIVE state)            requests creds            login page                own credentials          forwards to
                           via Red Hat                  |                        |                       Lightspeed
                           Google Form           3b. User logs in          4b. Agent validates          APIs
                           → receives                with Red Hat              scope and                 |
                           client_id/secret          credentials               order status         5b. Lightspeed
                           by email                    |                        |                       APIs validate
                            |                    3c. Auth code             4c. Request proceeds         token/creds
                       2b'. Admin enters              exchanged for             if valid                  |
                           credentials in             access token               |                  5c. Response
                           Gemini card                  |                        v                      flows back
                           form                  3d. Access token          Order-bound,                 to user
                            |                        ready to use          scope-validated
                       2c. Credentials                                     request
                           validated and
                           stored (linked
                           to order_id)
```

---

## Security Properties

- **Order-bound access**: Every authenticated request is tied to an active
  marketplace order. Cancelled or expired subscriptions are immediately
  rejected.
- **Scope-based authorization**: The `agent:insights` scope must be present
  in the access token. Tokens without this scope receive `403 Forbidden`.
- **Secrets encrypted at rest**: All client secrets stored in the database are
  encrypted with Fernet (symmetric AES-128-CBC with HMAC-SHA256).
- **Token introspection (not local JWT verification)**: The agent validates
  tokens by calling the authorization server's introspection endpoint. This
  ensures revoked tokens are immediately rejected without waiting for
  expiration.
- **End-to-end identity propagation**: The user's Bearer token is forwarded
  to the MCP server and downstream APIs, preserving the user's identity
  across the full call chain.
- **CSRF protection**: The OAuth 2.0 authorization code flow uses the `state`
  parameter for CSRF protection during browser redirects.
