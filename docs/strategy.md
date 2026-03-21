# Native Agent IAM - Agent Carryall

**The IAM + Context Control Plane for Autonomous AI Agents**

**Production implementation:** https://github.com/tronmongoose/agent.carryall/

### What It Is

Carryall is the identity, access, and context-control layer for autonomous AI agents. It addresses two production-blocking failures simultaneously:

1. Agents operate today with uncontrolled, over-privileged access, and
2. Token costs scale unsustainably as workflows become multi-step.

Carryall uses an LLM-powered policy compiler to derive the minimum permissions and minimum context required for each agent action, then enforces both inline via cryptographically signed execution envelopes and tamper-evident audit trails.

This is not "better prompts" or "metadata." It is decision-time understanding: the combination of inputs, intent, constraints, history, permissions, exceptions, and outcomes surrounding each real enterprise action. Carryall makes that decision logic explicit, bounded, and provable — turning agent execution into something **the CISO** can approve, **the CFO** can afford, and **the CTO** can adopt without architectural friction.

As of February 2026, Carryall includes a production Python authorization library, Ed25519 cryptographic identity, a Model Context Protocol (MCP) server, a CLI, Docker images (carryall:0.3.1, clawdbot:0.3.0), and Kubernetes deployment support. Seven agents run daily under Carryall's authority on personal financial, health, email, and project data — the same data categories regulated industries handle for their customers.

### Why This Must Exist Now

Enterprise AI deployment has outpaced security and governance infrastructure. For B2C, security and governance are non-existent.

Folks are shipping autonomous agents with God-mode access because no IAM system exists for non-human actors that reason, adapt, and take multi-step actions across tools and clouds. CISOs are already asking how agent behavior can be audited, permissions proven, and compliance demonstrated; current systems can store records, but they were never designed to capture why decisions happened as they unfolded. That gap is becoming the bottleneck for AI adoption.

This is not theoretical. In February 2026, a CFO at a growth-stage edtech company described the exact failure mode unprompted: their CEO is shipping Claude-powered agents with MCP integrations at speed, while the CFO has no visibility into what those agents can access, no audit trail, and no way to prove compliance with FERPA. They are buying a Mac Mini for local inference to avoid sending student data to cloud LLMs — but have no policy layer to govern what runs on it. This is one conversation. The pattern is everywhere.

At the same time, token economics are becoming a hard constraint. As agents evolve from single prompts to autonomous workflows, token usage has increased by orders of magnitude. To keep ROI viable, context must be narrowed aggressively. Carryall makes context compression a first-class control primitive, reducing operational cost while increasing security, converting agents from a governance risk into deployable infrastructure.

Early architectural testing confirmed that embedding policy logic inside individual agents does not scale in enterprise environments. Isolated policy state, fragmented audit trails, and redeployment-heavy updates create operational drag and security blind spots. Centralized, shared authorization infrastructure is not optional — it is a prerequisite for multi-agent systems.

## Production Dogfooding (Jan-Feb 2026)

### System Scale

| Metric | Value | Source |
| :---- | :---- | :---- |
| Total policy events | 522 | SLOS audit database |
| Agents in daily operation | 7 | Cron schedule (daily 9:00-9:30 AM) |
| Data domains governed | 7 | finance, health, personal, startup, community, email, meta |
| Days running clean | 6+ | Executive Digest streak counter |
| System health | GREEN | Zero unresolved alerts |

### Policy Compiler Performance

| Metric | Value | Source |
| :---- | :---- | :---- |
| Scope reduction (avg) | 68% | compile_policy dogfood runs |
| Compiler confidence | 91.5% | Dogfood metrics (8 test runs, 7 passed) |
| compile_policy cost | ~$0.001/run | OpenAI token tracking |
| compile_policy latency | ~2.1s | Dogfood avg |
| Least-privilege enforcement | Yes | Agents cannot exceed compiled authority |
| Audit coverage | 100% | Every action signed and logged |
| Enforcement latency | Inline | Auth does not block execution |

The 68% scope reduction means the LLM compiler removes two-thirds of the permissions an agent *could* request, giving it only what it needs. This is not rules-based filtering — the compiler understands intent.

### Deployability

| Element | Status |
| :---- | :---- |
| MCP authorization server | stdio (local) + HTTP (bridge) |
| CLI tooling | Key management, envelopes, audit queries |
| Docker images | carryall:0.3.1, clawdbot:0.3.0 |
| Kubernetes | Docker Compose deployed; Helm chart planned |
| Dashboard | Live — React + Python, 5-tab operational UI |
| Agent delivery | 7 agents, daily automated Telegram briefs |

### What 522 Events Taught Us

63% of what appeared to be security "denials" turned out to be routine agent behavior — metadata probes, vault enumeration, capability discovery. Without filtering, this noise makes agent monitoring unusable for humans.

The Executive Digest system we built on top of Carryall separates meaningful security events from operational noise — the difference between "75% denial rate" (alarming) and "2 meaningful denials out of 522 events" (healthy).

This is exactly the "decision-time understanding" that the thesis claims is the moat. Carryall does not just enforce policy — it generates the understanding needed to govern agents at scale.

### Know Your Agent: Cryptographic Identity + Signed Credentials

Carryall treats agents as first-class principals with cryptographic identity. Each action executes under an Ed25519-signed envelope binding

