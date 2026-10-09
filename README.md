# Carryall

**Authorization for AI agents.** Least-privilege scopes, signed short-lived envelopes, and a tamper-evident audit trail. Agents can act on their own without acting unsafely.

Carryall is a personal project. It runs the authorization layer for my own agent swarm on one machine. Feedback is welcome.

## The problem

Agents need access to sensitive data: financial records, health information, internal documents. Most agent systems handle this with an API key and trust. Give the agent a token and hope it behaves.

That breaks down once agents cross domain boundaries, call tools on their own, or act on untrusted input. You need:

- **Least privilege.** An agent gets only the scopes it needs, only for as long as it needs them.
- **Cryptographic proof.** Every action is signed, time-limited and tamper-evident.
- **Policy compilation.** A plain-language intent becomes a minimal-scope envelope.
- **Adversarial detection.** Threat patterns are scored, such as a finance operation that touches untrusted content.

## How it works

```
Agent intent: "Read Q4 budget for planning"
         ↓
    compile_policy (LLM)
         ↓
    Signed envelope: vault:finance:read, TTL 300s, Ed25519
         ↓
    Policy check → ALLOW / DENY / REQUIRE_APPROVAL
         ↓
    Vault access (scoped, audited, hash-chained)
```

1. The agent declares its intent in plain English.
2. `compile_policy` uses an LLM to pick the smallest set of scopes.
3. Carryall issues a signed, time-limited envelope.
4. Every action is checked against policy, written to a SHA-256 hash-chained audit log, and scored by Sentinel.

## Install

```bash
pip install authority-runtime
```

Published on PyPI as [`authority-runtime`](https://pypi.org/project/authority-runtime/). The current line needs Python 3.10 or later. The 0.5.x maintenance releases still support Python 3.9.

## Quick start

```bash
# Generate a keypair for an agent
python -m authority_runtime.cli keys generate finance-agent

# MCP server over stdio, for Claude Code and other MCP clients
python -m authority_runtime.cli mcp serve

# MCP server over HTTP, for a chat gateway or other tools
python -m authority_runtime.cli mcp serve --transport http --port 8765
```

## MCP tools

Carryall exposes 8 tools over the [Model Context Protocol](https://modelcontextprotocol.io):

| Tool | Purpose |
|------|---------|
| `compile_policy` | Turn an intent into a minimal-scope signed envelope |
| `check_access` | Check whether an envelope permits an action |
| `list_vaults` | List the data vaults |
| `get_metadata` | Get a document's metadata and access policy |
| `read_document` | Read a document (scoped, audited) |
| `write_document` | Write a document (scoped, audited) |
| `query_documents` | Search within one vault domain |
| `audit_log` | Query the tamper-evident audit trail |

## Security model

### Envelopes

Every agent action needs a signed **AuthorityEnvelope**:

- Ed25519 signature from the agent's keypair
- Short TTL, 300 seconds by default
- Minimal scopes chosen by the policy compiler
- A hash-chained audit entry (SHA-256, with gap detection)

### Sentinel scoring

Sentinel scores audit events for adversarial patterns. Scores add up, capped at 100:

| Pattern | Score |
|---------|-------|
| Trifecta contamination (finance plus untrusted content) | +80 |
| Invalid envelope signature | +50 |
| Context ACL denial (cross-vault read or write) | +40 |
| Cross-domain leak finding | +30 |

A total of 70 or more recommends BLOCK. From 30 to 69 recommends FLAG. Below 30 passes.

### Approval gates

A pipeline stage can require a human decision before it continues. Approvals expire on their own, survive a restart, and land in the audit trail.

## Fleet supervisor

`authority_runtime.supervisor` gives a read-only view of an agent fleet, one row per agent. It reports what each agent is doing, what waits on a human, and what failed or went quiet. It never writes to the sources it reads. A deployment supplies an adapter for its own schedulers and logs. Carryall supplies the status contract, the state and flag rules, and an HTML board and inbox. See [docs/supervisor.md](docs/supervisor.md).

## Components

| Component | Purpose |
|-----------|---------|
| **authority-runtime-python** | Core library: Ed25519 signing, envelopes, policy engine, MCP server, supervisor |
| **mayor** | Routing engine: complexity scoring, local or frontier model selection |
| **context** | Context persistence: ingestion, compaction, embeddings, vault-scoped ACLs |
| **sentinel** | Adversarial scoring: trifecta detection, spend velocity, cross-domain leaks |
| **agents/argus** | Security scanner: data locality and credential exposure checks |
| **policies** | Rego policy templates for 7 vault domains |
| **schemas** | Vault document metadata schema |
| **lib** | Shared utilities: notification routing, pipeline verification |

## Where it sits

Carryall is not a chat gateway or a data store. It sits between the two:

```
Channel (chat bot, Claude Code, other MCP clients)
         |
    Router            ← picks a local or frontier model
         |
    Carryall          ← envelopes, policy, scope enforcement
         |
    Data plane        ← vaults, documents, audit trail
```

My own deployment uses a Telegram bot as the channel and local document vaults as the data plane. Any MCP client and any store with an adapter can fill those slots.

## Testing

```bash
cd authority-runtime-python && python -m pytest
```

779 tests on `main`.

## Versioning

Semantic versioning. Release notes are in [authority-runtime-python/CHANGELOG.md](authority-runtime-python/CHANGELOG.md).

## License

Business Source License 1.1. See [LICENSE](LICENSE). This is a source-available license, not an open-source one.
