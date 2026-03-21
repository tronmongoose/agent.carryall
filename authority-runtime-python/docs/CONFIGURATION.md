# Configuration Guide

## Environment Variables

All configuration is via environment variables. Set them in your shell, a `.env` file, or Docker Compose.

| Variable | Default | Description |
|----------|---------|-------------|
| `CARRYALL_API_KEY` | (none) | Bearer token for HTTP auth |
| `CARRYALL_LOG_LEVEL` | `INFO` | DEBUG, INFO, WARNING, ERROR |
| `CARRYALL_LOG_FORMAT` | `json` | `json` or `text` |
| `CARRYALL_RATE_LIMIT` | `100` | Requests per minute per IP |
| `CARRYALL_DB` | `~/.carryall/authority.db` | SQLite database path |
| `CARRYALL_KEYS_DIR` | `~/.carryall/keys` | Agent keypair storage |
| `CARRYALL_CREDENTIALS_DIR` | `~/.carryall/credentials` | Issued credentials |
| `CARRYALL_SLOS_CONFIG` | (none) | SLOS backend config path |
| `OPENAI_API_KEY` | (none) | For LLM policy compiler |
| `ANTHROPIC_API_KEY` | (none) | Alternative compiler |

---

## YAML Policy Files

Define agent permissions in YAML:

```yaml
version: "1.0"
agents:
  academic-advisor:
    description: "Academic advising agent"
    clearance: standard
    scopes:
      - vault:students:read
    resources:
      - "slos://vaults/student-records/*"
    constraints:
      require_purpose: true
      denied_resources:
        - "slos://vaults/student-health/*"
    rate_limit: 100
```

### Key Fields

- **scopes**: List of `domain:resource:action` permission strings. Supports wildcards (`vault:*:read`).
- **resources**: URI patterns the agent can access. Wildcards supported.
- **constraints**: Enforcement rules applied at runtime:
  - `require_purpose`: Agent must state why it needs access
  - `denied_resources`: Explicit deny list (overrides scopes)
  - `max_records`: Limit on records returned per query
  - `require_approval`: Actions that need human sign-off

### Loading Policies

```bash
carryall policy list                    # List loaded policies
carryall policy validate policy.yaml    # Validate a policy file
```

---

## Database

### Location

Default: `~/.carryall/authority.db`

Override with `CARRYALL_DB` environment variable.

### SQLite Settings

Carryall enables these SQLite pragmas automatically:
- `journal_mode=WAL` -- write-ahead logging for crash safety
- `synchronous=FULL` -- ensures data is written to disk

### Schema Migrations

Migrations run automatically when the database is opened. Check status:

```bash
carryall db status
```

### Audit Trail Integrity

The audit trail uses a SHA-256 hash chain. Each entry's hash includes the previous entry's hash, making tampering and deletions detectable.

```bash
carryall audit --verify
```

---

## Logging

### JSON Format (default)

```json
{"timestamp": "2026-02-27T10:15:30Z", "level": "INFO", "logger": "authority_runtime.mcp_server", "message": "Request handled", "request_id": "abc123", "method": "check_access", "duration_ms": 12}
```

### Text Format

Set `CARRYALL_LOG_FORMAT=text` for human-readable output:

```
2026-02-27 10:15:30 INFO authority_runtime.mcp_server - Request handled [request_id=abc123]
```

### Debug Mode

Set `CARRYALL_LOG_LEVEL=DEBUG` for verbose output including envelope validation details, scope matching, and constraint checking.
