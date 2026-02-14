# Small LLM Integration - TODO

**Priority**: CRITICAL for Week 1 completion
**Status**: Not started
**Blocker for**: Policy engine, Claude Code integration, full demo

---

## Why This Matters

The current demo uses **rule-based authority narrowing** (manual specification of scopes/context). For the product to be production-ready, we need an **LLM-based policy compiler** that:

1. **Analyzes the current context** (user request, conversation history, available skills)
2. **Selects the minimal skill set** needed for the next step
3. **Narrows authority intelligently** based on intent (not hardcoded rules)
4. **Runs fast and cheap** (small model, <100ms inference, pennies per call)

---

## Requirements

### Model Selection Criteria

1. **Size**: 1-3B parameters (must run locally or via cheap API)
2. **Speed**: <100ms inference time
3. **Task**: Function calling / tool selection (not general chat)
4. **Cost**: <$0.001 per call (self-funding economics)
5. **Deployment**: npm-installable or hosted API (no complex setup)

### Candidate Models

| Model | Size | Strengths | Concerns |
|-------|------|-----------|----------|
| **Llama 3.2 1B** | 1B | Fast, good at structured output | Need local inference setup |
| **Phi-3 Mini** | 3.8B | Strong reasoning, Microsoft-backed | Larger size |
| **Gemma 2B** | 2B | Google, good at following instructions | API availability? |
| **Gemini Flash** | Unknown | Fast, cheap API, good at function calling | Vendor lock-in concern |

### Recommended: Start with Gemini Flash

**Rationale**:
- Cheap API ($0.00001 per token)
- Fast (<500ms)
- Excellent at function calling (proven use case)
- Can switch to local model later if needed
- **MVP speed** over perfect architecture

---

## Implementation Plan

### Step 1: LLM Client Setup
```typescript
// packages/core/src/llm/client.ts
export interface LLMClient {
  selectSkills(
    context: AgentContext,
    availableSkills: Skill[],
    policy: Policy
  ): Promise<SkillSelection>;
}

export class GeminiClient implements LLMClient {
  // Use @google/generative-ai SDK
}
```

### Step 2: Policy Compiler
```typescript
// packages/core/src/policy/compiler.ts
export async function compilePolicy(
  parentEnvelope: AuthorityEnvelope,
  userRequest: string,
  availableSkills: Skill[]
): Promise<AuthorityEnvelope> {
  // 1. Extract context from parent
  // 2. Call LLM to select minimal skill set
  // 3. Create child envelope with narrowed authority
  // 4. Return signed envelope
}
```

### Step 3: Update Demo
```typescript
// demo/llm-demo.ts
const childEnvelope = await compilePolicy(
  parentEnvelope,
  "Find user by email",
  allAvailableSkills
);

console.log(`LLM selected: ${childEnvelope.skill.name}`);
console.log(`Token reduction: ${tokenSavings}%`);
```

---

## Success Criteria

- [ ] LLM can select correct skill given context
- [ ] Token reduction >70% (measured vs baseline)
- [ ] Inference time <500ms
- [ ] Cost <$0.001 per decision
- [ ] Works with 3 different user intents (read, write, admin)

---

## Risks & Mitigation

**Risk**: LLM selects wrong skill, breaks user workflow
**Mitigation**: Fallback to rule-based selection if confidence <0.8

**Risk**: LLM too slow (>1s), bad UX
**Mitigation**: Cache common decisions, use streaming if needed

**Risk**: LLM too expensive, economics don't work
**Mitigation**: Switch to local Llama 3.2 1B (one-time setup cost)

---

## References

- [MVP - 5 Week Plan.md](../MVP - 5 Week Plan.md) - Week 1 Day 2 tasks
- [Architecture Plan.md](../Architecture Plan.md) - Section 4.3: Authority Compiler
- User requirement: "lets use a small LLM for this"

---

**Next Steps**:
1. Research Gemini Flash API for function calling
2. Create proof-of-concept: "Given context + skills, return best skill"
3. Measure token reduction vs baseline
4. Integrate into envelope creation flow

---

**Created**: December 23, 2024, 8:30 PM
**Status**: Ready to start on Day 2
