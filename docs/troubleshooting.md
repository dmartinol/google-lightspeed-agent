# Troubleshooting Guide

This guide helps diagnose and resolve common issues with the Lightspeed Agent.

## Quick Diagnostics

### Health Check

```bash
# Check if agent is running
curl http://localhost:8000/health

# Expected response
{"status": "healthy", "agent": "lightspeed_agent"}
```

### Readiness Check

```bash
# Check if agent is ready to accept requests
curl http://localhost:8000/ready

# Expected response
{"status": "ready", "agent": "lightspeed_agent"}
```

### View Logs

```bash
# Local development
python -m lightspeed_agent.main 2>&1 | tee agent.log

# Podman
podman logs lightspeed-agent-pod-lightspeed-agent

# Cloud Run
gcloud run services logs read lightspeed-agent --region=us-central1
```

## Startup Issues

### Agent Fails to Start

**Symptom**: Agent exits immediately after starting

**Check Configuration**:

```bash
# Validate environment
python -c "from lightspeed_agent.config import get_settings; print(get_settings())"
```

**Common Causes**:

| Error | Cause | Solution |
|-------|-------|----------|
| `ValidationError: google_api_key` | Missing API key | Set `GOOGLE_API_KEY` |
| `MCP connection failed` | MCP server not reachable | Check `MCP_SERVER_URL` and MCP server status |
| `Connection refused` | Database not running | Start PostgreSQL |

### Port Already in Use

**Symptom**: `Address already in use`

```bash
# Find process using port 8000
lsof -i :8000

# Kill the process
kill -9 <PID>

# Or use a different port
AGENT_PORT=8001 python -m lightspeed_agent.main
```

### Import Errors

**Symptom**: `ModuleNotFoundError`

```bash
# Ensure virtual environment is activated
source .venv/bin/activate

# Reinstall dependencies
pip install -e ".[dev]"
```

## Authentication Issues

### 401 Unauthorized

**Symptom**: All authenticated requests return 401

**Check Token**:

```bash
# Decode JWT (without verification)
echo $TOKEN | cut -d. -f2 | base64 -d 2>/dev/null | jq .
```

**Common Causes**:

| Issue | Cause | Solution |
|-------|-------|----------|
| Token not active | Token expired or revoked | Get new token via OAuth or `client_credentials` |
| Introspection failed | Agent can't reach Keycloak | Check `RED_HAT_SSO_ISSUER` and network |
| Wrong credentials | Agent client_id/secret invalid | Check `RED_HAT_SSO_CLIENT_ID/SECRET` |

**Test Introspection Endpoint**:

```bash
# Verify the introspection endpoint is reachable
curl -s -X POST \
  "$RED_HAT_SSO_ISSUER/protocol/openid-connect/token/introspect" \
  -u "$RED_HAT_SSO_CLIENT_ID:$RED_HAT_SSO_CLIENT_SECRET" \
  -d "token=$TOKEN"
```

### 403 Forbidden

**Symptom**: Authenticated but access denied — token is valid but missing required scope

**Check Scopes**:

```bash
# Decode token and check scope claim
echo $TOKEN | cut -d. -f2 | base64 -d 2>/dev/null | jq .scope
```

**Required Scope**: `agent:insights` (configurable via `AGENT_REQUIRED_SCOPE`)

**Fix**: Ensure the `agent:insights` Client Scope exists in Keycloak and is assigned to the client that issued the token.

### OAuth Callback Errors

**Symptom**: Callback fails with error

| Error | Cause | Solution |
|-------|-------|----------|
| `invalid_grant` | Code expired or reused | Restart OAuth flow |
| `redirect_uri_mismatch` | URI doesn't match registered | Update redirect URI |
| `invalid_client` | Wrong client credentials | Check client_id/secret |

### Introspection Endpoint Failures

**Symptom**: `Introspection request failed`

```bash
# Test introspection endpoint connectivity
curl -s -o /dev/null -w "%{http_code}" \
  "$RED_HAT_SSO_ISSUER/protocol/openid-connect/token/introspect" \
  -u "$RED_HAT_SSO_CLIENT_ID:$RED_HAT_SSO_CLIENT_SECRET" \
  -d "token=test"
# Should return 200 (with {"active": false})
```

**Causes**:
- Network connectivity issues
- Firewall blocking outbound HTTPS
- SSO service unavailable
- Invalid `RED_HAT_SSO_CLIENT_ID` / `RED_HAT_SSO_CLIENT_SECRET`

## Agent/AI Issues

### No Response from Agent

**Symptom**: Agent returns empty response

**Check Gemini Connection**:

```bash
# Test Gemini API directly
curl -X POST "https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent?key=$GOOGLE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"contents":[{"parts":[{"text":"Hello"}]}]}'
```

**Common Causes**:

| Issue | Cause | Solution |
|-------|-------|----------|
| API quota exceeded | Too many requests | Wait or increase quota |
| Invalid API key | Key revoked/invalid | Generate new key |
| Model not available | Region restriction | Use supported region |

### Slow Responses

**Symptom**: Requests take > 30 seconds

**Causes**:
- Cold start (first request after idle)
- Complex queries requiring multiple tool calls
- MCP server latency

**Solutions**:
- Set `min-instances=1` in Cloud Run
- Enable CPU boost for startup
- Add request timeouts

### Tool Execution Failures

**Symptom**: `Tool execution failed`

**Check MCP Connection**:

```bash
# Test MCP server (if using HTTP transport)
curl http://localhost:8080/health

# Check MCP server URL
echo $MCP_SERVER_URL
```

**Common Causes**:

| Issue | Cause | Solution |
|-------|-------|----------|
| `Authentication failed` | Invalid or expired JWT token | Ensure the caller has a valid Bearer token |
| `Connection refused` | MCP server not running | Start MCP server |
| `Timeout` | Network/server issues | Increase timeout |

## Database Issues

### Connection Failures

**Symptom**: `Connection refused` or timeout

**PostgreSQL**:

```bash
# Check PostgreSQL is running
pg_isready -h localhost -p 5432

# Test connection
psql postgresql://insights:insights@localhost:5432/lightspeed_agent
```

**SQLite**:

```bash
# Check database file permissions
ls -la lightspeed_agent.db

# Test with sqlite3
sqlite3 lightspeed_agent.db ".tables"
```

### Migration Errors

**Symptom**: `Table does not exist`

```bash
# Run migrations
alembic upgrade head

# Check current revision
alembic current
```

**Symptom**: `usage_records` table exists but atomic upsert fails (e.g. `ON CONFLICT` error)

If you upgraded from an older version that created `usage_records` without the partial unique index, add it manually (PostgreSQL):

```sql
-- Add tool_calls counter (for metering)
ALTER TABLE usage_records ADD COLUMN IF NOT EXISTS tool_calls INTEGER NOT NULL DEFAULT 0;

-- Add reporting_started_at column (for reporting)
ALTER TABLE usage_records ADD COLUMN IF NOT EXISTS reporting_started_at TIMESTAMP WITH TIME ZONE;

-- Create partial unique index (available = unreported AND not claimed)
CREATE UNIQUE INDEX uq_usage_records_order_period_unreported 
   ON usage_records (order_id, period_start, period_end) 
   WHERE reported IS FALSE AND reporting_started_at IS NULL;
```

## Container/Pod Issues

### Pod Won't Start

**Symptom**: Containers crash or restart

```bash
# Check pod status
podman pod ps

# Check container logs
podman logs lightspeed-agent-pod-lightspeed-agent

# Describe pod
podman pod inspect lightspeed-agent-pod
```

### Image Pull Failures

**Symptom**: `Image not found`

```bash
# Login to registry
podman login registry.access.redhat.com

# Pull image manually
podman pull registry.access.redhat.com/ubi9/python-312-minimal:latest
```

### Volume Mount Issues

**Symptom**: Config not found

```bash
# Check config directory exists
ls -la ./config/

# Check volume mounts
podman inspect lightspeed-agent-pod-lightspeed-agent | jq '.[].Mounts'
```

## Cloud Run Issues

### Deployment Failures

**Symptom**: Deploy command fails

```bash
# Check Cloud Build logs
gcloud builds list --limit=5

# Get build details
gcloud builds describe BUILD_ID
```

### Service Not Accessible

**Symptom**: 503 Service Unavailable

```bash
# Check service status
gcloud run services describe lightspeed-agent --region=us-central1

# Check revision status
gcloud run revisions list --service=lightspeed-agent --region=us-central1
```

### Cold Start Timeouts

**Symptom**: First request times out

**Solutions**:
1. Set minimum instances:
   ```bash
   gcloud run services update lightspeed-agent --min-instances=1
   ```

2. Enable CPU boost:
   ```bash
   gcloud run services update lightspeed-agent \
     --cpu-boost
   ```

## Performance Issues

### High Latency

**Diagnose**:

```bash
# Time a request
time curl http://localhost:8000/health

# Profile with detailed timing
curl -w "@curl-format.txt" -o /dev/null -s http://localhost:8000/a2a
```

**curl-format.txt**:
```
     time_namelookup:  %{time_namelookup}s\n
        time_connect:  %{time_connect}s\n
     time_appconnect:  %{time_appconnect}s\n
    time_pretransfer:  %{time_pretransfer}s\n
       time_redirect:  %{time_redirect}s\n
  time_starttransfer:  %{time_starttransfer}s\n
          time_total:  %{time_total}s\n
```

### Memory Issues

**Symptom**: OOM kills

```bash
# Monitor memory usage
podman stats lightspeed-agent-pod-lightspeed-agent

# Increase memory limit
# Edit lightspeed-agent-pod.yaml or Cloud Run config
```

## Logging and Debugging

### Enable Debug Logging

```bash
# Set environment variable
LOG_LEVEL=DEBUG python -m lightspeed_agent.main
```

### Enable Debug Mode

```bash
# Enables /docs endpoint
DEBUG=true python -m lightspeed_agent.main

# Access Swagger UI
open http://localhost:8000/docs
```

### Common Log Messages

| Message | Meaning | Action |
|---------|---------|--------|
| `Token validation failed` | Invalid/inactive token | Check token and introspection endpoint |
| `Insufficient scope` | Missing `agent:insights` | Add scope to client in Keycloak |
| `Tool execution failed` | MCP error | Check MCP server |
| `Rate limit exceeded` | Too many requests | Wait or upgrade |
| `Database connection failed` | DB unreachable | Check database |

## Getting Help

### Collect Diagnostic Information

Before reporting an issue, collect:

1. **Logs**:
   ```bash
   LOG_LEVEL=DEBUG python -m lightspeed_agent.main 2>&1 | tee debug.log
   ```

2. **Configuration** (redact secrets):
   ```bash
   env | grep -E '^(AGENT|GOOGLE|RED_HAT|MCP|LOG)' | sed 's/=.*/=REDACTED/'
   ```

3. **Version Info**:
   ```bash
   python --version
   pip show lightspeed-agent
   ```

4. **Request/Response** (redact tokens):
   ```bash
   curl -v http://localhost:8000/health 2>&1
   ```

### Report Issues

File issues at: https://github.com/your-org/lightspeed-agent/issues

Include:
- Description of the problem
- Steps to reproduce
- Expected vs actual behavior
- Diagnostic information collected above
