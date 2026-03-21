# SLOS Integration Guide for Agent Carryall + Clawdbot

> **Audience**: The SLOS (Sovereign Life OS) project team.
> **Purpose**: Everything SLOS needs to build a working integration with Agent Carryall and Clawdbot.
> **Last updated**: 2026-02-13

---

## Context

Carryall is the cryptographic permission enforcement layer. Clawdbot is the AI agent orchestrator. SLOS is the data backend that holds vaults and documents.

Today Carryall talks to SLOS via a `SlosBackend` adapter that calls SLOS's MCP server over stdio (JSON-RPC 2.0). The adapter already handles Ed25519 request signing. SLOS needs to implement the server side: verify signatures, serve vault/document data, and return document-level access policies.

---

## 1. Architecture Overview

```
User
  |
  v
Clawdbot (AI agent orchestrator)
  |
  |-- calls native tools via Carryall plugin (HTTP)
  v
Carryall MCP Server (Python, HTTP on port 8765)
  |
  |-- envelope signing, policy compilation, audit logging
  |-- calls SLOS MCP server (stdio, JSON-RPC 2.0)
  v
SLOS MCP Server (your code - stdio binary)
  |
  |-- signature verification, vault access, document metadata
  v
SLOS Vaults (local filesystem / database)
```

**Key boundary**: Carryall owns authorization logic (envelopes, scopes, signatures, audit). SLOS owns data (vaults, documents, metadata, document-level policies). SLOS verifies agent identity via Ed25519 signatures but does NOT need to understand envelopes.

---

## 2. What SLOS Needs to Build

### 2.1 MCP Server (stdio, JSON-RPC 2.0)

SLOS must provide an MCP-compatible server binary that Carryall spawns as a subprocess. Communication is over stdin/stdout using newline-delimited JSON-RPC 2.0.

**Binary name**: `slos-mcp` (or configurable via `carryall-integration.json`)

**Required MCP methods** (called via `tools/call`):

| Tool Name | Purpose | Required Fields |
|-----------|---------|-----------------|
| `list_vaults` | Return available vault names | (none beyond `_auth`) |
| `list_vault` | List documents in a vault | `vault` |
| `get_metadata` | Return document metadata + access policies | `uri` |
| `read_document` | Return document content | `uri` |

**Request format** (what SLOS receives on stdin):

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "get_metadata",
    "arguments": {
      "uri": "slos://vaults/finance/doc-001",
      "_auth": {
        "agent_id": "finance-agent",
        "timestamp": "2026-02-13T10:00:00Z",
        "signature": "<base64-encoded Ed25519 signature>"
      }
    }
  }
}
```

**Response format** (what SLOS writes to stdout):

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"uri\":\"slos://vaults/finance/doc-001\",\"id\":\"doc-001\",\"domain\":[\"finance\"],\"sensitivity\":\"confidential\",\"allowed_agents\":[\"finance-agent\",\"executive-agent\"],\"denied_agents\":[\"startup-agent\"],\"requires_approval\":[]}"
      }
    ]
  }
}
```

### 2.2 Response Schemas for Each Tool

**`list_vaults`** response:
```json
{
  "vaults": ["finance", "startup", "health", "personal", "shared"]
}
```

**`list_vault`** response:
```json
{
  "vault": "finance",
  "documents": [
    {"id": "doc-001", "title": "Q4 Finance Report"},
    {"id": "doc-002", "title": "Budget Projections"}
  ]
}
```

**`get_metadata`** response (critical - includes access policies):
```json
{
  "uri": "slos://vaults/finance/doc-001",
  "id": "doc-001",
  "domain": ["finance"],
  "sensitivity": "confidential",
  "allowed_agents": ["finance-agent", "executive-agent"],
  "denied_agents": ["startup-agent"],
  "requires_approval": ["intern-agent"]
}
```

**`read_document`** response:
```json
{
  "uri": "slos://vaults/finance/doc-001",
  "content": "...",
  "content_type": "text/markdown"
}
```

### 2.3 Ed25519 Signature Verification

Every request from Carryall includes an `_auth` field. SLOS must verify it.

**Signature algorithm**:
1. Extract `agent_id`, `timestamp`, and `signature` from `_auth`
2. Remove `_auth` from the arguments
3. Create canonical JSON of remaining arguments: `json.dumps(args, sort_keys=True, separators=(",", ":"))`
4. Construct message: `f"{agent_id}{timestamp}{args_json}"` encoded as UTF-8
5. Verify the base64-decoded signature against the message using the agent's Ed25519 public key
6. Reject if timestamp is more than 5 minutes old (replay protection)

