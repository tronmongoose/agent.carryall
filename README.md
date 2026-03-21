# Carryall

Agent control plane and IAM layer.

Carryall is not a channel gateway. It requires a channel transport (e.g. OpenClaw) upstream and a deployment config repo (e.g. `agents/`) downstream.

## Architecture

```
Channel Transport (OpenClaw, etc.)
         |
    Mayor (ClawRouter)        <- executive agent, routes + authorizes
         |
  Authorization agents        <- vault-scoped ACLs, Ed25519 signed envelopes
         |
  SLOS / data plane            <- vaults, audit trail
```

## Components

- **authority-runtime-python/** — Core IAM library. Envelope signing, OPA policy compilation, MCP server, SLOS backend integration.
- **mayor/** — ClawRouter executive routing engine. Scores query complexity, routes to local or frontier LLM.
- **context/** — DAG-backed context persistence. Ingestion, assembly, compaction, embeddings, vault-scoped ACLs.
- **sentinel/** — Adversarial scoring engine. Scores audit events and security findings (BLOCK/FLAG/PASS).
- **agents/argus/** — Security scanner. Data locality checks, cross-domain leak detection.
- **lib/** — Shared libraries: common utilities, notification routing, pipeline verification.
- **policies/** — OPA Rego policy templates for vault domain access control.
- **schemas/** — Vault metadata schema.

## Install

```bash
pip install ./authority-runtime-python/
```

## Versioning

Semantic versioning from day one. See `VERSION` and `CHANGELOG.md`.

## License

Business Source License 1.1. See `LICENSE`.
