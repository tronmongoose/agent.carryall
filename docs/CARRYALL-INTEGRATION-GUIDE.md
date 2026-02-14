# Carryall Integration Guide for SLOS + Clawdbot

> **Audience**: The Agent Carryall project team.
> **Purpose**: Everything Carryall needs to connect to SLOS and serve agents via Clawdbot.
> **Last updated**: 2026-02-13

---

## 1. Architecture

```
User (chat / IDE)
  |
  v
Clawdbot (AI agent orchestrator, ws://localhost:18789)
  |
  |-- HTTP plugin (localhost:8765)
  v
Carryall MCP Server (Python, HTTP transport)
  |
  |-- Ed25519 request signing
  |-- Envelope enforcement (scopes, TTL, audit)
  |-- spawns SLOS as subprocess, stdio JSON-RPC 2.0
  v
SLOS MCP Server (Rust binary, stdin/stdout)
  |
  |-- Ed25519 signature verification
  |-- Document-level policy metadata
  v
SLOS Vaults (local filesystem, Markdown + YAML frontmatter)
```

**Two access paths exist**:

| Path | Auth Model | Use Case |
|------|-----------|----------|
| Claude Code → SLOS (via `.mcp.json`) | No `_auth`, defaults to `claude-code` agent | Human-supervised interactive sessions |
| Clawdbot → Carryall → SLOS | Ed25519-signed `_auth` header per request | Autonomous agent operations |

**Trust boundary**: Carryall owns authorization (envelopes, scopes, policy evaluation). SLOS owns identity verification and data (vaults, documents, access policy metadata). SLOS verifies *who* the agent is; Carryall decides *what* the agent can do.

---

## 2. SLOS MCP Server

### 2.1 Binary Location

```
/Users/erikh/Desktop/sovereign-life-os/runtime/target/release/sovereign-life-os-runtime
```

Invoked with the `mcp` subcommand:
```bash
./sovereign-life-os-runtime mcp
```

This starts a stdio JSON-RPC 2.0 server reading NDJSON from stdin and writing responses to stdout.

### 2.2 Configuration File

SLOS provides `carryall-integration.json` at the project root:

```json
{
  "backend_name": "sovereign-life-os",
  "backend_version": "0.1.0",
  "mcp_transport": "stdio",
  "mcp_command": "./runtime/target/release/sovereign-life-os-runtime",
  "mcp_args": ["mcp"],
  "mcp_env": {},
  "mcp_cwd": ".",
  "protocol": {
    "version": "jsonrpc-2.0",
    "framing": "ndjson",
    "tool_call_method": "tools/call",
    "tool_list_method": "tools/list"
  },
  "available_tools": [
    "list_vaults", "list_vault", "get_metadata", "read_document",
    "write_document", "query_documents", "resolve_uri", "request_cross_domain"
  ]
}
```

**Key fields for `SlosBackend.__init__`**:
- `mcp_command`: The binary path (string or list)
- `mcp_args`: Arguments to append (e.g., `["mcp"]`)
- Combined into: `[mcp_command] + mcp_args`

### 2.3 Available Tools

| Tool | Purpose | Required Arguments |
|------|---------|-------------------|
| `list_vaults` | List all vault names | *(none)* |
| `list_vault` | List documents in a vault | `vault` |
| `get_metadata` | Document metadata + access policies (no content) | `uri` |
| `read_document` | Read document content by UUID | `id`, `purpose` |
| `write_document` | Create/update a document | `domain`, `content`, `metadata` |
| `query_documents` | Search documents by text query | `domain`, `query` |
| `resolve_uri` | Read document content by `slos://` URI | `uri`, `purpose` |
| `request_cross_domain` | Request cross-domain access (queues for approval) | `target_domain`, `purpose`, `fields_needed` |

---

## 3. JSON-RPC Protocol

### 3.1 Request Format

