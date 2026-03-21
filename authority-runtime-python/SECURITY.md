# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.3.x   | Yes       |
| 0.2.x   | No        |
| < 0.2   | No        |

## Reporting a Vulnerability

If you discover a security vulnerability in Authority Runtime, please report it responsibly.

**Email**: security@authority-runtime.dev

Please include:
- Description of the vulnerability
- Steps to reproduce
- Impact assessment
- Suggested fix (if any)

We will acknowledge receipt within 48 hours and provide an initial assessment within 5 business days.

## Security Architecture

Authority Runtime's security model is based on:

- **Ed25519 cryptographic signatures** on all authority envelopes
- **Parent-child subset enforcement** -- child envelopes cannot exceed parent permissions
- **TTL bounds** -- envelopes expire between 60 seconds and 24 hours
- **SHA-256 hash chain** on audit trail entries for tamper detection
- **Parameterized SQL queries** throughout (no string interpolation)
- **Explicit transactions** with `BEGIN IMMEDIATE` for atomic audit writes
- **WAL mode** for crash-safe SQLite operations

## Known Limitations

- **SQLite only** -- not yet suitable for distributed deployments
- **No TLS** -- the MCP HTTP server does not terminate TLS; deploy behind a reverse proxy
- **Bearer token auth** -- API authentication is simple Bearer token, not OAuth2/OIDC
- **No key rotation** -- agent keys do not have built-in rotation or expiration
- **No encryption at rest** -- audit trail data is stored unencrypted in SQLite