**Python reference implementation**:

```python
import json
import base64
import nacl.signing
from datetime import datetime, timezone, timedelta

def verify_request(arguments: dict, known_public_keys: dict) -> bool:
    auth = arguments.get("_auth")
    if not auth:
        return False

    agent_id = auth["agent_id"]
    timestamp = auth["timestamp"]
    signature_b64 = auth["signature"]

    # Check timestamp freshness (5-minute window)
    request_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    if abs((now - request_time).total_seconds()) > 300:
        return False  # Stale request

    # Look up agent's public key
    public_key_b64 = known_public_keys.get(agent_id)
    if not public_key_b64:
        return False  # Unknown agent

    # Reconstruct signed message
    args_without_auth = {k: v for k, v in arguments.items() if k != "_auth"}
    args_json = json.dumps(args_without_auth, sort_keys=True, separators=(",", ":"))
    message = f"{agent_id}{timestamp}{args_json}".encode("utf-8")

    # Verify signature
    try:
        public_key_bytes = base64.b64decode(public_key_b64)
        verify_key = nacl.signing.VerifyKey(public_key_bytes)
        signature_bytes = base64.b64decode(signature_b64)
        verify_key.verify(message, signature_bytes)
        return True
    except Exception:
        return False
```

**Node.js/TypeScript reference**:

```typescript
import { verify } from "@noble/ed25519";

async function verifyRequest(
  args: Record<string, unknown>,
  knownPublicKeys: Record<string, string>  // agent_id -> base64 public key
): Promise<boolean> {
  const auth = args._auth as {
    agent_id: string;
    timestamp: string;
    signature: string;
  };
  if (!auth) return false;

  // Check timestamp freshness
  const requestTime = new Date(auth.timestamp).getTime();
  const now = Date.now();
  if (Math.abs(now - requestTime) > 300_000) return false;

  // Look up public key
  const pubKeyB64 = knownPublicKeys[auth.agent_id];
  if (!pubKeyB64) return false;

  // Reconstruct signed message
  const { _auth, ...argsWithoutAuth } = args;
  const sortedKeys = Object.keys(argsWithoutAuth).sort();
  const argsJson = JSON.stringify(
    argsWithoutAuth,
    sortedKeys
  );
  // NOTE: Ensure output matches Python's (",", ":") separators - no spaces
  const message = new TextEncoder().encode(
    `${auth.agent_id}${auth.timestamp}${argsJson}`
  );

  // Verify
  const pubKey = Uint8Array.from(atob(pubKeyB64), (c) => c.charCodeAt(0));
  const sig = Uint8Array.from(atob(auth.signature), (c) => c.charCodeAt(0));
  return verify(sig, message, pubKey);
}
```

**IMPORTANT: Canonical JSON must match exactly.** Carryall uses Python's `json.dumps(data, sort_keys=True, separators=(",", ":"))`. If SLOS is written in a different language, ensure the JSON serialization produces identical output (sorted keys, no whitespace, no trailing commas).

### 2.4 Agent Key Registration

When Carryall generates a keypair for an agent, it stores the private key locally in `~/.carryall/keys/{agent_id}.key` and outputs the base64-encoded public key.

SLOS must maintain a registry of known agent public keys. Recommended: a config file like `config/agents.yaml`:

```yaml
# config/agents.yaml - Known agent public keys
agents:
  finance-agent: "base64-encoded-32-byte-Ed25519-public-key"
  executive-agent: "another-base64-public-key"
  health-agent: "another-base64-public-key"
```

**Key exchange workflow**:
1. Carryall operator runs: `carryall keys generate finance-agent`
2. Carryall outputs the base64 public key
3. Operator adds the public key to SLOS's `config/agents.yaml`
4. No private keys ever leave the Carryall host

### 2.5 Document-Level Access Policies

SLOS documents should include access policy metadata (e.g., in YAML frontmatter):

```yaml
---
id: doc-001
domain: [finance]
sensitivity: confidential
allowed_agents:
  - finance-agent
  - executive-agent
denied_agents:
  - startup-agent
requires_approval:
  - intern-agent
---
```

Carryall uses these policies in a priority chain:
1. **Explicit deny** (`denied_agents`) - always wins
2. **Requires approval** (`requires_approval`) - blocks until human approves
3. **Explicit allow** (`allowed_agents`) - grants access
4. **Scope-based allow** - if envelope has matching `vault:{vault}:{action}` scope
5. **Default deny** - no match = no access

SLOS just returns this metadata; Carryall handles the policy evaluation logic.