All tool calls use `method: "tools/call"` with the tool name in `params.name`:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "get_metadata",
    "arguments": {
      "uri": "slos://vaults/finance/budgets/q4-2025.md",
      "_auth": {
        "agent_id": "finance-agent",
        "timestamp": "2026-02-13T10:00:00Z",
        "signature": "<base64-encoded Ed25519 signature>"
      }
    }
  }
}
```

**Do NOT** use `method: "tools/get_metadata"` or put the tool name in the method field. The MCP protocol requires `method: "tools/call"` with a separate `name` param.

### 3.2 Response Format (MCP Content Envelope)

SLOS wraps all tool responses in the MCP content envelope format:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"vaults\":[\"finance\",\"startup\",\"health\",\"family\",\"personal\",\"meta\"]}"
      }
    ],
    "isError": false
  }
}
```

**Unwrapping**: The actual response data is JSON-encoded inside `result.content[0].text`. Carryall must:
1. Check `result.content` is a list
2. Find the first item with `type: "text"`
3. Parse `item["text"]` as JSON

### 3.3 Response Schemas

**`list_vaults`**:
```json
{"vaults": ["finance", "startup", "health", "family", "personal", "meta"]}
```

**`list_vault`**:
```json
{
  "vault": "finance",
  "documents": [
    {
      "id": "019bf091-...",
      "title": "Q4 Finance Report",
      "uri": "slos://vaults/finance/budgets/q4-2025.md",
      "domain": ["finance"],
      "sensitivity": "confidential",
      "allowed_agents": ["finance-agent", "executive-agent"],
      "denied_agents": ["startup-agent"],
      "requires_approval": []
    }
  ],
  "total": 1
}
```

**`get_metadata`** (critical for policy evaluation):
```json
{
  "uri": "slos://vaults/finance/budgets/q4-2025.md",
  "id": "019bf091-...",
  "domain": ["finance"],
  "sensitivity": "confidential",
  "allowed_agents": ["finance-agent", "executive-agent"],
  "denied_agents": ["startup-agent"],
  "requires_approval": [],
  "data_type": "note",
  "tags": ["budget", "q4"],
  "audit_level": "full"
}
```

**`read_document`**:
```json
{
  "uri": "slos://vaults/finance/budgets/q4-2025.md",
  "id": "019bf091-...",
  "content": "# Q4 Finance Report\n\nContent here...",
  "content_type": "text/markdown",
  "domain": ["finance"],
  "sensitivity": "confidential"
}
```

**Error responses** (inside the content envelope):
```json
{
  "success": false,
  "error": {
    "code": "ACCESS_DENIED",
    "message": "Agent startup-agent cannot access domain: health"
  },
  "audit_id": "019bfc1a-..."
}
```

### 3.4 Authentication Rejection

SLOS now distinguishes between:

| Scenario | Behavior |
|----------|----------|
| No `_auth` present | Defaults to `claude-code` agent (human-supervised mode) |
| `_auth` present, valid signature | Proceeds as the authenticated agent |
| `_auth` present, invalid/malformed | **Rejects the request** with `AUTH_FAILED` error |

If Carryall sends a request with a bad signature (wrong key, stale timestamp, malformed), SLOS will respond with:

```json
{
  "content": [{
    "type": "text",
    "text": "{\"success\":false,\"error\":{\"code\":\"AUTH_FAILED\",\"message\":\"Authentication failed: Signature verification failed\"}}"
  }],
  "isError": true
}
```

Carryall should treat `isError: true` with `AUTH_FAILED` as a hard failure - do not retry without fixing the root cause.

---

## 4. Ed25519 Request Signing

### 4.1 Algorithm

1. Gather the arguments (excluding `_auth`)
2. Create canonical JSON: `json.dumps(args, sort_keys=True, separators=(",", ":"))`
3. Construct message: `f"{agent_id}{timestamp}{canonical_json}"` encoded as UTF-8
4. Sign with the agent's Ed25519 private key
5. Base64-encode the 64-byte signature
6. Include as `_auth` field in arguments

