# Contributing to Authority Runtime

## Development Setup

```bash
git clone https://github.com/tronmongoose/agent.carryall.git
cd agent.carryall/authority-runtime-python
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Running Tests

```bash
# All tests
pytest

# Specific test file
pytest tests/test_envelope.py -v

# With coverage
pytest --tb=short -v
```

## Code Style

- **Formatter**: black (line length 100)
- **Linter**: ruff (line length 100)
- **Type hints**: Required for public API functions
- **Python**: 3.9+ compatible

Run before committing:
```bash
ruff check src/ tests/
black src/ tests/ --check
```

## Pull Request Process

1. Fork the repository
2. Create a feature branch from `main`
3. Write tests for new functionality
4. Ensure all 182+ tests pass
5. Run ruff and black
6. Submit PR with clear description

## Commit Messages

Use conventional commit style:
- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation
- `test:` Test changes
- `refactor:` Code refactoring

## Architecture

Key modules:
- `envelope.py` -- Envelope creation, validation, Ed25519 signing
- `enforce.py` -- Runtime permission enforcement (`EnforcedTool`)
- `storage.py` -- SQLite persistence, migrations, hash chain
- `policy.py` -- YAML policy engine
- `compliance.py` -- HTML compliance reports, attestations
- `mcp_server.py` -- MCP server (HTTP + stdio)
- `cli.py` -- CLI commands

## License

By contributing, you agree that your contributions will be licensed under the Business Source License 1.1.
