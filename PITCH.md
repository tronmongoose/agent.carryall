# Authority Runtime - Product Pitch

## The Problem

AI agents are becoming the new enterprise workforce, but they operate with **dangerous, all-or-nothing permissions**:

- **Claude Code** gets access to your entire codebase, every file, every secret
- **OpenAI Assistants** can read/write everything in connected tools
- **Custom agents** run with full API keys and credentials

**This is insane.** You wouldn't give a new employee admin access to everything on day 1. Why do we do it with AI agents?

---

## The Market Gap

Three massive players are ignoring a $B opportunity:

1. **Anthropic, OpenAI, Google**: Building LLMs but no cross-platform IAM layer
2. **SailPoint, Okta**: Building human IAM, not touching agents yet
3. **AWS, Azure, GCP**: Cloud IAM doesn't work for multi-vendor LLM agents

**The gap**: No neutral, cross-platform identity & access layer for AI agents.

We're building the **Auth0/Okta for AI agents**.

---

## The Solution: Authority Runtime

A lightweight, AI-native IAM layer that **wraps any agent execution** with intelligent permission enforcement.

### How It Works

```typescript
// 1. Parent envelope: Full authority
const parentEnvelope = {
  agent_id: "claude-session-123",
  step: 1,
  authority: {
    scopes: ['read:user', 'write:user', 'send:email'],
    context: ['user_id', 'email', 'preferences', 'session_token']
  }
}

// 2. User request: "Find user by email john@example.com"

// 3. LLM compiler selects MINIMAL skill + permissions
const childEnvelope = await compilePolicy(
  parentEnvelope,
  "Find user by email john@example.com"
)

// Result:
childEnvelope.authority = {
  scopes: ['read:user'],  // 67% reduction from parent
  context: ['user_id', 'email']  // 50% reduction from parent
}
childEnvelope.skill = 'getUserByEmail'  // Only this skill exposed
```

**Core Invariant**: Authority and context **only ever narrow, never expand** as agents act.

Enforced by:
- ✅ Cryptographic signatures (Ed25519)
- ✅ Parent-child validation (child ⊆ parent)
- ✅ TTL expiration (time-bounded authority)
- ✅ Immutable audit trail

---

## The Economics (Why This Wins)

### Trojan Horse GTM: Lead with COGS, Upsell Governance

**Bottom-up (Developer adoption)**:
- "Install Authority Runtime to **reduce your LLM costs by 70-90%**"
- Token reduction from narrowed context = immediate ROI
- **Self-funding**: Token savings > product cost (no budget approval needed)
- Developers adopt for cost, discover security benefits

**Top-down (Enterprise upsell)**:
- "Your agents are running with admin access everywhere"
- Compliance requirements (SOC2, HIPAA, FedRAMP) demand agent IAM
- Multi-vendor LLM strategy requires neutral governance layer
- Audit trail for "what did our agents access?"

### Pricing Model (Flexible)

```
Tier 1: Developer ($0/month)
- Open source core
- Local LLM for policy decisions
- Bring your own API key

Tier 2: Startup ($99/month)
- Hosted policy compiler (we run the LLM)
- 100k decisions/month included
- Shared audit dashboard

Tier 3: Enterprise (Custom)
- Dedicated policy compiler
- SSO integration (Okta, Azure AD)
- Compliance reports (SOC2 audit trail)
- Resource-level scoping (user:123:bio)
- SLA guarantees
```

**Key**: You can start free (BYO key) and upgrade as you need hosted/compliance features.

---

## Competitive Moat

### Why Anthropic/OpenAI Won't Build This

- **Not their business model**: They sell LLM tokens, not governance
- **Cross-platform is antithetical**: They want lock-in, we want neutrality
- **Example**: Anthropic won't build a tool that also works for OpenAI/Gemini

### Why SailPoint Won't Build This

- **Wrong DNA**: Human IAM company, not AI-native
- **Too slow**: Traditional enterprise software cycles (18-month releases)
- **Legacy tech**: Won't use LLMs to make IAM decisions (we do)

### Why Hyperscalers Won't Build This

- **Single-cloud only**: AWS IAM doesn't work for Azure + GCP + Anthropic + OpenAI
- **Multi-cloud enterprises** (same reason they use Okta instead of AWS IAM)
- **We're Switzerland**: Neutral layer every vendor needs

---