### 4.2 Canonical JSON

**Critical**: SLOS (Rust) and Carryall (Python) must produce byte-identical canonical JSON.

Python reference:
```python
json.dumps(args_without_auth, sort_keys=True, separators=(",", ":"))
```

This means:
- Object keys sorted alphabetically at every nesting level
- No whitespace around `:` or `,`
- No trailing commas
- Strings properly escaped

SLOS has a `canonical_json()` function in Rust that matches this exactly, verified by unit tests.

### 4.3 Timestamp Format

Use ISO-8601 UTC:
```python
datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
```

SLOS rejects timestamps more than **5 minutes** old (replay protection).

### 4.4 Key Management

Agent private keys are stored in `~/.carryall/keys/{agent_id}.key` (64-byte Ed25519 signing keys, raw bytes).

The corresponding public keys are registered in SLOS's `config/agents.yaml`:

```yaml
agents:
  finance-agent:
    public_key: "rho+4bS68ClDXO7YokLG/4cCbKjTITd7XDBzLpA4IqA="
  executive-agent:
    public_key: "WRbvQAbK/ri+gUSzSHOMqWdaLJPMewsvOIKunOKzdJw="
  startup-agent:
    public_key: "9+r5TjdfKAp65VYHYZ1UmrxUrBcND73mvFah9pish24="
  health-agent:
    public_key: "QACPq2Gh4Dwz1I07S2sKuugrT2IE7JsLss/5HgQeybg="
  personal-agent:
    public_key: "vAeLCvcBLz4E+rkl4BtcNE1Gtjn2dpLcMan4o95sVdQ="
```

**Key exchange workflow**:
1. `carryall keys generate {agent-id}` → creates private key, outputs base64 public key
2. Add public key to SLOS's `config/agents.yaml`
3. Private key never leaves the Carryall host

**Current keys are synced** as of 2026-02-13. If you regenerate any key, you must update both sides.

---

## 5. Policy Evaluation

### 5.1 Document-Level Policies

SLOS documents contain access policies in YAML frontmatter:

```yaml
---
id: "019bf091-..."
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

Carryall retrieves these via `get_metadata` and evaluates in priority order:

1. **Explicit deny** (`denied_agents`) → always wins
2. **Requires approval** (`requires_approval`) → blocks until human approves
3. **Explicit allow** (`allowed_agents`) → grants access
4. **Scope-based allow** → envelope has `vault:{vault}:{action}` scope
5. **Default deny** → no match = no access

### 5.2 Scope Format

Carryall scopes relevant to SLOS follow `vault:{vault}:{action}`:

| Scope | Meaning |
|-------|---------|
| `vault:finance:read` | Read docs in finance vault |
| `vault:health:write` | Write docs in health vault |
| `vault:*:read` | Read all vaults (wildcard) |
| `vault:*:*` | Full vault access |

SLOS does NOT evaluate scopes. SLOS returns the metadata; Carryall evaluates it.

---

## 6. Carryall Code: Critical Implementation Details

### 6.1 SlosBackend (`backends/slos.py`)

The three fixes applied to make real SLOS calls work:

**Fix 1 - `__init__` reads `mcp_args` from config**:
```python
cmd = self.config.get("mcp_command", "slos-mcp")
args = self.config.get("mcp_args", [])
self.mcp_command = ([cmd] if isinstance(cmd, str) else cmd) + args
```

**Fix 2 - `_call_mcp()` uses correct MCP method format**:
```python
request = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
        "name": method,        # e.g., "get_metadata"
        "arguments": signed_params,
    },
}
```

**Fix 3 - Response unwrapping for MCP content envelope**:
```python
raw_result = response.get("result", {})
if "content" in raw_result and isinstance(raw_result["content"], list):
    for item in raw_result["content"]:
        if item.get("type") == "text":
            return json.loads(item["text"])
