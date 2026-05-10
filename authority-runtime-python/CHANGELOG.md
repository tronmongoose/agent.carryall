# Changelog

All notable changes to Authority Runtime are documented here.

## [0.5.0] - 2026-05-10

### Added — bjornswarm-pattern ports

Five generic patterns extracted from bjornswarm (Carryall's customer-zero deployment) and shipped upstream as deployment-agnostic primitives. Mechanism ported up; deployment policy stays down.

- **`skill_loader`** — `SKILL.md` frontmatter loader with fail-closed `tools:` allowlist. Pydantic-validated; no wildcards, no prefix matching, missing or empty list denies everything. New `SkillManifest` type and `enforce_tool_access()` helper. (Port #1)
- **`harness_audit`** — static config-surface scanner. `HarnessAuditor` walks a config root, runs registered `Rule` objects, emits `Finding`s to JSONL (append-only). Two universal built-in rules: `no-dangerous-mode-skip` (critical), `skills-declare-tools` (medium). Rule exceptions are isolated — a rule that raises becomes a `severity=high` finding rather than crashing the scan. (Port #2)
- **`rule_packs`** — numbered hard-rule enforcement. `RulePack.load(yaml)` loads deployment rules; `@enforces(pack, point)` decorator wraps pipeline entry points; `RuleViolation` carries `rule_id` / `rule_number` / `description` / `enforcement_point` so violations trace back to a deployment's canonical rule list (e.g. bjornswarm "Rule #14"). Predicate convention aligned with `authority_runtime.constraints`: predicate returns `None` to pass, a string to fail. (Port #3)
- **`router`** — sensitivity-aware tiered routing primitive. `Router(classifier, registry, logger)` composes a `SensitivityClassifier` (ABC) with a `ModelRegistry` and pluggable `UsageLogger`. `force_tier=` overrides classification with `forced=True` audit trail. `ModelRegistry.assert_origins_allowed(set)` raises at boot if any tier's origin falls outside policy. `JsonlUsageLogger` privacy posture: writes only `query_len`, never the body. (Port #5)
- **`load_soul()` / `SkillSoul`** — opt-in SOUL.md sibling parser for skill voice/posture docs. `load_skill()` auto-attaches a sibling `SOUL.md` if present (`load_soul=False` to skip). Convention is **under evaluation in bjornswarm** (eval `sl-qvby`, window closes 2026-06-02) — Carryall ships the parser so deployments running the eval don't fork bespoke tooling, but the convention itself is documented as descriptive, not prescriptive. (Port #4)

### Added — Compiler

- **`OllamaCompiler`** — local Ollama-backed policy compiler. Same Pydantic-validated scope/context narrowing as `OpenAICompiler` / `AnthropicCompiler`; `gemma4:26b` default; `/api/chat` with `format: json` and `num_predict=2000` for Gemma's internal CoT. Routes via `aiohttp` to a local Ollama daemon — no API key, no network beyond localhost.
- `mcp_server`: `ollama` is now the default `llm_provider` for `compile_policy`, and any `available_scopes` containing `"finance"` is force-routed to `ollama` regardless of caller-supplied provider (sensitive data must stay local). Missing API keys for frontier providers now raise `PermissionDenied` instead of `ValueError` so failures are fail-closed at the auth layer.

### Tests

- 96 new tests across the five ports (skill_loader: 19, harness_audit: 19, rule_packs: 26, router: 22, soul_loader: 12). Suite total: 478 → 490 passing.

## [0.4.0] - 2026-04-19

### Added

- `authority_runtime.backends.Backend` — runtime-checkable Protocol codifying the seven-method contract already implemented by `MemoryBackend` and `SlosBackend`.
- `Decision`, `PolicyResult`, `DocumentMetadata` moved to `backends/base.py` so third-party adapters don't need to import from `slos.py`; `slos.py` re-exports for backward compatibility.
- `authority_runtime.backends.load_backend()` — resolves a backend from JSON config via built-in name, importlib entry point (`authority_runtime.backends` group), or dotted path. Honors `CARRYALL_SLOS_CONFIG`; falls back to `MemoryBackend` when unset.
- Built-in `memory` and `slos` entry points registered in `pyproject.toml`.
- `FakeCompiler` — deterministic, rule-based `LLMCompiler` subclass that maps keywords to scopes with the same subset-validation guarantees as the OpenAI/Anthropic compilers. No API key required.
- `examples/quickstart_memory.py` — full intent → compiled scopes → signed envelope → `MemoryBackend` access check loop, runnable in under a second without any LLM provider.
- README sections: "Zero-dependency quickstart" and "Pluggable backends".

## [0.3.0] - 2026-02-27

### Added
- Tamper-evident audit trail with SHA-256 hash chain (entry_hash + prev_hash)
- `verify_audit_chain()` method -- detects tampering, deletions, and modifications
- Versioned schema migrations with `schema_versions` table
- Automatic database backup before migrations
- SQLite WAL mode + `PRAGMA synchronous=FULL` for crash safety
- Bearer token auth middleware (`CARRYALL_API_KEY` env var)
- Per-IP sliding-window rate limiter (`CARRYALL_RATE_LIMIT` env var)
- Structured JSON logging with request ID correlation
- Graceful SIGTERM/SIGINT shutdown with connection draining
- Audit archival -- moves old entries to `audit_trail_archive` table
- CLI commands: `db migrate`, `db status`, `audit archive`
- Enhanced `audit export` with `--since` and `--format` options
- Real `audit --verify` using hash chain verification
- Docker Compose for single-command deployment
- GitHub Actions CI (pytest on Python 3.9/3.11/3.12/3.13 + ruff)
- GitHub Actions PyPI publish via OIDC on release

### Changed
- Version aligned to 0.3.0 across pyproject.toml, __init__.py, mcp_server.py, compliance.py

### Fixed
- Removed hardcoded `~/Desktop/sovereign-life-os/` path from MCP server
- MCP server falls back to MemoryBackend when no SLOS config is set

### Security
- Explicit `BEGIN IMMEDIATE` transactions for atomic audit writes
- Hash chain prevents silent deletion of audit entries

## [0.2.0] - 2026-02-25

### Added
- Wildcard scope matching (`vault:*:read` matches `vault:finance:read`)
- Constraint enforcement engine (require_purpose, denied_resources, max_records, require_approval)
- YAML policy engine -- define agent policies in YAML files
- HTML compliance reports with negative attestations
- `ComplianceReport` class with `render_html()` output
- Policy CLI commands: `policy list`, `policy validate`
- Compliance CLI commands: `compliance report`, `compliance export`
- SLOS backend integration for vault-based document storage
- Memory backend for testing without filesystem
- 144 tests (up from 29)

## [0.1.0] - 2026-02-15

### Added
- Core Authority Envelope system with Ed25519 signing
- `create_envelope()`, `create_simple_envelope()`, `create_child_envelope()`
- `validate_envelope()` with parent-child subset enforcement
- `EnforcedTool` class -- wraps functions with runtime permission enforcement
- `EnforcedToolkit` for managing groups of enforced tools
- `EnvelopeStore` -- SQLite persistence for envelopes and audit trail
- `create_audit_entry()` and `export_audit_trail()`
- LangGraph integration (`create_authority_graph()`)
- LLM-based policy compiler (OpenAI + Anthropic)
- CLI with key management, credentials, audit queries, MCP server
- MCP server with HTTP and stdio transports
- Input validation with detailed error messages
- 29 tests
