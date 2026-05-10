# SOUL.md (opt-in convention, under evaluation)

> **Status:** opt-in. The SOUL.md construct is under evaluation in
> bjornswarm (eval `sl-qvby`, window closes 2026-06-02). Carryall ships
> the **parser** so deployments can experiment without bespoke tooling.
> The **convention** itself — what SOUL.md should contain, how downstream
> systems should react to it — is not yet codified. Treat this doc as
> descriptive, not prescriptive.

A SOUL.md file may live next to a SKILL.md as an *optional sibling*. The
two files express different things:

| File       | Purpose |
|------------|---------|
| `SKILL.md` | Operational contract — name, description, permitted tools, runtime constraints |
| `SOUL.md`  | Voice, posture, refusals — how the skill should *sound* and what it should *not* do |

The split exists because operational contracts and voice/posture rot at
different rates and benefit from different reviewers.

## Format

SOUL.md frontmatter is **optional**. The file may be pure markdown, or it
may carry YAML frontmatter:

```markdown
---
eval: v0
voice: terse
refusals:
  - flattery
  - speculation
---

# Voice

Short sentences. No filler. Numbers as proof, never adjectives.
```

| Field   | Required | Type   | Meaning |
|---------|----------|--------|---------|
| `eval`  | no       | string | Evaluation tag (e.g. `v0`). Carryall surfaces it as `soul.eval_marker`; it does not enforce semantics. |
| anything else | no | any | Pass-through. Stored in `soul.frontmatter`. |

## Loading

```python
from authority_runtime import load_skill, load_soul

# Auto-attach: load_skill picks up sibling SOUL.md if present.
manifest = load_skill("path/to/SKILL.md")
if manifest.soul is not None:
    print(manifest.soul.eval_marker)  # "v0" or None
    print(manifest.soul.body)

# Skip the sibling lookup explicitly.
manifest = load_skill("path/to/SKILL.md", load_soul=False)

# Or load a SOUL.md directly.
soul = load_soul("path/to/SOUL.md")
```

## Error model

- **`SkillManifestError`** — SKILL.md problem (operational contract failure).
- **`SkillSoulError`** — SOUL.md problem (voice doc failure).

These are deliberately separate so a deployment can decide whether a
malformed voice doc should block skill loading. Today, `load_skill`
propagates `SkillSoulError` (fail-loud). Future versions may add a
soft-fail mode if the eval concludes that's the right default.

## Why parser-only, why now

The bjornswarm dogfood evaluation (`sl-qvby`) is the load-bearing question:
does the SKILL/SOUL split produce better-aligned skills, or just twice as
many files? Until that closes 2026-06-02, the convention should not be
codified in product docs as a recommended pattern. The parser ships now so
deployments running the eval don't fork their own — but the answer to
"should I author SOUL.md files?" is still "depends on what bjornswarm finds."
