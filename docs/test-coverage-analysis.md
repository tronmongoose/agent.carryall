# Test Coverage Analysis

**Date:** 2026-03-17
**Scope:** `authority-runtime-python/` (core Python package)

---

## Current State

- **16 test modules**, ~4,700 lines of test code
- **182 tests** passing (pytest)
- **~9,800 LOC** of source code across 17 modules + 2 backends
- **Estimated coverage: ~51%** — well-tested core, but several major modules have zero dedicated tests

---

## Coverage Map

### Well-Tested Modules

| Module | LOC | Test File(s) | Notes |
|--------|-----|-------------|-------|
| envelope.py | 575 | test_envelope.py (430 LOC) | Signing, validation, narrowing |
| storage.py | 970 | test_storage.py, test_hash_chain.py, test_migrations.py | Persistence, hash chain, schema migrations |
| compliance.py | 572 | test_compliance.py, test_compliance_report.py | Reports, HTML rendering, XSS escaping |
| enforce.py | 590 | test_constraints.py, test_scope_matching.py | Scope matching, constraint enforcement |
| policy.py | 245 | test_policy.py (269 LOC) | YAML loading, agent policies, envelope creation |
| validation.py | 428 | test_validation.py (269 LOC) | Input validation, error paths |
| backends/memory.py | 296 | test_memory_backend.py (237 LOC) | In-memory vault, standalone mode |
| logging_config.py | 71 | test_logging.py (103 LOC) | JSON formatter, request IDs |

### Untested Modules (No Dedicated Tests)

| Module | LOC | Risk | Why It Matters |
|--------|-----|------|----------------|
| **cli.py** | 1,405 | High | User-facing entry point. 8 CLI sub-apps, 100+ commands. Zero test coverage. |
| **compiler.py** | 719 | High | LLM policy compiler (OpenAI/Anthropic). Prompt injection detection, token metrics, confidence scoring — all untested. |
| **mcp_server.py** | 1,260 | High | Only ~150 LOC tested (rate limiter + HTTP auth). Tool dispatching, envelope caching, SLOS integration, error handling all untested. |
| **roles.py** | 539 | Medium | Dynamic RBAC system with intent matching, caching, priority resolution. No tests. |
| **backends/slos.py** | 515 | Medium | SLOS vault adapter. URI parsing, policy evaluation, MCP signing — all untested. |
| **shell.py** | 489 | Medium | Interactive REPL with 8 commands. No tests for command parsing or audit logging. |
| **langgraph.py** | 407 | Medium | LangGraph integration. Graph construction, state management, envelope narrowing in agent workflows. |
| **keys.py** | 212 | Low-Medium | Ed25519 key management. File permissions (0o600), caching, import/export. |
| **types.py** | 281 | Low | Pydantic models — tested indirectly through every other module. |

---

## Recommended Improvements (Priority Order)

### 1. `test_compiler.py` — LLM Policy Compiler (HIGH)

**Why:** Security-sensitive. Compiles natural language to authority scopes. Prompt injection detection is untested.

**Test cases to add:**
- Scope extraction from LLM responses (happy path)
- Prompt injection detection and rejection
- Confidence scoring thresholds
- Fallback behavior when LLM is unavailable
- Token counting and pricing accuracy
- Invalid/malformed LLM responses
- Scope validation against known patterns

### 2. `test_mcp_server.py` — MCP Server Tools (HIGH)

**Why:** The MCP server is the primary runtime interface (1,260 LOC) but only rate limiting and auth middleware are tested.

**Test cases to add:**
- `carryall_check_access` tool dispatching and response format
- `carryall_list_vaults` with multiple backends
- `carryall_get_metadata` with valid/invalid resources
- `carryall_audit_log` query filtering and pagination
- Envelope validation within tool calls
- Error responses for malformed requests
- SLOS backend integration through MCP layer
- Concurrent request handling

### 3. `test_cli.py` — CLI Commands (HIGH)

**Why:** User-facing entry point. Broken CLI commands = broken first experience.

**Test cases to add:**
- `carryall init` — project scaffolding
- `carryall keys generate` — key creation and file permissions
- `carryall credentials issue` — envelope creation via CLI
- `carryall audit query` — audit trail queries
- `carryall compliance report` — report generation
- `carryall policy validate` — YAML policy validation
- `carryall mcp serve` — server startup
- Error messages for invalid inputs
- Use `typer.testing.CliRunner` for isolation

### 4. `test_roles.py` — RBAC System (MEDIUM)

**Why:** Role-based access is a core security feature. Intent matching, priority resolution, and caching are untested.

**Test cases to add:**
- Built-in role definitions (FERPA roles)
- Custom role creation and persistence
- Intent matching accuracy
- Role priority resolution (most-specific wins)
- Cache invalidation
- Role inheritance
- Edge cases: overlapping roles, missing roles

### 5. `test_slos_backend.py` — SLOS Vault Backend (MEDIUM)

**Why:** SLOS is the production storage backend. URI parsing and policy evaluation are security-critical.

**Test cases to add:**
- `parse_slos_uri()` — valid and malformed URIs
- Policy evaluation (allow/deny/require_approval)
- Document read/write with metadata
- MCP signing verification
- Vault listing and filtering
- Error handling for unreachable vaults

### 6. `test_langgraph.py` — LangGraph Integration (MEDIUM)

**Why:** Agent framework integration. Envelope narrowing within graphs is the core value proposition.

**Test cases to add:**
- `create_authority_node()` — node creation and configuration
- `create_authority_graph()` — graph construction
- `AuthorityState` management across nodes
- Envelope narrowing at each graph step
- Permission denial propagation through graph
- Multi-step workflow with audit trail

### 7. `test_shell.py` — Interactive Shell (LOW-MEDIUM)

**Test cases to add:**
- Each shell command (vaults, list, read, metadata, check, switch, audit, whoami)
- Command parsing and argument validation
- Audit logging of shell actions
- Error handling for invalid commands

### 8. `test_keys.py` — Key Management (LOW-MEDIUM)

**Test cases to add:**
- Key pair generation and storage
- File permission enforcement (0o600)
- Key import/export
- Cache behavior
- Concurrent access to key store

---

## Cross-Cutting Gaps

### No Coverage Configuration
There is no `.coveragerc` or `pytest-cov` configuration. Adding `pytest-cov` would enable tracking actual line/branch coverage percentages.

**Recommendation:** Add to `pyproject.toml`:
```toml
[tool.pytest.ini_options]
addopts = "-v --tb=short --cov=authority_runtime --cov-report=term-missing"

[tool.coverage.run]
source = ["src/authority_runtime"]
branch = true

[tool.coverage.report]
fail_under = 70
show_missing = true
```

### No TypeScript Extension Tests
The `extensions/carryall/` TypeScript plugin has zero tests. It's a thin HTTP client, but request formatting and error handling should be validated.

### No Negative/Security Tests for Core Modules
While `test_integration.py` covers some security scenarios (tampering, expiration), there are no dedicated adversarial tests for:
- Signature forgery attempts
- Scope escalation attacks
- Replay attacks with expired envelopes
- Race conditions in concurrent envelope creation
- SQL injection in storage queries

### No Performance/Load Tests
No tests validate behavior under load (concurrent envelope creation, large audit trails, many-level delegation chains).

---

## Summary

The core cryptographic and policy modules are well-tested. The biggest gaps are in the **user-facing layers** (CLI, MCP server, shell) and **integration modules** (compiler, LangGraph, SLOS backend, roles). Addressing items 1-3 above would bring coverage to an estimated ~75% and cover the most critical untested paths.
