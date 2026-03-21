# Deployment Guide

## Local (Mac / Linux)

### Install

```bash
pip install authority-runtime
```

Or from source:

```bash
git clone https://github.com/tronmongoose/agent.carryall.git
cd agent.carryall/authority-runtime-python
pip install -e .
```

### Initialize

```bash
carryall init
```

This creates `~/.carryall/` with:
- `keys/` -- Ed25519 agent keypairs
- `credentials/` -- issued credentials
- `authority.db` -- SQLite audit database

### Generate Agent Keys

```bash
carryall keys generate --agent-id my-agent
```

### Start MCP Server

```bash
# stdio (for local MCP clients like Claude Code)
carryall mcp serve --transport stdio

# HTTP (for remote clients or Docker)
carryall mcp serve --transport http --port 8765
```

### With API Authentication

```bash
export CARRYALL_API_KEY=your-secret-key
carryall mcp serve --transport http --port 8765
```

Clients must include `Authorization: Bearer your-secret-key` header. Health endpoints (`/health`, `/healthz`) bypass auth.

### Verify Installation

```bash
carryall db status            # Check database and migrations
carryall audit --verify       # Verify audit trail integrity
carryall policy list          # List loaded policies
```

---

## Docker Compose

### Quick Start

```bash
cd docker/
docker compose up -d
```

### Configuration

Create a `.env` file alongside `docker-compose.yml`:

```bash
CARRYALL_API_KEY=your-secret-key
CARRYALL_LOG_LEVEL=INFO
CARRYALL_LOG_FORMAT=json
CARRYALL_RATE_LIMIT=100
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
```

### Health Check

```bash
curl http://localhost:8765/health
# {"status": "healthy"}
```

### Data Persistence

Data is stored in a Docker volume (`carryall-data`). To back up:

```bash
docker compose exec carryall cp /home/carryall/.carryall/authority.db /home/carryall/.carryall/authority.db.bak
```

---

## Kubernetes (Helm)

A Helm chart is available at `helm/clawdbot-carryall/`.

```bash
helm install carryall helm/clawdbot-carryall/ \
  --set carryall.apiKey=your-secret-key \
  --set carryall.logLevel=INFO
```

The chart supports:
- Carryall as a sidecar or standalone service
- ConfigMap-based policy configuration
- PersistentVolume for audit database
- Network policies restricting egress to LLM APIs only

See `helm/clawdbot-carryall/values.yaml` for all options.

---

## Database Management

### Migrations

Migrations run automatically on startup. To run manually:

```bash
carryall db migrate
```

Before running migrations on an existing database, a backup is created automatically (`authority.db.bak`).

### Backup

```bash
cp ~/.carryall/authority.db ~/.carryall/authority.db.bak
```

### Audit Archival

Move entries older than 1 year to the archive table:

```bash
carryall audit archive --older-than 365d --yes
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CARRYALL_API_KEY` | (none) | Bearer token for HTTP API auth. If unset, no auth required. |
| `CARRYALL_LOG_LEVEL` | `INFO` | Logging level: DEBUG, INFO, WARNING, ERROR |
| `CARRYALL_LOG_FORMAT` | `json` | Log format: `json` (structured) or `text` (human-readable) |
| `CARRYALL_RATE_LIMIT` | `100` | Max requests per minute per IP |
| `CARRYALL_DB` | `~/.carryall/authority.db` | SQLite database path |
| `CARRYALL_KEYS_DIR` | `~/.carryall/keys` | Agent keypair directory |
| `CARRYALL_CREDENTIALS_DIR` | `~/.carryall/credentials` | Issued credentials directory |
| `CARRYALL_SLOS_CONFIG` | (none) | Path to SLOS integration config (JSON) |
| `OPENAI_API_KEY` | (none) | OpenAI API key for policy compiler |
| `ANTHROPIC_API_KEY` | (none) | Anthropic API key for policy compiler |
