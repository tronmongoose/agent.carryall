# Carryall Plugin — Dogfood Log

Findings from building Fast Forward agents on top of `tronmongoose/agent.carryall_plugin` (v0.3.1). This is a living document — add to it as implementation continues.

**Repo assessed:** https://github.com/tronmongoose/agent.carryall_plugin
**Assessment date:** 2026-03-29
**Consumer project:** Fast Forward Tech GTM agents (M1/M2/M5)

---

## Overall Verdict

The architecture is right. Control plane / data plane separation, deterministic policy decisions, Ed25519 signed envelopes, least-privilege scoping — all of this is sound and exactly what agent systems need. The gap is between the design and what's actually shipped. The plugin today is a working proof of concept with the right bones, but it's not production-ready for someone other than its author to deploy.

The good news: every gap below is fixable, and building FF agents is the forcing function to fix them.

---

## Priority Ranked Improvements

### P0 — Blocking Production Use

**1. authority_runtime is not publicly installable**
- **RESOLVED (2026-04-01).** `agent.carryall` repo made public under BSL 1.1. Installable via:
  ```
  pip install -e "git+https://github.com/tronmongoose/agent.carryall.git#subdirectory=authority-runtime-python&egg=authority-runtime"
  ```
- Not on PyPI yet. The git+https install path is long and fragile for CI/CD.
- **Remaining fix:** Publish to PyPI as `authority-runtime` (even as 0.x alpha) for clean `pip install authority-runtime`.

**2. Zero test coverage**
- No tests/ directory, no pytest config, no CI
- demo.py is the only validation, and it's a walkthrough, not automated assertions
- **Impact:** No way to know if a change breaks something. Can't refactor safely. Can't accept contributions.
- **Fix:** Add unit tests for: envelope creation/expiry, scope validation, cross-scope denial, key generation, PolicyDecisionPoint decisions. Use the FF enforcement tests as a starting template — they already cover the patterns.

**3. No CI/CD pipeline**
- No GitHub Actions, no pre-commit hooks, no automated anything
- 4 commits, all manual
- **Impact:** Every release is a trust-me. No regression gates.
- **Fix:** Add a basic GitHub Actions workflow: lint, test, type-check on PR.

### P1 — Blocks Template Reuse (Other Clients After FF)

**4. MemoryBackend is demo-only — no production backend exists**
- MemoryBackend is an in-memory dict. Fine for demo. Useless for any real system.
- FF agents need to check against real filesystem paths. We built our own `check_vault_access()` in enforcement.py because MemoryBackend doesn't do this.
- **Impact:** Every consumer has to write their own access-check logic. That's the hardest part to get right and the most dangerous to get wrong.
- **Fix:** Ship a `FilesystemBackend` (or at least an ABC + reference implementation) that checks real paths against envelope scopes. The pattern from FF's enforcement.py is a starting point.

**5. No decorator / lifecycle pattern for agent functions**
- The create-envelope → check-access → execute → audit cycle is universal. Every agent function needs it.
- We built `enforce_envelope()` in enforcement.py. It works, but it should live upstream.
- **Impact:** Every consumer reimplements the same boilerplate. Inconsistent implementations = inconsistent security.
- **Fix:** Add `@enforce_envelope` (or similar) to authority_runtime. Accept a scope resolver, agent_id, TTL. Handle the full lifecycle including audit event emission.

**6. Scope strings are not parameterized**
- Current: `vault:finance:read` — hardcoded domain names
- FF needs: `vault:{client_slug}:read:context.json` — dynamic, path-scoped
- We build scope strings manually in VaultScope.scopes property
- **Impact:** Every multi-tenant consumer invents their own scope string format. No consistency, no validation.
- **Fix:** Support scope templates: `vault:{domain}:{operation}:{resource}` with validation. Could be as simple as a `ScopeTemplate` class with `.bind(domain="acme", resource="context.json")`.

### P2 — Should Fix Before v1.0

