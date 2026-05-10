# Skill Authoring (`SKILL.md`)

Skills are declared on disk as `SKILL.md` files with YAML frontmatter. Carryall
parses the frontmatter; the markdown body is passed to LLM consumers as-is.

## Minimal example

```markdown
---
name: my-skill
description: One-line description of what this skill does.
tools:
  - tool_one
  - tool_two
---

# My Skill

Markdown body. This is what an LLM sees as the skill's documentation.
```

## Frontmatter fields

| Field         | Required | Type      | Meaning |
|---------------|----------|-----------|---------|
| `name`        | yes      | string    | Stable skill identifier |
| `description` | yes      | string    | One-line summary used by skill selectors |
| `tools`       | no       | list[str] | Fail-closed allowlist of tools the skill may call |

## `tools:` semantics — fail-closed allowlist

The `tools:` key is the skill-tool authorization boundary. It mirrors the
`Authority.scopes` allowlist used at the agent-resource boundary.

- **Listed tools are permitted.** Tools not listed are denied.
- **No wildcards.** `*` matches the literal string `*`, not "any tool".
- **No prefix matching.** `carryall_compile_policy` does not match
  `carryall_compile_policy_v2`.
- **Empty list (`tools: []`) means no tools allowed.** Use this for
  documentation-only skills (e.g., guides that explain how to use a Python API
  rather than calling MCP tools).
- **Missing key defaults to empty list.** Same as `tools: []`. There is no
  implicit wildcard.

## Loading a skill

```python
from authority_runtime import load_skill, enforce_tool_access

manifest = load_skill("path/to/SKILL.md")

# Per-tool authorization check
if enforce_tool_access(manifest, "tool_one"):
    ...
```

Any parse error (missing file, missing frontmatter delimiter, invalid YAML,
missing required field, malformed `tools:` value) raises
`SkillManifestError`. There is no partial-success path.

## Why fail-closed

The pattern comes from bjornswarm (Carryall's customer-zero deployment), where
every `skills/*/SKILL.md` declares `tools:` and the harness enforces. The
mechanism upstreamed here; the specific tool lists per skill stay in each
deployment.
