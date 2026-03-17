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
5\. Tests written for new or changed code

\#\#\# Language Discipline
\- Separate exists vs works
\- Never say production-ready without verified end-to-end use

\#\#\# Test Coverage Policy
\- Every new module MUST have a corresponding test file (test\_\<module\>.py)
\- Every new public function or class MUST have at least one test
\- Security-sensitive code (auth, signing, policy enforcement, input sanitization) MUST have both positive and negative tests
\- Bug fixes MUST include a regression test that fails without the fix
\- Run `pytest tests/ -q` before declaring any task done — all tests must pass
\- When modifying existing code, check if existing tests still cover the changed behavior; add tests if not
\- Test patterns to follow:
  \- Use fixtures for shared setup (keys, envelopes, temp dirs)
  \- Use `unittest.mock` for external dependencies (LLM APIs, network)
  \- Use `typer.testing.CliRunner` for CLI command tests
  \- Use `pytest.mark.asyncio` for async code
\- Coverage targets: aim for >70% line coverage on all modules; >90% on enforcement, signing, and policy modules

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
\- shipping new modules or features without tests
\- adding code to security-sensitive paths without negative tests (rejection, tamper, escalation)
\- marking a task done when tests are failing

\---

\#\# Final Rule  
Write code as if the next maintainer’s life depends on it.  
