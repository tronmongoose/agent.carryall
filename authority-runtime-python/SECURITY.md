# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.5.x   | Yes       |
| 0.4.x   | No        |
| < 0.4   | No        |

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
- **TTL bounds** -- envelopes expire between 60 seconds and 24 hours (`validation.py`)
- **SHA-256 hash chain** on audit trail entries for tamper detection
- **Fail-closed constraints** -- an empty constraints dict is refused, not skipped
- **Positive model allowlist** -- exact provider and model IDs, with every refusal audited
- **Validated egress** -- one path, DNS resolved once, connections pinned to that answer
- **Parameterized SQL queries** throughout (no string interpolation)
- **Explicit transactions** with `BEGIN IMMEDIATE` for atomic audit writes
- **WAL mode** for crash-safe SQLite operations

## Threat Model

This section states what the runtime defends, what it does not, and where the
boundaries actually sit. It names its own gaps on purpose. A reader who assumes
more protection than this list describes will deploy it wrong.

### Assumed deployment

One trusted operator on one machine, running their own agents. Everything runs
in one process tree under one OS user. A hostile co-tenant is out of scope.
Multi-tenant use is not supported, and that is a design limit rather than a gap
waiting on test coverage.

### Who can read what

- **The OS user is the real boundary.** Any process running as that user can
  read the agent keys, the audit database, and the vault files. File
  permissions are the only barrier between agents on the same machine.
- **Agent keys** are 32-byte Ed25519 seeds in `~/.carryall/keys`, directory
  mode `0o700`, files mode `0o600`, unencrypted. There is no passphrase and no
  hardware backing. A read of that directory is a full compromise of every
  agent identity it holds.
- **The audit database** is unencrypted SQLite at `$CARRYALL_DB`. Anyone who
  can read it sees every recorded action, resource URI, and decision reason.
  Anyone who can write it can rewrite history (see below).
- **Vault document bodies** are encrypted at rest with age-x25519 when the
  vault's sensitivity calls for it (`crypto.py`). **Frontmatter stays
  plaintext** so metadata queries keep working. Titles, tags, and other
  frontmatter fields are readable by anyone with file access.
- **Approval prompts** leave the machine. The ntfy and Telegram channels carry
  the agent ID, action, resource URI, and purpose to a third-party service.

### Scopes are not a tenant boundary

Scope strings are a least-privilege aid, not an isolation mechanism. Concretely:

- **Enforcement is in-process and cooperative.** `check_envelope` and
  `check_access` only run when the calling code calls them. An agent that skips
  the call is not stopped, because the library lives inside the agent's own
  process. This is a guardrail against mistakes, not a sandbox against intent.
- **Authority is self-signed.** An envelope is signed with the agent's own key,
  and the agent holds that key. An agent can mint an envelope granting itself
  any scope, and the signature verifies. Signatures prove the envelope was not
  altered in transit. They do not prove the authority was legitimately granted.
- **The server-side data is the stronger check.** Document-level
  `denied_agents`, `requires_approval`, and `allowed_agents` in SLOS are keyed
  by agent ID and are not under the envelope's control. Where a decision must
  hold against a determined agent, it belongs there, not in a scope string.
- **Wildcards widen quietly.** `vault:*:read` matches every vault at that
  segment count. Review granted scopes as patterns, not as examples.
- Despite the wording in `vault_scope.py`, vault scoping does not isolate
  tenants. It namespaces one operator's own agents.

### Audit trail integrity is detective, not preventive

- The hash chain links entries by `prev_hash`. It detects an edit to a row that
  leaves the rest of the chain in place.
- **There is no signature and no external anchor.** Anyone who can write the
  database can rewrite every row and recompute every hash. The chain then
  verifies cleanly. Tamper evidence depends on the file being harder to write
  than to read, which on one machine under one user it is not.
- **Truncation is a warning, not a failure.** `verify_audit_chain` reports
  missing IDs in `gaps` and still returns `valid: True`. Deleting the tail of
  the log is not reported as invalidity. Callers that care must inspect `gaps`.
- Runtime decisions (egress, model policy) are recorded as unsigned system
  entries with `signature_valid=False`. That flag is accurate. It means no
  envelope signature existed to verify, not that verification failed.

### Egress, model policy, and rule packs

- **Egress** (`egress.py`) resolves each destination once, rejects the whole
  answer set unless every address passes, and connects to the pinned addresses.
  It always denies link-local, cloud metadata addresses and names, multicast,
  unspecified, reserved, private ranges, IPv6 forms that embed IPv4, and
  non-canonical IPv4 literals. Redirects are refused. It does not follow
  proxies. It cannot protect a call site that does not use it.
- **Model policy** (`models/`) is a positive allowlist of exact IDs. A local
  additions file may add local-provider models only. The banned-vendor check is
  a second layer, not the control. Neither layer inspects model weights or
  verifies what a provider actually serves for a given ID.
- **Rule packs** (`rule_packs/builtin.py`) match command and path strings
  textually. They catch the literal forms they name. They cannot see through
  shell variables, `eval`, base64, or any other indirection, and they do not
  intercept syscalls. Enforcement happens only where a caller invokes
  `enforce_point` or shells out to `carryall rules check`.

### The MCP HTTP server

- **It is unauthenticated when `CARRYALL_API_KEY` is unset.** The server logs a
  warning and serves every endpoint. Setting that variable is required, not
  optional, for any listener reachable by another process or host.
- The bearer token is compared with `!=`, which is not constant time.
- There is no TLS. Deploy behind a reverse proxy that terminates it.
- Rate limiting is per peer address and in-memory. It resets on restart and is
  not a defense against a distributed caller.

### What is in this repository and not enforced

- The `policies/*.rego` files are **not evaluated**. There is no OPA client and
  no `opa` invocation anywhere in this package. The 5-tier precedence ladder is
  reimplemented in Python in `backends/slos.py`, and the CLI describes it in OPA
  terms for familiarity. Editing a `.rego` file changes nothing at runtime.
- `sentinel/scorer.py` in the parent repository has no callers.

### What this does not defend against

- A compromised host, or any process running as the same OS user
- An agent that declines to call the enforcement functions
- An agent misusing authority it was legitimately granted
- Prompt injection. `_detect_prompt_injection` logs and does not block
- A malicious or backdoored model, or a provider serving different weights
- Supply-chain compromise of this package or its dependencies
- Side channels, including timing, and traffic analysis of approval channels
- Denial of service against the local SQLite database or the MCP listener
- Key theft, key rotation, and revocation, none of which are implemented

## Known Limitations

- **SQLite only** -- not yet suitable for distributed deployments
- **No TLS** -- the MCP HTTP server does not terminate TLS. Deploy behind a reverse proxy
- **Bearer token auth** -- API authentication is simple Bearer token, not OAuth2/OIDC,
  compared without constant-time semantics, and absent entirely when unconfigured
- **No key rotation** -- agent keys do not have built-in rotation or expiration
- **Audit trail unencrypted** -- entries are stored as plaintext in SQLite, with no
  signature over the chain head. Vault document bodies are encrypted. Frontmatter is not
- **Single-tenant** -- scopes namespace one operator's agents and do not isolate tenants
