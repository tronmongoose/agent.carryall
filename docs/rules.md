# Rule Packs

`authority_runtime.rule_packs` — numbered hard-rule enforcement.

A **rule pack** is a deployment-supplied YAML file declaring numbered runtime
rules. Each rule names a predicate (registered separately by deployment code)
and one or more enforcement points (e.g., `pre-notify`, `pre-llm-call`).

When a rule fires, the enforcement call raises `RuleViolation` with the rule's
number and description — so failures are traceable to the deployment's
canonical rule list (e.g., bjornswarm "rule #14").

## Rule pack file format

```yaml
version: 1
rules:
  - id: financial-no-telegram
    number: 3
    description: "Financial figures route to ntfy only, never Telegram."
    predicate: contains_financial_data
    enforcement:
      - pre-notify

  - id: no-chinese-llms
    number: 13
    description: "No Chinese-origin LLMs."
    predicate: model_origin_allowed
    enforcement:
      - pre-llm-call
```

| Field         | Required | Type       | Meaning |
|---------------|----------|------------|---------|
| `id`          | yes      | string     | Stable rule identifier |
| `description` | no       | string     | Human-readable rule text |
| `predicate`   | yes      | string     | Name of a registered predicate |
| `enforcement` | yes      | list[str]  | Enforcement points where this rule fires |
| `number`      | no       | int / null | Canonical rule number (used in error messages) |

## Predicate contract

Predicates align with `authority_runtime.constraints`: a predicate evaluates
a context dict and returns:

- `None` — action permitted, rule passes.
- `str`  — action denied, with the string used as the violation message.

Anything else raises `RulePackError` at enforcement time.

```python
from authority_runtime.rule_packs import register_predicate

@register_predicate("contains_financial_data")
def _check(ctx) -> str | None:
    body = ctx.get("body", "")
    if "$" in body or "balance:" in body.lower():
        return "body contains financial figure"
    return None
```

## Enforcing rules

### Direct call

```python
from authority_runtime.rule_packs import RulePack

pack = RulePack.load("rules.yaml")
pack.enforce_point("pre-notify", {"channel": "telegram", "body": "..."})
# raises RuleViolation if any rule attached to pre-notify fires
```

### `@enforces` decorator

```python
from authority_runtime.rule_packs import enforces

@enforces(pack, "pre-notify")
def send_notification(channel, body):
    ...
```

By default the decorated function's kwargs become the predicate context.
For richer context, pass a builder:

```python
@enforces(
    pack,
    "pre-notify",
    context_builder=lambda channel, body, **kw: {
        "channel": channel,
        "body": body,
        "user_id": kw.get("user_id"),
    },
)
def send_notification(channel, body, *, user_id):
    ...
```

## RuleViolation

```python
class RuleViolation(Exception):
    rule_id: str               # e.g. "financial-no-telegram"
    rule_number: int | None    # e.g. 3
    description: str           # rule's `description` field
    message: str               # predicate's return string
    enforcement_point: str     # e.g. "pre-notify"
```

`str(violation)` formats as `Rule #3 (financial-no-telegram) violated at pre-notify: <message>`.

## Boundary

Carryall ships the loader, registry, enforcer, decorator, and the
`RuleViolation` type. Deployments supply:

- The `rules.yaml` file with their numbered rules.
- Predicate functions registered by name.
- Wiring from their pipelines to the enforcement points (via
  `enforce_point` or `@enforces`).

This lets each deployment carry its own canonical rule list (bjornswarm's
financial routing, no-Chinese-LLMs, no-PANW work product, etc.) without
the contents leaking into the Carryall product.
