# Authority Runtime

**Cryptographic IAM for AI agents** -- scoped, signed, time-limited permissions with a tamper-evident audit trail.

[![License: BSL 1.1](https://img.shields.io/badge/License-BSL%201.1-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-182%20passing-brightgreen.svg)]()

---

## The Problem

AI agents operate with all-or-nothing permissions. If an agent has an API key, it can do anything with that credential. Traditional auth (OAuth, RBAC, JWT) assumes a human clicked a button -- agents don't click buttons.

Authority Runtime creates **cryptographically signed permission envelopes** that scope exactly what an agent can do, enforced at runtime, with a complete audit trail.

```
Parent Envelope                    Child Envelope
|- scopes: [read, write, delete]   |- scopes: [read]        <- narrowed
|- context: [user, email, history] |- context: [email]      <- narrowed
|- ttl: 10 minutes                 |- ttl: 5 minutes        <- narrowed
'- signature: Ed25519(...)         '- signature: Ed25519(...)
```

**The child cannot exceed the parent. Cryptographically enforced.**

---

## Install

```bash
pip install authority-runtime
```

---

## Quick Start

```python
from authority_runtime import generate_key_pair, create_simple_envelope, check_envelope

# Generate Ed25519 identity
private_key, public_key = generate_key_pair()

# Create a scoped, signed, time-limited envelope
envelope = create_simple_envelope(
    agent_id="my-agent",
    scopes=["read:users", "write:users"],
    private_key=private_key,
)

# Verify access -- passes
check_envelope(envelope, public_key, required_scope="read:users")

# Verify access -- raises PermissionDenied
check_envelope(envelope, public_key, required_scope="delete:users")
```

See [docs/QUICKSTART.md](docs/QUICKSTART.md) for more examples including `EnforcedTool` runtime blocking and HTML compliance reports.

### Zero-dependency quickstart (no API key, no network)

For evaluation, CI, and offline work, pair `MemoryBackend` with `FakeCompiler` to exercise the full intent → compiled scopes → signed envelope → access check loop without any LLM provider:

```bash
pip install authority-runtime
python examples/quickstart_memory.py
```

`FakeCompiler` is a deterministic, rule-based implementation of `LLMCompiler` — it maps keywords in the intent to scopes that must already be in the parent's authority, with the same subset-enforcement guarantees as the OpenAI/Anthropic compilers. Use it as the default compiler in tests and CI.

### Pluggable backends

`authority_runtime.backends.Backend` is a `runtime_checkable` Protocol. `MemoryBackend` and `SlosBackend` implement it; third parties can ship their own (e.g. a ConductorOne Baton adapter) by implementing the seven methods and registering an entry point:

```toml
# pyproject.toml of the adapter package
[project.entry-points."authority_runtime.backends"]
baton = "carryall_baton.backend:BatonBackend"
```

Load a backend from a config file:

```json
{
  "backend": "baton",
  "init": { "c1z_path": "./sync.c1z" }
}
```

```bash
export CARRYALL_SLOS_CONFIG=./backend.json
```

```python
from authority_runtime.backends import load_backend
backend = load_backend()  # honors CARRYALL_SLOS_CONFIG, defaults to MemoryBackend
```

---

## What It Does

1. **Scope** -- Define exactly which tools and data an agent can access
2. **Sign** -- Ed25519 signatures make permissions tamper-proof
3. **Expire** -- TTLs ensure permissions don't persist forever
4. **Enforce** -- `EnforcedTool` blocks unauthorized actions at runtime
5. **Audit** -- Every action logged with cryptographic proof
6. **Verify** -- SHA-256 hash chain on audit trail detects tampering and deletions

### Key Features

- **YAML policy engine** -- define agent permissions in declarative YAML
- **Constraint enforcement** -- require_purpose, denied_resources, max_records, require_approval
- **Wildcard scope matching** -- `vault:*:read` matches `vault:finance:read`
- **HTML compliance reports** -- negative attestation ("agent never accessed X")
- **Tamper-evident audit trail** -- SHA-256 hash chain, `carryall audit --verify`
- **Schema migrations** -- versioned, with automatic backup
- **MCP server** -- HTTP and stdio transports with Bearer auth + rate limiting
- **LangGraph integration** -- graph-based agents with automatic permission narrowing

---

## CLI

```bash
carryall init                          # Initialize ~/.carryall/
carryall keys generate --agent-id bot  # Generate Ed25519 keypair
carryall mcp serve --transport http    # Start MCP server
carryall audit query                   # Query audit trail
carryall audit --verify                # Verify hash chain integrity
carryall compliance report             # Generate HTML compliance report
carryall policy validate policy.yaml   # Validate YAML policy
carryall db status                     # Check database + migrations
```

---

## Edtech FERPA Demo

A complete multi-agent demo showing FERPA compliance:

```bash
git clone https://github.com/tronmongoose/carryall-edtech-pilot.git
cd carryall-edtech-pilot
pip install authority-runtime
python -m demo.run
```

Demonstrates: agent identity, least privilege, access denial, negative attestation, compliance export. No API keys needed.

---

## Architecture

```
Agent Request
    |
    v
Root Envelope (Ed25519 signed, scoped, time-bounded)
    |
    v
Policy Engine (YAML policies, constraints, scope matching)
    |
    v
EnforcedTool (validates signature, checks TTL, verifies scope)
    |
    v
Audit Trail (SQLite, hash chain, compliance export)
```

---

## Design Constraints

1. **Envelopes are immutable** -- create new ones, don't modify existing
2. **Children subset Parents** -- authority only narrows, never expands
3. **TTLs only decrease** -- child can't outlive parent (60s-24h range)
4. **Signatures are mandatory** -- no unsigned envelopes
5. **Enforcement is cryptographic** -- can't bypass without private key

---

## Documentation

| Doc | Description |
|-----|-------------|
| [Getting Started](docs/QUICKSTART.md) | 5-minute tutorial with 3 progressive examples |
| [Deployment](docs/DEPLOYMENT.md) | Local, Docker Compose, and Kubernetes |
| [Configuration](docs/CONFIGURATION.md) | Environment variables, YAML policies, logging |
| [Changelog](CHANGELOG.md) | Release history |
| [Security](SECURITY.md) | Vulnerability reporting + architecture |
| [Contributing](CONTRIBUTING.md) | Development setup + PR process |

---

## Test Suite

182 tests across 15 test files covering envelope operations, scope matching, constraint enforcement, policy engine, compliance reports, hash chain integrity, schema migrations, MCP auth, and structured logging.

```bash
pytest            # Run all tests
pytest -v -x      # Verbose, stop on first failure
```

---

## License

Business Source License 1.1 - See [LICENSE](LICENSE). Converts to Apache 2.0 after 4 years.