### 2.6 SLOS URI Format

All resource references use the format: `slos://vaults/{vault_name}/{document_id}`

Examples:
- `slos://vaults/finance/doc-001`
- `slos://vaults/health/019bf091-medical-records`
- `slos://vaults/shared/team-notes`

SLOS must parse these URIs and resolve them to actual documents.

---

## 3. Configuration File

Carryall looks for a `carryall-integration.json` file that tells it how to reach SLOS:

```json
{
  "mcp_command": "slos-mcp",
  "mcp_args": ["--config", "/path/to/slos/config"],
  "description": "SLOS MCP server for vault access"
}
```

Alternatively, `mcp_command` can be a list:

```json
{
  "mcp_command": ["node", "dist/mcp-server.js"],
  "mcp_args": ["--vaults-dir", "/data/vaults"]
}
```

Carryall spawns this as a subprocess and communicates over stdio.

---

## 4. Security and Privacy Concerns

### 4.1 What Carryall Sends to SLOS

- **Agent ID**: identifies which agent is making the request
- **Timestamp**: for replay protection
- **Ed25519 signature**: proves the request came from Carryall (not spoofed)
- **Tool arguments**: the specific data requested (URI, vault name, etc.)

Carryall does NOT send:
- LLM API keys
- Envelope internals (signatures, scopes, policy IDs)
- Audit log data
- User prompts or conversation history

### 4.2 What SLOS Should NOT Do

- **Do not log document content to external services.** All logging should be local.
- **Do not cache agent signatures.** Each request has a unique timestamp; verify fresh each time.
- **Do not trust the agent_id without verifying the signature.** The `_auth.agent_id` field is only trustworthy if the signature verifies against that agent's registered public key.
- **Do not return documents without verifying `_auth`.** Every request must be authenticated.
- **Do not expose the MCP server on a network port.** It runs as a subprocess communicating over stdio. Network exposure would bypass Carryall's envelope enforcement.

### 4.3 What SLOS Should Do

- **Verify every request signature** using the reference implementation above
- **Reject stale requests** (timestamp > 5 minutes old)
- **Log access locally** for SLOS's own audit trail (who accessed what, when)
- **Return accurate `allowed_agents`/`denied_agents`/`requires_approval`** metadata - Carryall depends on this for policy decisions
- **Treat sensitivity levels seriously**: `confidential` and `restricted` documents should have explicit `allowed_agents` lists, not open access

### 4.4 Trust Boundary Summary

```
SLOS trusts:
  - Carryall's Ed25519 signatures (verifiable)
  - Agent IDs are genuine (proven by signature)

SLOS does NOT trust:
  - That the agent should have access (SLOS returns policies, Carryall decides)
  - Any network requests (stdio only)
  - Any unsigned requests

Carryall trusts:
  - SLOS returns accurate metadata and policies
  - SLOS returns correct document content
  - SLOS handles its own data securely

Carryall does NOT trust:
  - That agents should have blanket access (enforced via envelopes)
  - That LLM outputs are safe (scopes narrowed, never expanded)
```

---

## 5. Scope System

Carryall scopes follow `namespace:resource:action` format. The scopes relevant to SLOS:

| Scope | Meaning |
|-------|---------|
| `vault:finance:read` | Read documents in finance vault |
| `vault:finance:write` | Write documents in finance vault |
| `vault:health:read` | Read documents in health vault |
| `vault:*:read` | Read all vaults (wildcard) |
| `vault:*:*` | Full access to all vaults |
| `audit:read` | Query audit logs |

SLOS does not need to evaluate scopes. Carryall handles scope matching against envelopes. SLOS only needs to return document metadata so Carryall can check `allowed_agents`/`denied_agents`.

---

## 6. Deployment Architectures

### 6.1 Local Development

```
Terminal 1: carryall mcp serve --transport http --port 8765
Terminal 2: (SLOS runs as subprocess, spawned by Carryall when needed)
Terminal 3: clawdbot agent --local
```

SLOS binary (`slos-mcp`) must be on the PATH or specified in `carryall-integration.json`.

### 6.2 Kubernetes (Helm)

```yaml
# Pod: clawdbot + carryall sidecar
containers:
  - name: clawdbot
    image: clawdbot-carryall:latest
    # Clawdbot gateway + carryall plugin
  - name: carryall
    image: carryall:latest
    # Carryall MCP server (HTTP mode)
    # SLOS binary bundled inside or as initContainer
```

