\# Claude Code Project Guide  
Authoritative execution policy. Drop in project root.

Updated: 2026-02-01

\---

\#\# 1\. Project Philosophy  
\- Action Over Tracking: every feature must work end-to-end  
\- Privacy Absolute: local-first; no cloud data without explicit consent  
\- Verifiable Trust: actions auditable locally; user controls retention/export

\---

\#\# 2\. Execution Doctrine

\#\#\# Skills vs MCP  
\- Skills-first by default  
\- Use MCP servers only for:  
  \- network / API access  
  \- third-party integrations  
  \- long-running automation  
  \- privileged system actions

\#\#\# Tools Before Code  
Before coding:  
\- read relevant files  
\- search for existing utilities and patterns  
\- verify reality (types, routes, schema, config)

\#\#\# Clarification Rule  
\- Do not ask questions unless missing info would change architecture, security, data model, or user-visible behavior  
\- Otherwise: proceed with explicit assumptions

\---

\#\# 3\. The Ten Commandments  
1\. Read, search, and verify before coding  
2\. No clarifying questions unless high-impact  
3\. Write clear, obvious, boring code  
4\. Be brutally honest about what works vs exists  
5\. Preserve context; document why when changing  
6\. Make atomic, reversible commits  
7\. Document the WHY, not narration  
8\. Test before declaring done  
9\. Handle errors explicitly  
10\. User data is sacred

\---

\#\# 4\. Repo Birth Checklist  
\- Location: \~/code/\<scope\>/\<project\>  
\- git init immediately  
\- Add: README.md, .gitignore, .env.example  
\- Add: /docs/plan.md, /docs/adr/  
\- Install / run / test works locally  
\- Commit: chore: scaffold

\---

\#\# 5\. Planning Protocol

\#\#\# When to Plan  
Plan first if any are true:  
\- 3+ files involved  
\- new data model  
\- security or auth boundary change  
\- refactor or redesign  
\- ambiguity affects architecture

\#\#\# Plan Template  
Use this structure in /docs/plan.md:

\- Goal    
  One sentence

\- Current State    
  What exists now (honest)

\- Proposed Changes    
  1\. \<file/module\>: \<change\> — \<why\>

\- Risks    
  \- \<risk\> — \<mitigation\>

\- Validation    
  \- fresh install works    
  \- core flow end-to-end    
  \- tests pass    
  \- error paths exercised

\---

\#\# 6\. Implementation Workflow

\#\#\# Interface Lock (no parallelism before this)  
\- module boundaries  
\- types and interfaces  
\- IO contracts (API, events, DB schema)

\#\#\# Parallel Work (after lock; no shared files)  
1\. Component / core logic  
2\. Types / DTOs  
3\. Hooks / utilities  
4\. Styles / UI (if applicable)  
5\. Tests  
6\. Integration (routing, exports)  
7\. Config / docs  
8\. Validation

\#\#\# Rules  
\- Minimal deviation from existing patterns  
\- Preserve naming and structure  
\- No placeholder business logic  
\- Scaffolding stubs allowed only if runnable and tracked

\---

\#\# 7\. Quality Gates

\#\#\# Done Means  
1\. Ran as a fresh user  
2\. Core feature works end-to-end  
3\. First 5-minute failures handled  
4\. Claims match reality

\#\#\# Language Discipline  
\- Separate exists vs works  
\- Never say production-ready without verified end-to-end use

\---

\#\# 8\. Metrics & Performance Integrity  
\- Clearly label simulated vs real metrics  
\- Build real integration before tracking  
\- Simulation allowed only if explicit, minimal, temporary  
\- Never use simulated metrics for validation or claims

\---

\#\# 9\. Dependency Policy  
\- Prefer stable versions  
\- Avoid N.0.0 when possible  
\- Use latest only for security fixes or required features  
\- Document version choice when non-default

\---

\#\# 10\. Error Handling & Safety  
\- Graceful failure for low-stakes actions  
\- Fail closed for auth or high-stakes actions  
\- Local logging only unless explicitly consented  
\- Human verification for irreversible or financial actions

\---

\#\# 11\. Assumption Tagging

Required only for:  
\- external dependencies  
\- timing or race conditions  
\- auth or security boundaries  
\- persistence consistency  
\- money or irreversible actions  
\- non-determinism (LLMs, retries)

Tag format (inline in code comments):  
\- \#COMPLETION\_DRIVE: \<assumption\>  
\- \#SUGGEST\_VERIFY: \<defensive fix\>

Verification pass:  
Resolve all COMPLETION\_DRIVE assumptions with defensive code or tests.  
Do not remove features or change intent.

\---

\#\# 12\. Anti-Patterns  
\- planning without reading code  
\- parallelizing before interfaces are locked  
\- over-engineering  
\- “this should work” without running it  
\- deleting context instead of documenting  
\- fake or simulated validation metrics

\---

\#\# Final Rule  
Write code as if the next maintainer’s life depends on it.

---

## 13. Quality Gates & Release Pipeline

Carryall went from v0.1.0 → v0.5.0 with the `CI` workflow failing on every commit on `main` and the PyPI publish workflow trusting humans to verify CI themselves. v0.5.0 shipped to PyPI on a red-CI commit. This section captures the gates put in place after that incident, and the policies that make them real.

### Required CI checks (gating)

On every push/PR to `main`, the `CI` workflow runs:

