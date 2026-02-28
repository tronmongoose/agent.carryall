# Changelog

All notable changes to Authority Runtime are documented here.

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
