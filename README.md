# Carryall

**IAM for AI agents.** Least-privilege authorization, cryptographic audit trails, and policy-governed vault access — so your agents can act autonomously without acting unsafely.

## The Problem

AI agents need access to sensitive data — financial records, health information, customer databases, internal documents. Today, most agent systems solve this with API keys and trust: give the agent a token, hope it behaves.

That doesn't scale. When agents cross domain boundaries, invoke tools autonomously, or act on untrusted input, you need:

- **Least-privilege scoping** — agents get only the permissions they need, for only as long as they need them
- **Cryptographic proof** — every action is signed, time-limited, and tamper-evident
- **Policy compilation** — natural language intent translated into minimal-scope authorization envelopes via LLM
- **Adversarial detection** — automatic scoring of threat patterns (the "lethal trifecta": finance operation + untrusted content + context injection)

Carryall is the control plane that provides all of this.

## How It Works

```
Agent Intent: "Read Q4 budget for planning"
         ↓
    compile_policy (LLM)
         ↓
    Signed Envelope: vault:finance:read, TTL 300s, Ed25519
         ↓
    OPA Policy Check → ALLOW / DENY / REQUIRE_APPROVAL
         ↓
    Vault Access (scoped, audited, hash-chained)
```

1. Agent declares intent in plain English
2. `compile_policy` uses an LLM to select the minimal scopes needed
3. A signed, time-limited envelope is issued
4. Every action is checked against OPA policies, audited with SHA-256 hash chaining, and scored by Sentinel for adversarial patterns

## Architecture

```
Channel Transport (Telegram, OpenClaw, Claude Code)
         |
    Mayor / ClawRouter         ← routes queries by complexity (local vs frontier)
         |
    Carryall Authorization     ← envelope signing, policy compilation, scope enforcement
         |
    SLOS Data Plane            ← vaults, documents, hash-chained audit trail
```

Carryall is not a channel gateway. It sits between the transport layer and the data plane. It requires a channel transport upstream and a deployment config repo downstream.

## Components

| Component | Purpose |
|-----------|---------|
| **authority-runtime-python** | Core IAM library — Ed25519 signing, MCP server, envelope system, OPA policy engine |
| **mayor** | Executive routing engine — complexity scoring, local vs frontier LLM selection |
| **context** | DAG-backed context persistence — ingestion, compaction, embeddings, vault-scoped ACLs |
| **sentinel** | Adversarial scoring — trifecta detection, spend velocity, cross-domain leak scoring |
| **agents/argus** | Security scanner — data locality checks, credential exposure detection |
| **policies** | OPA Rego templates for 7 vault domains |
| **schemas** | Vault document metadata schema |
| **lib** | Shared utilities — notification routing, pipeline verification |

## Quick Start

```bash
# Install the core library
pip install ./authority-runtime-python/

# Generate agent keys
python -m authority_runtime.cli keys generate finance-agent

# Start MCP server (for Claude Code / stdio integration)
python -m authority_runtime.cli mcp serve

# Start HTTP server (for Telegram gateway, external tools)
python -m authority_runtime.cli mcp serve --transport http --port 8765
```

## MCP Tools

Carryall exposes 8 tools via [Model Context Protocol](https://modelcontextprotocol.io):

| Tool | Purpose |
|------|---------|
| `compile_policy` | Translate intent → minimal-scope signed envelope |
| `check_access` | Check if an envelope permits a specific action |
| `list_vaults` | List available data vaults |
| `get_metadata` | Get document metadata and access policies |
| `read_document` | Read document content (scoped, audited) |
| `write_document` | Write document to vault (scoped, audited) |
| `query_documents` | Search within a vault domain |
| `audit_log` | Query the tamper-evident audit trail |

## Security Model

### Envelope System
Every agent action requires a signed **AuthorityEnvelope**:
- Ed25519 signature from agent keypair
- Time-limited TTL (default 300 seconds)
- Minimal scopes selected by LLM policy compiler
- Hash-chained audit trail (SHA-256, gap detection, tamper-evident)

### Sentinel Scoring
Adversarial scoring engine detects threat patterns:

| Pattern | Score | Action |
|---------|-------|--------|
| Trifecta contamination (finance + untrusted content) | +80 | BLOCK |
| Invalid envelope signature | +50 | BLOCK |
| ACL violation (cross-domain access) | +40 | FLAG |
| Score >= 70 | — | Automatic BLOCK |
| Score 40-69 | — | REQUIRE_APPROVAL |

### Quality Gates
Pipeline stages can require human approval before proceeding — Telegram inline keyboard with approve/deny, auto-expire, idempotent restart, full audit trail.

## Deployment

See [carryall-onboarding](https://github.com/tronmongoose/carryall-onboarding) for the customer deployment runbook.

## Testing

```bash
cd authority-runtime-python && python -m pytest  # 178 tests
```

## Versioning

Semantic versioning. See `VERSION` and `CHANGELOG.md`.

## License

Business Source License 1.1. See `LICENSE`.