| Job | Status | Notes |
|-----|--------|-------|
| `test (3.10)` `test (3.11)` `test (3.12)` `test (3.13)` | **required** | Drop or add a Python version → edit the matrix in `ci.yml` AND the protection rules below. |
| `lint` (ruff) | **required** | Default ruleset (`E`/`F`/`W`). Tightening to `I`/`UP`/`B` is a known follow-up: ~400 pre-existing errors of import-sort drift. |
| `typecheck` (mypy) | **advisory, not required** | 97 pre-existing errors in foundation modules (`storage`, `langgraph`, `cli`, `mcp_server`, `__init__`). New code is held to a mypy-clean bar; legacy debt is worked down separately. |

**New code must be mypy-clean** even though the foundation isn't. The four v0.5.0 port packages (`skill_loader`, `harness_audit`, `rule_packs`, `router`) are clean and that bar holds.

### Branch protection on `main`

Configured via `gh api -X PUT repos/.../branches/main/protection`. To inspect:

```bash
gh api repos/tronmongoose/agent.carryall/branches/main/protection
```

Active rules:
- Required status checks: `test (3.10/3.11/3.12/3.13)` + `lint`. Strict mode (branch must be up to date).
- `required_linear_history: true` — no merge commits. Use `git merge --ff-only` or rebase.
- `allow_force_pushes: false`, `allow_deletions: false`.
- `enforce_admins: false` — admin can override-merge in genuine emergencies. Document why in the commit if you do.
- No PR-review requirement (solo project); direct push to main is allowed when CI is green.

**Adding a Python version to the matrix** also requires adding it to `required_status_checks.contexts` in the protection rules and to the `REQUIRED` array in `publish.yml`'s verify step. Three places, easy to miss; grep for the version string.

### PyPI publish gate

`.github/workflows/publish.yml` runs on `release: published` and **before any build/upload step** polls the check-runs API for the release SHA:

- Up to 30 minutes of polling (60 × 30s).
- Required checks: same as branch protection (test matrix + lint).
- Any `failure`/`cancelled`/`timed_out` → publish refuses with a clear error.
- All required checks `success` → proceeds to `python -m build` and `pypa/gh-action-pypi-publish`.

If you need to ship a release on a commit where typecheck failed (the documented backlog), that's allowed by design. If lint or any test job failed, publish refuses — fix the commit, don't bypass the gate.

### Local CI parity

`Makefile` at the repo root mirrors CI exactly. `make check` locally and green CI on the same commit mean the same thing.

```bash
make install        # Create authority-runtime-python/.venv, install -e .[dev]
make lint           # ruff check src/ tests/   (CI parity)
make typecheck      # mypy src/                (CI parity)
make test           # pytest                   (CI parity)
make check          # lint + typecheck + test
make precommit-install
```

`.pre-commit-config.yaml` runs `ruff --fix` and `ruff-format` on staged Python files. Catches the F401 class of failure before CI does. Mypy intentionally not in pre-commit (too slow / env-dependent).

### Release ritual

1. `make check` locally. All green except possibly typecheck (legacy).
2. Bump `version` in `pyproject.toml`, `__version__` in `__init__.py`, and the three assertions in `tests/test_archive.py::TestVersionConsistency`. Add a CHANGELOG entry.
3. Commit, push to `main`. Wait for CI.
4. Once `test (3.10-3.13)` + `lint` are green: `git tag -a vX.Y.Z -m "..."`, `git push origin vX.Y.Z`.
5. `gh release create vX.Y.Z --title "..." --notes-file notes.md --verify-tag`.
6. `publish.yml` triggers, verifies CI, publishes to PyPI via OIDC trusted publishing.
7. If the publish workflow refuses: read its log. Don't delete and recreate the release — fix the underlying commit, retag, restart.

### Open backlog (tracked, not blocked)

- **Mypy foundation cleanup.** `mypy src/` reports 97 errors across 14 files; none in new port packages. Working down by module is the right shape: pick a file, fix to clean, move on. Don't try to tackle all at once.
- **Ruff ruleset hardening.** Adding `select = ["E", "F", "I", "UP", "B", "W"]` surfaces ~400 pre-existing errors (mostly import-sort `I`). Use `ruff check --fix` for the 67-ish auto-fixable, then iterate on the rest by category.
- **README badges drift.** Test count badge has been wrong for several releases; CI badge missing entirely. Add `https://github.com/tronmongoose/agent.carryall/actions/workflows/ci.yml/badge.svg`.
- **`CLAUDE.md` markdown escaping.** This file's earlier sections contain literal `\#` and `\---` from a paste that escaped markdown. Headings won't render in any viewer. Either reformat this file in clean markdown or accept that it's plaintext-only.

### Lessons carried forward

- **Quality gates only matter if they gate something.** A failing CI check that doesn't block anything is decoration. Either require it for merge/publish, or remove it from CI. The middle ground ("we'll watch the failures") is what got Carryall to v0.5.0 with five months of red main.
- **Honesty over green.** When a legacy backlog can't be cleared in scope, document it in CHANGELOG and adjust the gate to exclude it. Don't pretend a check is enforced when it isn't.
- **Local parity beats local convention.** Developers run different commands than CI runs → drift. A Makefile that wraps the literal CI invocations costs nothing and prevents this.
- **The release workflow must verify its preconditions.** Trusting the human cutting the release to have checked CI is exactly how v0.5.0 shipped with a red main. Self-verifying workflows refuse to ship broken code even when the human says "go."
  