return raw_result
```

### 6.2 MCP Server (`mcp_server.py`)

All 4 tool handler methods had `mock=True` hardcoded. These were changed to `mock=False` to use real SLOS calls.

---

## 7. Clawdbot Integration

### 7.1 Plugin Configuration

Clawdbot connects to Carryall via HTTP plugin. Configuration lives in `~/.clawdbot/clawdbot.json`:

```json
{
  "plugins": {
    "load": {
      "paths": ["/Users/erikh/code/carryall/extensions/carryall"]
    },
    "entries": {
      "carryall": {
        "enabled": true,
        "config": {
          "carryallUrl": "http://localhost:8765"
        }
      }
    }
  }
}
```

The plugin source at `extensions/carryall/index.ts` is a thin HTTP client that forwards Clawdbot tool calls to Carryall's MCP server.

### 7.2 Startup Sequence

```bash
# Terminal 1: Start Carryall (which spawns SLOS as needed)
cd /Users/erikh/code/carryall/authority-runtime-python
PYTHONPATH=src python -m authority_runtime.mcp_server --transport http --port 8765

# Terminal 2: Start Clawdbot
cd ~/.clawdbot
clawdbot agent --local

# Terminal 3 (optional): Direct SLOS access via Claude Code
# Already configured via .mcp.json - no manual startup needed
```

---

## 8. Testing

### 8.1 SLOS Standalone Smoke Test

```bash
cd /Users/erikh/Desktop/sovereign-life-os

# List vaults (no auth - defaults to claude-code)
echo '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_vaults","arguments":{}}}' \
  | ./runtime/target/release/sovereign-life-os-runtime mcp 2>/dev/null
```

### 8.2 Signed Request Test

Generate a signed request using Carryall's keys:

```python
#!/usr/bin/env python3
"""Generate a signed SLOS request for testing."""
import json, base64, sys
from datetime import datetime, timezone
from pathlib import Path
import nacl.signing

agent_id = "finance-agent"
key_path = Path.home() / ".carryall" / "keys" / f"{agent_id}.key"
signing_key = nacl.signing.SigningKey(key_path.read_bytes())

arguments = {"vault": "finance"}
timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
args_json = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
message = f"{agent_id}{timestamp}{args_json}".encode()
signature = signing_key.sign(message).signature

arguments["_auth"] = {
    "agent_id": agent_id,
    "timestamp": timestamp,
    "signature": base64.b64encode(signature).decode(),
}

request = {
    "jsonrpc": "2.0", "id": 1,
    "method": "tools/call",
    "params": {"name": "list_vault", "arguments": arguments}
}

print(json.dumps(request))
```

Pipe to SLOS:
```bash
python3 sign_request.py | ./runtime/target/release/sovereign-life-os-runtime mcp 2>/dev/null
```

### 8.3 Invalid Signature Test

Send a request with a bad signature to verify SLOS rejects it:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_vaults","arguments":{"_auth":{"agent_id":"finance-agent","timestamp":"2026-02-13T00:00:00Z","signature":"dGhpcyBpcyBub3QgYSB2YWxpZCBzaWduYXR1cmU="}}}}' \
  | ./runtime/target/release/sovereign-life-os-runtime mcp 2>/dev/null
```

Expected: `AUTH_FAILED` error (not fallback to `claude-code`).

### 8.4 End-to-End via Carryall

With Carryall running on port 8765:
```bash
curl -X POST http://localhost:8765/tools/carryall_list_vaults \
  -H "Content-Type: application/json" \
  -d '{"envelope": "<valid-envelope-json>"}'
```

---

## 9. Troubleshooting

### "Agent key not found"
- Check `~/.carryall/keys/{agent_id}.key` exists
- Generate with: `carryall keys generate {agent_id}`

### "Authentication failed: Signature verification failed"
- Keys may be out of sync between Carryall and SLOS
- Read SLOS's expected key: check `config/agents.yaml` in SLOS
- Read Carryall's actual key: `python3 -c "import nacl.signing; k = nacl.signing.SigningKey(open('~/.carryall/keys/{agent_id}.key', 'rb').read()); print(base64.b64encode(k.verify_key.encode()).decode())"`
- If they differ, update SLOS's `config/agents.yaml` with Carryall's public key

