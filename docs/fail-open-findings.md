# Fail-open findings (2026-09-24)

Two defects found while writing the SECURITY.md threat model (sl-gzlb). Both are
documented as limits in `authority-runtime-python/SECURITY.md`. Neither is fixed.
Both contradict the fail-closed posture the rest of the control plane now holds.

The bead database lives outside this repo, so these are written here rather than
filed. Suggested bead titles are given per finding.

---

## 1. The MCP HTTP server serves every endpoint unauthenticated, on every interface

Suggested bead: `fix(mcp): refuse to serve HTTP without CARRYALL_API_KEY`

### Where

| What | Location |
|---|---|
| Auth is optional | `authority-runtime-python/src/authority_runtime/mcp_server.py:1492-1497` |
| The check that gets skipped | `mcp_server.py:1515` (`if api_key:`) |
| Bind address default | `mcp_server.py:1482`, `mcp_server.py:1648` |
| CLI default | `authority-runtime-python/src/authority_runtime/cli.py:1513` |

### What happens

`api_key = os.environ.get("CARRYALL_API_KEY")` is read with no default. When it
is unset the server logs one warning and starts. The auth middleware then runs
`if api_key:` around the entire bearer-token check, so with no key configured
every request is authorized.

The default host is `0.0.0.0` at all three layers, so the listener is reachable
from any interface, not just loopback. The startup banner prints
`Auth: DISABLED`, which is accurate and easy to miss in a log.

The exposed surface is the full JSON-RPC tool set, including
`carryall_compile_policy` (mints signed envelopes) and `carryall_read_document`.

### Repro

```bash
unset CARRYALL_API_KEY
carryall mcp serve --transport http --port 8765
curl -s -XPOST localhost:8765/rpc -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

The tool list returns without credentials. No approval is requested and the
request is authorized rather than refused.

### Why it matters

Every other unconfigured path in this control plane now denies: an empty
constraints dict, an unresolvable model, a destination that fails validation, a
missing model-allowlist file. This one allows. A missing environment variable
is exactly the condition that should fail closed, because it is the state a
fresh machine, a new shell, or a broken unit file lands in.

### Proposed fix

- With `--transport http` and no `CARRYALL_API_KEY`, refuse to start and exit
  non-zero naming the variable. Do not serve.
- Change the default bind to `127.0.0.1`. Binding all interfaces should be an
  explicit `--host 0.0.0.0`.
- Compare the token with `hmac.compare_digest`. The current `!=` at
  `mcp_server.py:1517` is not constant time.
- Keep `/health` and `/healthz` unauthenticated, as they are today.
- Optional escape hatch, if a keyless loopback listener is genuinely wanted:
  an explicit `--insecure-no-auth` flag that also forces the loopback bind. An
  explicit flag is auditable. An unset variable is not.

### Cost

Small. One guard in `run_http`, one default change, one import. The default
change is a breaking change for anyone relying on the all-interfaces bind, so it
wants a CHANGELOG entry and a minor version bump.

---

## 2. `verify_audit_chain` reports missing rows and still returns valid

Suggested bead: `fix(storage): deleted audit rows must fail verification`

### Where

`authority-runtime-python/src/authority_runtime/storage.py:862`

```python
"valid": True if not gaps else True,  # gaps are warnings, not invalidity
```

### What happens

Both branches of the conditional evaluate to `True`. The expression cannot
return anything else, so the comment describes a decision the code does not
implement in any distinguishable way. The function detects gaps in the
auto-increment sequence, records them in `gaps`, and then reports the chain as
valid regardless.

Deleting whole rows is the cheapest way to tamper with this log. Because
`prev_hash` links to the previous surviving row, deleting a contiguous run from
the tail leaves a chain that recomputes cleanly. Deleting from the middle
leaves a gap that this function finds and then forgives.

Any caller that trusts the `valid` field, rather than separately inspecting
`gaps`, will report an intact audit trail over a truncated one. Callers today:

- `carryall audit --verify` in this repo (`cli.py`)
- three in bjornswarm, which were not reviewed for this:
  `agents/sentinel/organs/audit_chain_verify.py`,
  `agents/sentinel/organs/probes/audit_chain_integrity.py`, and
  `dashboard/server.py`

### Repro

```python
store.save_audit_entry(entry)   # x3
# then, with any sqlite client:
#   DELETE FROM audit_trail WHERE id = 2;
result = store.verify_audit_chain()
# result["valid"] is True, result["gaps"] is [(2, 3)]
```

### Why it matters

SECURITY.md now states plainly that the chain is detective and not preventive,
and that anyone who can write the database can rewrite it. That remains true
after this fix. The narrower claim worth keeping honest is the one the API
makes: `valid: True` should not be returned for a log with rows missing.

### Proposed fix

- Return `valid: False` when `gaps` is non-empty, and keep `gaps` populated so a
  caller can tell truncation from a hash mismatch.
- Add a distinct field rather than overloading `error`, for example
  `failure: "gap" | "hash_mismatch" | "prev_hash_mismatch"`.
- Audit archival (`archive_audit_entries`) legitimately removes rows. Check
  whether it leaves gaps that would now read as tampering, and if so record the
  archived range so verification can account for it. This is the part that makes
  the change more than a one-line edit.
- Review the three bjornswarm callers listed above once the semantics change.
  That is a separate session under the repo-boundary rule.

### Cost

Medium, because of the archival interaction. The one-line change is trivial. Not
breaking the archive path is the actual work. Tests exist in
`authority-runtime-python/tests/test_hash_chain.py` and
`tests/test_archive.py` and will need cases for both.
