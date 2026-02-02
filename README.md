# Carryall - Authority Runtime

**The IAM + Context Control Plane for AI Agents**

> Cryptographic policy enforcement for autonomous AI agents. Reduce token costs by ~82% while enforcing least-privilege execution.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## Architecture

```
                                    CARRYALL ARCHITECTURE
    ┌──────────────────────────────────────────────────────────────────────────────┐
    │                                                                              │
    │   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐                     │
    │   │   Agent A   │    │   Agent B   │    │   Agent C   │    AI Agent Fleet   │
    │   │ (Finance)   │    │   (HR)      │    │  (DevOps)   │                     │
    │   └──────┬──────┘    └──────┬──────┘    └──────┬──────┘                     │
    │          │                  │                  │                             │
    │          │    HTTP + Signed Envelope          │                             │
    │          └──────────────────┼──────────────────┘                             │
    │                             │                                                │
    │                             ▼                                                │
    │   ┌──────────────────────────────────────────────────────────────────┐      │
    │   │                    CARRYALL SERVICE                               │      │
    │   │                     (HTTP :8765)                                  │      │
    │   │                                                                   │      │
    │   │  ┌─────────────────────────────────────────────────────────────┐ │      │
    │   │  │                  MCP Server (HTTP Transport)                 │ │      │
    │   │  │                                                              │ │      │
    │   │  │  Tools:                                                      │ │      │
    │   │  │  ├── carryall_check_access  → Policy Decision               │ │      │
    │   │  │  ├── carryall_list_vaults   → Resource Discovery            │ │      │
    │   │  │  ├── carryall_get_metadata  → Document Access               │ │      │
    │   │  │  └── carryall_audit_log     → Audit Trail Query             │ │      │
    │   │  └─────────────────────────────────────────────────────────────┘ │      │
    │   │                             │                                    │      │
    │   │                             ▼                                    │      │
    │   │  ┌─────────────────────────────────────────────────────────────┐ │      │
    │   │  │               POLICY EVALUATION ENGINE                       │ │      │
    │   │  │                                                              │ │      │
    │   │  │  1. Verify Ed25519 signature on envelope                    │ │      │
    │   │  │  2. Check TTL expiration                                    │ │      │
    │   │  │  3. Validate parent-child authority (child ⊆ parent)        │ │      │
    │   │  │  4. Match scopes against requested resource                 │ │      │
    │   │  │  5. Return: ALLOW | DENY | REQUIRE_APPROVAL                 │ │      │
    │   │  └─────────────────────────────────────────────────────────────┘ │      │
    │   │                             │                                    │      │
    │   │                             ▼                                    │      │
    │   │  ┌─────────────────────────────────────────────────────────────┐ │      │
    │   │  │                    AUDIT LOG                                 │ │      │
    │   │  │                                                              │ │      │
    │   │  │  Every decision recorded:                                   │ │      │
    │   │  │  ├── timestamp                                              │ │      │
    │   │  │  ├── agent_id (from envelope)                               │ │      │
    │   │  │  ├── action attempted                                       │ │      │
    │   │  │  ├── resource requested                                     │ │      │
    │   │  │  ├── decision (allow/deny/require_approval)                 │ │      │
    │   │  │  ├── envelope signature verification                        │ │      │
    │   │  │  └── decision rationale                                     │ │      │
    │   │  └─────────────────────────────────────────────────────────────┘ │      │
    │   └──────────────────────────────────────────────────────────────────┘      │
    │                                                                              │
    │                             │                                                │
    │                             ▼                                                │
    │   ┌──────────────────────────────────────────────────────────────────┐      │
    │   │                    DATA BACKENDS                                  │      │
    │   │                                                                   │      │
    │   │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │      │
    │   │  │ SLOS Vaults  │  │   Future:    │  │   Future:    │           │      │
    │   │  │ (Documents)  │  │   S3/GCS     │  │   Databases  │           │      │
    │   │  └──────────────┘  └──────────────┘  └──────────────┘           │      │
    │   └──────────────────────────────────────────────────────────────────┘      │
    │                                                                              │
    └──────────────────────────────────────────────────────────────────────────────┘


                              AUTHORITY ENVELOPE FLOW

    ┌────────────────────────────────────────────────────────────────────────────┐
    │                                                                            │
    │   STEP 1: Agent Request                                                    │
    │   ┌──────────────────────────────────────────────────────────────────┐    │
    │   │  Agent: "I need to read Q4 finance report"                       │    │
    │   │  Agent ID: finance-agent-001                                     │    │
    │   └──────────────────────────────────────────────────────────────────┘    │
    │                                   │                                        │
    │                                   ▼                                        │
    │   STEP 2: LLM Policy Compiler (PLANNED)                                   │
    │   ┌──────────────────────────────────────────────────────────────────┐    │
    │   │  Input:  Natural language intent + agent context                 │    │
    │   │  Output: Minimal scopes + resources + TTL                        │    │
    │   │                                                                  │    │
    │   │  "Read Q4 finance report" →                                      │    │
    │   │     scopes: ["vault:finance:read"]                               │    │
    │   │     resources: ["slos://vaults/finance/q4-report"]               │    │
    │   │     ttl: 300 seconds                                             │    │
    │   └──────────────────────────────────────────────────────────────────┘    │
    │                                   │                                        │
    │                                   ▼                                        │
    │   STEP 3: Envelope Creation + Signing                                     │
    │   ┌──────────────────────────────────────────────────────────────────┐    │
    │   │  {                                                               │    │
    │   │    "envelope_id": "env-abc123",                                  │    │
    │   │    "agent_id": "finance-agent-001",                              │    │
    │   │    "created_at": "2025-01-31T12:00:00Z",                         │    │
    │   │    "expires_at": "2025-01-31T12:05:00Z",                         │    │
    │   │    "authority": {                                                │    │
    │   │      "scopes": ["vault:finance:read"],                           │    │
    │   │      "resources": ["slos://vaults/finance/q4-report"]            │    │
    │   │    },                                                            │    │
    │   │    "signature": "Ed25519:abc123..."  ← Cryptographic proof       │    │
    │   │  }                                                               │    │
    │   └──────────────────────────────────────────────────────────────────┘    │
    │                                   │                                        │
    │                                   ▼                                        │
    │   STEP 4: Policy Check (Carryall Service)                                 │
    │   ┌──────────────────────────────────────────────────────────────────┐    │
    │   │  POST /tools/carryall_check_access                               │    │
    │   │                                                                  │    │
    │   │  ✓ Signature valid                                               │    │
    │   │  ✓ TTL not expired                                               │    │
    │   │  ✓ Scope matches: vault:finance:read                             │    │
    │   │  ✓ Resource matches: slos://vaults/finance/*                     │    │
    │   │                                                                  │    │
    │   │  Decision: ALLOW                                                 │    │
    │   │  Logged to audit trail                                           │    │
    │   └──────────────────────────────────────────────────────────────────┘    │
    │                                   │                                        │
    │                                   ▼                                        │
    │   STEP 5: Action Execution                                                │
    │   ┌──────────────────────────────────────────────────────────────────┐    │
    │   │  Agent reads Q4 finance report                                   │    │
    │   │  Only this specific action was permitted                         │    │
    │   │  No access to HR data, DevOps systems, or other finance docs     │    │
    │   └──────────────────────────────────────────────────────────────────┘    │
    │                                                                            │
    └────────────────────────────────────────────────────────────────────────────┘
```

