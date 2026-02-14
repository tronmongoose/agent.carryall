---
name: carryall
description: "AI agent permission management using cryptographic Authority Envelopes. Use carryall tools when agents need minimal permissions, policy compilation from intent, access checks, or audit trails."
---

# Carryall - Authority Runtime for AI Agents

Carryall provides cryptographic permission envelopes for AI agents, enforcing least-privilege access with full audit trails.

## Available Tools

### carryall_compile_policy (Primary Tool)

Translate natural language intent into a minimal permission envelope. The LLM selects only the scopes actually needed for the request.

Example: "I need to read the Q4 finance report" → envelope with only `vault:finance:read` (not vault:hr:read or vault:shared:write).

Parameters:
- `agent_id` (required): Agent requesting the envelope
- `intent` (required): Natural language description of what the agent needs
- `available_scopes` (required): Scopes the agent may request from
- `available_resources` (required): Resource patterns the agent can access
- `ttl_seconds` (optional): Envelope lifetime (default: 300s)
- `llm_provider` (optional): "openai" or "anthropic"

### carryall_check_access

Check if an envelope allows a specific action on a resource. Returns allow, deny, or require_approval.

### carryall_list_vaults

List available SLOS vaults (requires valid envelope).

### carryall_get_metadata

Get document metadata from a SLOS vault (requires envelope with read scope).

### carryall_audit_log

Query the audit trail of all agent access decisions (requires envelope with audit:read scope).

## Scope Format

Scopes follow `namespace:resource:action`:
- `vault:finance:read` - Read finance vault
- `vault:hr:write` - Write HR vault
- `vault:shared:read` - Read shared vault
- `vault:*:read` - Read all vaults
- `audit:read` - Query audit logs

## Workflow: Least-Privilege Agent Access

1. Call `carryall_compile_policy` with the agent's intent and available scopes
2. Receive a signed envelope with only the minimal scopes needed
3. Use the envelope with `carryall_check_access` before accessing resources
4. Use `carryall_get_metadata` to access vault documents
5. All actions are audit-logged automatically via `carryall_audit_log`