**7. OpenAI API key in .mcp.json — why?**
- The MCP config requires `OPENAI_API_KEY`. But the whole pitch is "deterministic, no LLM in the loop."
- Is the policy compiler using GPT-4o-mini for intent parsing? If so, that's a significant undocumented dependency and contradicts the deterministic claim.
- **Impact:** Confusing. Also means Carryall has a runtime dependency on a third-party paid API for its core function.
- **Fix:** Document why this is needed. If it's for intent parsing, make it optional (support both LLM-assisted and pure-deterministic modes). If it's not needed for the core path, remove it from required config.

**8. No formal MCP tool schemas**
- Tools are documented in natural language in SKILL.md
- No JSON Schema, no OpenAPI spec, no type definitions
- **Impact:** Integration requires reading prose and guessing. Can't auto-generate clients.
- **Fix:** Add JSON Schema definitions for each MCP tool's input/output. These likely already exist in the MCP protocol layer — just surface them in docs.

**9. No error taxonomy**
- What errors can `create_simple_envelope` throw? What about `check_access`?
- demo.py only shows happy paths
- **Impact:** Consumer code has to catch generic exceptions and guess at failure modes.
- **Fix:** Define error classes (EnvelopeExpiredError, ScopeDeniedError, InvalidSignatureError, etc.) and document when each is raised.

**10. No CHANGELOG or release notes**
- v0.3.1 is the only version. No history of what changed.
- **Impact:** Minor now, critical once there are multiple consumers.
- **Fix:** Add CHANGELOG.md. Start now while history is short.

### P3 — Nice to Have

**11. 300-second default TTL is short for batch operations**
- FF's proposal agent targets 60s, document agent 90s. 300s is fine.
- But batch processing (e.g., re-running all client ICPs) would need longer TTLs or envelope renewal.
- **Fix:** Document the TTL story. Add a renewal/refresh pattern if needed.

**12. No multi-tenant patterns documented**
- The demo shows named agents (finance-agent, startup-agent). These are single-tenant personas.
- FF needs per-client scoping where the "tenant" is dynamic.
- **Fix:** Add a multi-tenant example to the docs. FF's VaultScope pattern is a good reference.

**13. asciinema demo is nice but could be a real integration test**
- demo.cast and demo.gif show the workflow. Convert to a runnable test.

---

## What Works Well

These are worth calling out because they should be preserved and doubled down on:

1. **Architecture is right** — Control plane / data plane separation is the correct pattern. Don't change this.
2. **Ed25519 signing** — Real cryptographic binding, not JWT-lite. Good choice.
3. **Deterministic policy decisions** — PolicyDecisionPoint + BUILTIN_SCOPE_RULES is the right abstraction. Same input = same output, no LLM variance.
4. **Simple API surface** — generate_key_pair, create_simple_envelope, check_access. Easy to learn.
5. **Anthropic marketplace approval** — Distribution channel exists. Plugin manifest is clean.
6. **MIT license** — Correct choice for adoption.

---

## FF-Specific Workarounds Built

These are things we had to build in `carryall/enforcement.py` because the plugin doesn't provide them. Each is a candidate for upstream.

| Workaround | File | Lines | Upstream Candidate? |
|-----------|------|-------|-------------------|
| VaultScope dataclass | enforcement.py | VaultScope class | Yes — generalize as ScopeBinding |
| Dynamic scope string generation | enforcement.py | VaultScope.scopes | Yes — scope templates |
| check_vault_access() | enforcement.py | check_vault_access() | Yes — FilesystemBackend |
| enforce_envelope decorator | enforcement.py | enforce_envelope() | Yes — core lifecycle |
| Cross-client denial check | enforcement.py | in check_vault_access | Yes — multi-tenant support |

---

## Log

_Add entries as implementation continues._