---

## The Problem

AI agents today operate with dangerous, all-or-nothing permissions:

- Agents get full API keys and credentials
- No audit trail of what agents actually accessed
- Token costs explode as context grows with each step
- CISOs can't approve what they can't audit

**This is the bottleneck for enterprise AI adoption.**

---

## The Solution

Carryall is an **AI-native IAM layer** that enforces least-privilege execution with cryptographic guarantees.

### Core Invariant
> **"Authority and context only ever narrow—never expand—as agents act."**

Enforced by:
- Ed25519 cryptographic signatures
- Parent-child validation (child ⊆ parent)
- TTL expiration (time-bounded authority)
- Immutable audit trail

---

## What's Built (January 2025)

| Component | Status | Description |
|-----------|--------|-------------|
| Authority Runtime Python | Working | Core envelope creation, signing, verification |
| Ed25519 Cryptographic Identity | Working | Key generation, signing, signature verification |
| MCP Server | Working | HTTP + stdio transports, 4 policy tools |
| Policy Evaluation | Working | allow/deny/require_approval decisions |
| SLOS Backend | Working | Mock vault system for testing |
| CLI | Working | Keys, envelopes, MCP serve, SLOS commands |
| Docker Images | Working | carryall:0.2.0, clawdbot:0.3.0 |
| Helm Chart | Working | Kubernetes deployment with sidecar support |
| Test Suite | 98.3% | 59/60 tests passing |
| Token Reduction | 82% | Measured in E2E tests |

### Not Yet Built
- LLM Policy Compiler (natural language → minimal scopes)
- Persistent audit storage (currently in-memory)
- Multi-agent fleet management