1. agent **identity**,
2. selected **skill**,
3. minimal **scopes** and resources,
4. **permitted context** fields, and
5. explicit time bounds (**TTL**)

Guarantees include signed agent credentials for tool and transaction access; provable attribution ("who or what executed this"); strict subset authority delegation (child ⊆ parent); tamper detection via signature invalidation; and time-bounded execution using TTLs and envelope IDs.

This is the difference between "an LLM did something" and "a provably bounded agent executed an authorized action."

In production, seven agents operate under distinct identities with enforced scope boundaries: finance-agent (154 requests, finance-only), executive-agent (98 requests, cross-domain read), health-agent (98 requests, health-only, no LLM access), email-agent (6 requests, personal/email scoped). Each has a YAML policy defining clearance level, rate limits, and explicit deny lists.

## What Makes the Moat Defensible

1. **Context as Decision-Time Understanding (Control Plane Moat):** Most systems store records; they do not capture decision logic. Carryall becomes the runtime system that binds intent, constraints, permissions, exceptions, and outcomes into a causal chain of "what was allowed and why," across every step of agent execution. This is critical governance substrate enterprises need to scale agent autonomy.

2. **Agent-Native Identity Propagation (Cross-System Moat):** Traditional IAM binds identity to static principals inside a single control plane. Agents traverse SaaS tools, cloud services, internal APIs, and delegated sub-agents in one workflow. Carryall preserves authority and auditability for a non-deterministic agent, creating cross-product continuity that incumbents and cloud providers are structurally disincentivized to deliver. Hyperscalers want identity/agents to stay inside their ecosystem.

3. **Cryptographic Enforcement + Signed Envelopes (Trust Moat):** Permissions are not advisory metadata; they are execution boundaries enforced with Ed25519 signatures, parent-child subset rules, and TTL semantics. Creates verifiable, portable trust across different systems.

4. **LLM Policy Compiler (Learning Moat):** Natural language intent → minimal enforceable permission sets and minimal context. Not rules-based IAM. The compiler understands that "read student financial aid records" and "read student grades" require different scopes from the same agent — something static rules cannot express. Production data improves safety/utility tradeoffs over time, increasing switching costs.

## Why Incumbents Will Not Win This Market

Traditional IAM vendors are structurally misaligned. Their systems assume static roles and predictable human behavior. Inserting LLM inference into authorization decisions would require a fundamental rewrite of their core architecture. Additionally, their go-to-market motion targets security teams, whereas Carryall is adopted by AI engineers first and pulled into compliance later, after it is already embedded.

This is confirmed by early conversations. The buyer is not the CISO — it is the technical co-founder or CTO who is already building with Claude/GPT and realizes they have a governance gap. The CFO validates the need; the builder adopts the tool. Auth0's playbook, not Okta's.

This is an AI-native identity layer. As Auth0 did not emerge from Active Directory, the IAM layer for agents will not emerge from Auth0, SailPoint, Saviynt or Okta.

## First Customer Signal

In February 2026, a CFO at a growth-stage edtech company described their governance gap unprompted: AI agents shipping fast with no IAM, no audit trail, FERPA exposure, and no visibility for the finance team. They are actively buying infrastructure (Mac Mini for local inference) to mitigate data residency risk — but have no policy layer. This is the exact wedge Carryall addresses.

## Bottom Line

Carryall is foundational infrastructure for the agent era. It reduces agent scope by 68% on average, enforces least-privilege execution cryptographically, and produces the audit trail enterprises and regulators require. Seven agents run daily under Carryall's authority on personal financial, health, and project data — the same data categories edtech companies handle for students and healthcare companies handle for patients.

The market is forming before defaults are set. Dogfooding validates both the technical and economic case. First external conversations confirm the pain point is real and unaddressed. Carryall is positioned to become the IAM control plane serious agent deployments depend on.

---

# Architecture and Flow Diagram

## Architecture

System Architecture

1. Purpose: converts "concept" into "deployable control plane."
2. Agent fleet -> HTTP + signed envelope -> Carryall service (HTTP :8765)
3. MCP tool surface: check_access, list_vaults, get_metadata, audit_log
4. Policy engine checks: signature, TTL, child ⊆ parent, scope/resource match, allow|deny|require_approval
5. Audit log fields captured per decision

## Learnings

Embedding the policy service inside each agent container does not support enterprise multi-agent systems. Observed failures include isolated policy state, fragmented audits, multiplied resource overhead, and redeployment for every policy change.

Carryall must run as a shared HTTP authorization service that all agents call. This enables centralized enforcement, unified auditability, immediate policy updates, and independent horizontal scaling.

## Authority Envelope Flow

A 5-step flow with a minimal example envelope payload: agent request -> policy compiler -> envelope creation/signing -> policy check endpoint -> bounded execution

## How This Will Be Distributed

Carryall ships developer-first and embeds early in agent architectures. Local development uses stdio-based MCP; production deployments use a shared HTTP Carryall service. This ensures authorization exists before formal security review.

Kubernetes-native deployment includes single-command installs, sidecar or shared-service patterns, ConfigMap-based configuration, secrets management for cryptographic material, and service-mesh compatibility.

The next phase introduces a managed service with compliance dashboards, audit exports, policy controls, and enterprise guarantees. Regulated industries adopt cloud-native first, followed by on-prem and air-gapped deployments for high-ACV environments.

**TL;DR: Developer-first adoption followed by centralized enterprise procurement.**