In K8s, Carryall runs as HTTP (not stdio) and the Clawdbot plugin calls it over `localhost:8765`. SLOS can either:
- Be bundled into the Carryall container (simplest)
- Run as a separate sidecar in the same pod
- Run as a separate service (requires `carryall-integration.json` to point to a network command)

### 6.3 Docker Compose

```yaml
services:
  clawdbot:
    image: clawdbot-carryall:latest
    ports: ["18789:18789"]
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
    volumes:
      - ./slos-data:/data/vaults
  carryall:
    image: carryall:latest
    command: ["carryall", "mcp", "serve", "--transport", "http", "--port", "8765"]
```

---

## 7. Testing the Integration

### 7.1 Minimal Smoke Test

Once SLOS has its MCP server built:

```bash
# 1. Generate an agent key
carryall keys generate test-agent
# Outputs: Public key (base64): <key>

# 2. Add the public key to SLOS config/agents.yaml

# 3. Start carryall
carryall mcp serve --transport http --port 8765

# 4. Test via curl
curl -X POST http://localhost:8765/tools/carryall_list_vaults \
  -H "Content-Type: application/json" \
  -d '{"envelope": <valid-envelope-json>}'

# 5. Or test via Clawdbot
clawdbot agent --local --message \
  "Compile a policy for agent test-agent to read finance reports. \
   Available scopes: vault:finance:read, vault:hr:read. \
   Available resources: slos://vaults/finance/*, slos://vaults/hr/*"
```

### 7.2 Verifying Signature Flow

Test that SLOS correctly rejects bad signatures:

```python
# In SLOS test suite
def test_rejects_invalid_signature():
    request = {
        "uri": "slos://vaults/finance/doc-001",
        "_auth": {
            "agent_id": "test-agent",
            "timestamp": "2026-02-13T10:00:00Z",
            "signature": "dGhpcyBpcyBub3QgYSB2YWxpZCBzaWduYXR1cmU="  # invalid
        }
    }
    result = verify_request(request, known_keys)
    assert result is False

def test_rejects_stale_timestamp():
    # Sign correctly but use old timestamp
    request = sign_request(
        "test-agent",
        {"uri": "slos://vaults/finance/doc-001"}
    )
    request["_auth"]["timestamp"] = "2020-01-01T00:00:00Z"
    result = verify_request(request, known_keys)
    assert result is False

def test_accepts_valid_request():
    request = sign_request(
        "test-agent",
        {"uri": "slos://vaults/finance/doc-001"}
    )
    result = verify_request(request, known_keys)
    assert result is True
```

---

## 8. What SLOS Does NOT Need to Build

- **Envelope creation/signing** - Carryall handles this
- **LLM policy compilation** - Carryall handles this
- **Scope evaluation** - Carryall handles this
- **Audit logging** - Carryall maintains its own audit trail
- **Clawdbot plugin** - Already built (lives in `extensions/carryall/`)
- **Key generation** - Carryall generates keypairs; SLOS only stores public keys

---

## 9. Files in Agent Carryall Relevant to SLOS

| File | What It Does |
|------|-------------|
| `authority-runtime-python/src/authority_runtime/backends/slos.py` | The adapter that calls SLOS. Read this to understand exactly what Carryall sends. |
| `authority-runtime-python/src/authority_runtime/keys.py` | Key management. Shows how keypairs are generated and stored. |
| `authority-runtime-python/src/authority_runtime/mcp_server.py` | The Carryall MCP server. Shows all tool endpoints. |
| `authority-runtime-python/src/authority_runtime/types.py` | Core types (AuthorityEnvelope, Authority, Skill, etc.). |
| `authority-runtime-python/src/authority_runtime/envelope.py` | Envelope creation, signing, verification, narrowing. |
| `extensions/carryall/index.ts` | Clawdbot plugin - thin HTTP client to Carryall. |

---

## 10. Checklist for SLOS Implementation

- [ ] Build `slos-mcp` binary that reads JSON-RPC from stdin, writes to stdout
- [ ] Implement `list_vaults` tool
- [ ] Implement `list_vault` tool (documents in a vault)
- [ ] Implement `get_metadata` tool (returns access policy fields)
- [ ] Implement `read_document` tool
- [ ] Implement Ed25519 signature verification (match Python's canonical JSON exactly)
- [ ] Implement timestamp freshness check (5-minute window)
- [ ] Create `config/agents.yaml` for public key storage
- [ ] Add document-level access policies to document frontmatter/metadata
- [ ] Write tests for signature verification (valid, invalid, stale)
- [ ] Create `carryall-integration.json` for Carryall to discover SLOS
- [ ] Test end-to-end: generate key -> register in SLOS -> compile policy -> access document