---

## Quick Start

### Local Development

```bash
cd authority-runtime-python

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install
pip install -e ".[dev]"

# Generate keys
carryall keys generate

# Run tests
pytest

# Start MCP server (stdio mode)
carryall mcp serve

# Start MCP server (HTTP mode for Kubernetes)
carryall mcp serve --transport http --port 8765
```

### Kubernetes Deployment

```bash
# Build and push Docker image
docker build -f docker/Dockerfile.carryall -t carryall:0.2.0 .

# Deploy with Helm
helm install carryall ./helm/clawdbot-carryall \
  --set secrets.anthropicApiKey=$ANTHROPIC_API_KEY \
  --set carryall.enabled=true

# Check health
kubectl exec -it deploy/carryall -- curl localhost:8765/health
```

---

## MCP Tools

Carryall exposes policy enforcement as MCP (Model Context Protocol) tools:

### carryall_check_access
Check if an envelope authorizes an action.

```bash
curl -X POST http://localhost:8765/tools/carryall_check_access \
  -H "Content-Type: application/json" \
  -d '{
    "envelope": {
      "envelope_id": "env-001",
      "agent_id": "finance-agent",
      "authority": {
        "scopes": ["vault:finance:read"],
        "resources": ["slos://vaults/finance/*"]
      },
      "signature": "..."
    },
    "action": "read",
    "resource": "slos://vaults/finance/q4-report"
  }'

# Response: {"decision": "allow", "reason": "..."}
```

### carryall_audit_log
Query the audit trail.

```bash
curl -X POST http://localhost:8765/tools/carryall_audit_log \
  -H "Content-Type: application/json" \
  -d '{
    "envelope": {...},
    "agent_id": "finance-agent",
    "limit": 50
  }'
```

---

## Scope Format

Scopes follow `namespace:resource:action` pattern:

```
vault:finance:read     - Read access to finance vault
vault:shared:write     - Write access to shared vault
vault:*:read           - Read access to all vaults
audit:read             - Required for audit log queries
```

---

## Policy Decisions

| Decision | Meaning |
|----------|---------|
| `allow` | Agent has explicit access via envelope scopes |
| `deny` | Agent lacks required scope or is explicitly denied |
| `require_approval` | Action needs human approval before proceeding |

Evaluation order: denied_agents > requires_approval > allowed_agents > envelope scopes > default deny.

---

## Project Structure

```
authority-runtime-python/
├── src/authority_runtime/
│   ├── envelope.py      # Envelope creation, signing, verification
│   ├── signing.py       # Ed25519 cryptographic operations
│   ├── policy.py        # Policy evaluation engine
│   ├── mcp_server.py    # MCP server (stdio + HTTP)
│   ├── slos.py          # SLOS vault backend
│   └── cli.py           # CLI commands
├── tests/               # Test suite (98.3% pass rate)
└── pyproject.toml       # Dependencies

helm/clawdbot-carryall/  # Kubernetes Helm chart
├── templates/
│   ├── deployment.yaml  # Pod spec with carryall sidecar
│   ├── service.yaml     # Service exposure
│   ├── configmap.yaml   # MCP server config
│   └── secrets.yaml     # API keys
└── values.yaml          # Configuration

docker/
├── Dockerfile.carryall  # Standalone carryall image
└── Dockerfile.clawdbot  # Agent gateway with embedded carryall
```

---

## Roadmap

### Now: Multi-Agent HTTP Service
- Deploy carryall as standalone Kubernetes service
- Multiple agents call shared policy service
- Centralized audit log across agent fleet

### Next: LLM Policy Compiler
- Natural language intent → minimal scopes
- "Read Q4 finance report" → `vault:finance:read` + specific resource
- This is what makes it scalable (not traditional RBAC)

### Future: Enterprise Features
- Persistent audit storage (PostgreSQL/S3)
- Compliance dashboards
- Policy version control
- Integration with existing IAM (Okta, Azure AD)

---

## Why This Matters

Traditional IAM vendors are structurally misaligned for AI agents:
- Their systems assume static roles and predictable human behavior
- Agents traverse SaaS tools, cloud services, APIs in one workflow
- No existing system captures *why* decisions happened

Carryall is the IAM control plane that makes agent execution auditable, bounded, and provable.

---

## License

MIT License - See LICENSE file

---

## Links

- [Native Agent IAM - Runtime Auth](./Native%20Agent%20IAM%20-%20%20Runtime%20Auth.md) - Full vision document
- [GitHub](https://github.com/tronmongoose/agent.carryall)

---

*Last updated: January 2025*