## What We've Built (2 Days of Work)

✅ **Core Authority Envelope System**
- Cryptographic signing (Ed25519)
- Envelope creation, validation, narrowing
- 10 passing tests
- Demo: 67% authority reduction, 50% context reduction

✅ **LLM-Based Policy Compiler**
- Pluggable LLM interface (swap providers easily)
- Anthropic Claude Haiku implementation
- OpenAI GPT-4o-mini implementation
- POC: 100% skill selection accuracy, $0.0001/decision

✅ **Cross-Platform Architecture**
- Universal envelope format
- Works with Claude, OpenAI, Gemini
- Provider-agnostic skill definitions

---

## The Demo

**Scenario**: Multi-step agent workflow

1. **Step 1**: Parent envelope with full authority (5 skills, 10 scopes)
2. **User request**: "Find user by email"
3. **LLM compiler**: Selects `getUserByEmail` skill, narrows to `read:user` scope
4. **Step 2**: Child envelope (1 skill, 1 scope) - 80% reduction
5. **Validation**: Signature verified, authority narrowing enforced
6. **Result**: Agent executes with minimal permissions, audit trail recorded

**Token savings**: ~70% reduction from context filtering
**Cost per decision**: $0.0001 (self-funding at scale)
**Latency**: ~3s (acceptable for MVP, will optimize)

---

## Next Steps (5-Week MVP for Ribbit)

**Week 1** (Complete): Core envelope + LLM compiler ✅
**Week 2**: Claude Code integration + dogfooding
**Week 3**: OpenAI adapter + token measurement dashboard
**Week 4**: Side-by-side comparison (with vs without Authority Runtime)
**Week 5**: Pitch deck + GitHub repo launch

**Ribbit Meeting**: End of January 2025

---

## Why This Works

### Network Effects
- More skills in registry → more useful for developers
- More developers → more skill contributions
- More enterprises → stronger compliance story

### Timing
- AI agents are **exploding** (Claude Code, OpenAI Assistants, AutoGPT)
- Security hasn't caught up yet (still all-or-nothing permissions)
- Regulatory pressure incoming (EU AI Act, SOC2 for agents)

### Founder-Market Fit
- Background: SailPoint (identified agent IAM problem years ago)
- Thesis: "This is the next generation identity/access solution for agents"
- No one else is building cross-platform neutral layer

---

## The Ask

We're looking for early design partners to validate:

1. **Is 70-90% token reduction compelling enough for adoption?**
2. **Do enterprises care about agent governance yet, or is it too early?**
3. **What compliance requirements would you need for SOC2/HIPAA?**
4. **Resource-level scoping (user:123:bio) vs action-level (write:user)?**

**Ideal design partners**:
- Companies running multi-step agent workflows
- Using multiple LLM providers (Claude + OpenAI)
- Compliance requirements (SOC2, HIPAA, FedRAMP)
- Token bills >$10k/month (meaningful savings)

---

**Contact**: [Your info]
**Demo**: `npm run demo:llm` (5 minutes to run)
**Repo**: [GitHub link - coming Week 5]

---

## Appendix: Technical Deep Dive

### Authority Envelope Format

```typescript
interface AuthorityEnvelope {
  version: string;
  envelope_id: string;
  agent_id: string;
  provider: 'claude' | 'openai' | 'gemini' | 'custom';
  step_number: number;
  parent_envelope_id?: string;
  root_policy_id: string;
  issued_at: string;
  expires_at: string;

  skill: Skill;
  authority: Authority;
  context: Context;
  execution: ExecutionConfig;
  audit: Audit;
  signature: Signature;
  metadata: Metadata;
}
```

### LLM Compiler Prompt

```
You are an AI security system that selects the minimal skill
and permissions needed for an agent to complete a task.

USER REQUEST: "Find user by email john@example.com"

AVAILABLE SKILLS:
1. getUserById - Retrieves user details by their unique ID
2. getUserByEmail - Searches for a user by their email address
3. updateUserProfile - Updates user profile information
4. sendEmail - Sends an email to a specified recipient
5. deleteUser - Permanently deletes a user account

AVAILABLE SCOPES:
- read:user
- write:user
- delete:user
- send:email

Select the ONE skill with MINIMAL scopes needed.
```

**Result**: `getUserByEmail` with `read:user` scope

---

**Last Updated**: December 26, 2024