| Date | Session | Finding | Priority |
|------|---------|---------|----------|
| 2026-03-29 | S0 | authority_runtime not installable | P0 |
| 2026-03-29 | S0 | No tests in plugin repo | P0 |
| 2026-03-29 | S0 | MemoryBackend demo-only, had to write own access check | P1 |
| 2026-03-29 | S0 | No decorator pattern, built enforce_envelope | P1 |
| 2026-03-29 | S0 | Scope strings not parameterized, built VaultScope | P1 |
| 2026-03-29 | S0 | OpenAI key required but "deterministic" claim | P2 |
| 2026-03-29 | S1-S3 | enforce_envelope can't be wired without authority_runtime installed — all 3 agents run unprotected | P0 |
| 2026-03-29 | S3 | Scoping bot vault creation needs Carryall-signed event — can't sign without runtime | P0 |
| 2026-03-29 | S3.5 | Ephemeral key generation per-call was wasteful — cached in decorator closure. Plugin should document key lifecycle best practices | P3 |
| 2026-03-29 | S4 | E2E integration test confirms full pipeline works without Carryall — enforcement is entirely opt-in right now. Should be opt-out (fail if missing) | P0 |
| 2026-03-29 | S4 | parse_json_response and load_prompt had to be built as shared utils. Plugin could ship a `call_and_parse()` helper that wraps LLM call + fence stripping for common agent patterns | P3 |
| 2026-03-30 | Multi-tenant review | skill_name="ff-{agent_id}" hardcoded — blocks multi-tenant. Need agent_prefix param in create_vault_envelope | P1 |
| 2026-03-30 | Multi-tenant review | agent_id default "ff-agent" in enforce_envelope — same issue | P1 |
| 2026-03-30 | Multi-tenant review | Key pairs are per-decorator not per-tenant. Shared process = shared keys. Need tenant-scoped key management | P2 |
| 2026-03-30 | Multi-tenant review | Envelope scope format vault:{slug}:read:{resource} works for multi-tenant but has no tenant namespace. Two tenants with same client slug = scope collision | P2 |
| 2026-04-03 | Pipeline refactor | enforce_envelope decorator doesn't integrate with AgentPipeline.run() lifecycle. Built _enforce_access() as inline method call. Decorator works for standalone functions; pipeline needs enforcement at a specific point in the lifecycle. Plugin should support both patterns. | P1 |
| 2026-04-03 | Pipeline refactor | Carryall graceful degradation works — CARRYALL_AVAILABLE flag, warning log, continues without enforcement. Good for dev/test. Production should flip to fail-closed once authority_runtime confirmed installed. | P2 |
| 2026-04-03 | Pipeline refactor | Pipeline key pair is per-instance (cached in __init__). Better than per-call but still not per-tenant. Key management story needs work for multi-tenant. | P2 |

---

## Deliverable 1 Summary (updated 2026-04-04)

**Built:** 3 agents (M1 Proposal, M2 Document, M5 Scoping Bot), AgentPipeline base class, enforcement wrapper, vault system, ReviewClient ABC (Notion + ClickUp + Stub), Slack notifications, TenantConfig system, CLI tools, E2E tests, Railway deployment.

**67 tests passing**, 14 skipped (enforcement tests need authority_runtime — should now pass with public repo).

**Review queue migration:** ClickUp → Notion completed. ABC pattern (ReviewClient) + factory (get_review_client) + REVIEW_BACKEND env var made the swap a config change, not a code rewrite.

**Deployment:** Railway via Procfile + runtime.txt + requirements.txt. Scoping bot live at web-production-5447c.up.railway.app.

**Biggest takeaway (updated):** authority_runtime is now installable from public repo (2026-04-01). The 14 skipped enforcement tests should be re-enabled. Remaining Carryall gap: no PyPI package, install path is fragile for CI/CD pipelines.

**Template value:** The patterns in `carryall/enforcement.py`, AgentPipeline base class, ReviewClient ABC, SlackNotifier, TenantConfig system, and vault_utils are all reusable for any future client project. Next client project should take ~40% less time.