### "Authentication failed: Request timestamp too old"
- Timestamps must be within 5 minutes of server time
- Check clock sync between Carryall host and SLOS host (usually same machine)

### "MCP call failed" / "Invalid MCP response"
- Ensure the SLOS binary is built: `cd sovereign-life-os/runtime && cargo build --release`
- Ensure `carryall-integration.json` has correct `mcp_command` and `mcp_args`
- Test SLOS directly with a raw stdin request (see Section 8.1)

### Carryall returns mock data instead of real data
- Check that `mock=False` in all tool handlers in `mcp_server.py`
- Previously was `mock=True` in 4 locations (lines ~334, ~374, ~405, ~443)

### SLOS returns data for invalid agents
- Prior to the latest fix, SLOS would fall back to `claude-code` for any failed auth
- Now: if `_auth` is present but invalid, SLOS rejects with `AUTH_FAILED`
- If `_auth` is absent, `claude-code` is used (by design for direct access)

---

## 10. Files Reference

### SLOS Side

| File | Purpose |
|------|---------|
| `runtime/src/mcp_server.rs` | MCP server (stdio + daemon), JSON-RPC routing, canonical JSON, auth extraction |
| `runtime/src/auth.rs` | Ed25519 signature verification, timestamp freshness, agent registry |
| `runtime/src/request_handler.rs` | Tool implementations (list_vaults, read_document, get_metadata, etc.) |
| `runtime/src/document.rs` | Document parsing (YAML frontmatter), vault traversal, URI resolution |
| `runtime/src/policy_engine.rs` | OPA/Regorus policy evaluation for agent capabilities |
| `config/agents.yaml` | Agent definitions with Ed25519 public keys |
| `carryall-integration.json` | Carryall discovery file (binary path, protocol, keys) |
| `.mcp.json` | Claude Code direct-access config (no Carryall needed) |

### Carryall Side

| File | Purpose |
|------|---------|
| `authority-runtime-python/src/authority_runtime/backends/slos.py` | SLOS backend adapter (signing, MCP calls, policy evaluation) |
| `authority-runtime-python/src/authority_runtime/mcp_server.py` | Carryall MCP server (HTTP, wraps SLOS backend with envelope enforcement) |
| `authority-runtime-python/src/authority_runtime/keys.py` | Agent key management (generate, load, store) |
| `authority-runtime-python/src/authority_runtime/envelope.py` | Envelope creation, signing, verification, narrowing |
| `extensions/carryall/index.ts` | Clawdbot plugin (thin HTTP client to Carryall) |

### Clawdbot Side

| File | Purpose |
|------|---------|
| `~/.clawdbot/clawdbot.json` | Gateway config with Carryall plugin entry |

---

## 11. Checklist

- [x] `SlosBackend.__init__` reads `mcp_command` + `mcp_args` from `carryall-integration.json`
- [x] `_call_mcp()` uses `method: "tools/call"` with `name` in params
- [x] `_call_mcp()` unwraps MCP content envelope responses
- [x] `mcp_server.py` uses `mock=False` for all real tool calls
- [x] Agent Ed25519 keys synced between `~/.carryall/keys/` and SLOS `config/agents.yaml`
- [x] Canonical JSON matches between Python and Rust (sorted keys, compact separators)
- [x] SLOS binary builds (`cargo build --release`) and all 41 tests pass
- [x] SLOS rejects requests with invalid `_auth` (no fallback to `claude-code`)
- [x] SLOS response includes `uri`, `title`, `content_type` fields per integration spec
- [x] Clawdbot has Carryall plugin configured at `http://localhost:8765`
- [ ] End-to-end test: Clawdbot → Carryall → SLOS (requires all three running)
- [ ] Approval workflow CLI for `request_cross_domain` results
