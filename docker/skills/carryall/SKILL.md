---
name: carryall
description: "Policy-enforced access to SLOS vaults using Authority Runtime envelopes. Use when checking permissions, listing vaults, reading documents, or querying the audit trail for AI agent actions."
---

# Carryall - Authority Runtime for SLOS

Carryall enforces cryptographic permission envelopes on AI agent access to SLOS data vaults. Every action is policy-checked and audit-logged.

The carryall service is available at the `CARRYALL_URL` environment variable (defaults to `http://carryall.default.svc.cluster.local:8765`). All policy checks go through this HTTP API.

## HTTP API Endpoints

### Health Check
```bash
curl -s ${CARRYALL_URL:-http://carryall.default.svc.cluster.local:8765}/health
```
Returns: `{"status": "healthy", "service": "carryall-mcp"}`

### List Available Tools
```bash
curl -s ${CARRYALL_URL:-http://carryall.default.svc.cluster.local:8765}/tools
```
Returns list of MCP tools: `carryall_check_access`, `carryall_list_vaults`, `carryall_get_metadata`, `carryall_audit_log`, `carryall_compile_policy`

### Check Access (Policy Enforcement)

Check if an action is allowed before executing:
```bash
curl -X POST ${CARRYALL_URL:-http://carryall.default.svc.cluster.local:8765}/tools/carryall_check_access \
  -H "Content-Type: application/json" \
  -d '{
    "envelope": {
      "envelope_id": "env-001",
      "agent_id": "finance-agent",
      "provider": "carryall",
      "created_at": "2026-01-31T12:00:00Z",
      "expires_at": "2026-02-01T12:00:00Z",
      "ttl_seconds": 86400,
      "authority": {
        "scopes": ["vault:finance:read"],
        "resources": ["slos://vaults/finance/*"]
      },
      "signature": "..."
    },
    "action": "read",
    "resource": "slos://vaults/finance/doc-001"
  }'
```

Response:
- `{"decision": "allow", "reason": "..."}`
- `{"decision": "deny", "reason": "..."}`
- `{"decision": "require_approval", "reason": "..."}`

### List Vaults
```bash
curl -X POST ${CARRYALL_URL:-http://carryall.default.svc.cluster.local:8765}/tools/carryall_list_vaults \
  -H "Content-Type: application/json" \
  -d '{"envelope": {...}}'
```

### Get Document Metadata
```bash
curl -X POST ${CARRYALL_URL:-http://carryall.default.svc.cluster.local:8765}/tools/carryall_get_metadata \
  -H "Content-Type: application/json" \
  -d '{
    "envelope": {...},
    "uri": "slos://vaults/finance/doc-001"
  }'
```

### Query Audit Log
```bash
curl -X POST ${CARRYALL_URL:-http://carryall.default.svc.cluster.local:8765}/tools/carryall_audit_log \
  -H "Content-Type: application/json" \
  -d '{
    "envelope": {...},
    "agent_id": "finance-agent",
    "limit": 50
  }'
```

### Compile Policy (LLM Intent-to-Envelope)

**This is the key differentiator.** Use LLM to translate natural language intent into a minimal permission envelope. Instead of manually constructing envelopes, describe what you want to do:

```bash
curl -X POST ${CARRYALL_URL:-http://carryall.default.svc.cluster.local:8765}/tools/carryall_compile_policy \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "research-agent",
    "intent": "I need to read the Q4 finance report and summarize key metrics",
    "available_scopes": ["vault:finance:read", "vault:hr:read", "vault:shared:read"],
    "available_resources": ["slos://vaults/finance/*", "slos://vaults/shared/*"],
    "ttl_seconds": 300,
    "llm_provider": "openai"
  }'
```

Response includes:
- **envelope**: Signed envelope with minimal scopes (e.g., only `vault:finance:read`)
- **compilation.reasoning**: Why these scopes were selected
- **compilation.scope_reduction_ratio**: Token/scope reduction achieved (typically 60-80%)
- **metrics**: LLM token usage and cost

This enables least-privilege execution: agents request broad capabilities, but only get the minimum needed for each specific task.

## Policy Model

- **ALLOW**: Agent has explicit access or matching envelope scope
- **DENY**: Agent is explicitly denied or lacks required scope
- **REQUIRE_APPROVAL**: Agent needs human approval for this resource

Policy evaluation order: denied_agents > requires_approval > allowed_agents > envelope scopes > default deny.

## Scope Format

Scopes follow `namespace:resource:action` pattern:
- `vault:finance:read` - Read access to finance vault
- `vault:shared:write` - Write access to shared vault
- `vault:*:read` - Read access to all vaults
- `audit:read` - Required for audit log queries

## Workflow: Secure Document Access

1. **Check if action is allowed**:
   ```bash
   result=$(curl -s -X POST ${CARRYALL_URL:-http://carryall.default.svc.cluster.local:8765}/tools/carryall_check_access \
     -H "Content-Type: application/json" \
     -d '{"envelope": {...}, "action": "read", "resource": "slos://vaults/finance/doc-001"}')
   ```

2. **Parse decision**:
   ```bash
   decision=$(echo "$result" | jq -r '.content[0].text | fromjson | .decision')
   ```

3. **Only proceed if allowed**:
   ```bash
   if [ "$decision" = "allow" ]; then
     # Proceed with document access
     curl -s -X POST ${CARRYALL_URL:-http://carryall.default.svc.cluster.local:8765}/tools/carryall_get_metadata ...
   else
     echo "Access denied: $result"
   fi
   ```

## Important: All Actions Are Logged

Every call to carryall is recorded in the audit trail with:
- Timestamp
- Agent ID (from envelope)
- Action attempted
- Resource accessed
- Decision (allow/deny/require_approval)
- Envelope signature verification result

This creates an immutable record of all AI agent data access.
