# Router (sensitivity-aware tiered routing)

`authority_runtime.router` — a deployment-configurable primitive for routing
queries between model tiers (e.g., local-vs-frontier) based on a
deployment-supplied sensitivity classifier.

Carryall ships the abstractions and tier mechanics. Deployments supply the
classifier, the model lineup, and the sensitivity → tier mapping.

## Quick start

```python
from authority_runtime.router import (
    JsonlUsageLogger,
    ModelRegistry,
    Router,
    Sensitivity,
    SensitivityClassifier,
)

# 1. Define how your deployment classifies query sensitivity.
class FinanceClassifier(SensitivityClassifier):
    def classify(self, query: str) -> Sensitivity:
        if "$" in query or "balance" in query.lower():
            return Sensitivity(level="sensitive", reasons=["financial-token"])
        return Sensitivity(level="public", reasons=["default"])

# 2. Register your model tiers.
registry = ModelRegistry()
registry.add_tier("local",    model="gemma4:26b",       origin="Google")
registry.add_tier("frontier", model="claude-sonnet-4",  origin="Anthropic")

# 3. Map sensitivity levels to tiers.
registry.map_sensitivity("public",    "frontier")
registry.map_sensitivity("sensitive", "local")

# 4. Origin policy (optional but recommended).
registry.assert_origins_allowed({"Anthropic", "Google", "Mistral", "Meta"})

# 5. Compose and route.
router = Router(
    classifier=FinanceClassifier(),
    registry=registry,
    logger=JsonlUsageLogger("/var/log/router-usage.jsonl"),
)
decision = router.route("what's my balance?")
# decision.tier == "local"
# decision.model == "gemma4:26b"
# decision.sensitivity.level == "sensitive"
```

## Concepts

### `Sensitivity`

The classifier's verdict. `level` is a deployment-defined label — Carryall
does not assume any particular taxonomy. Common levels: `public`,
`internal`, `sensitive`. `reasons` is a list of human-readable explanations
that get logged for audit.

### `SensitivityClassifier`

ABC with one method: `classify(query: str) -> Sensitivity`. Carryall ships
`NeverSensitiveClassifier` as a starting point / test fixture; real
deployments subclass.

### `Tier` and `ModelRegistry`

A `Tier` is a `(name, model, origin)` triple plus arbitrary metadata.
The registry holds tiers and the sensitivity-level → tier mapping.

`assert_origins_allowed(allowed)` is the boot-time enforcement hook for
deployment origin policies (e.g., bjornswarm rule #13: US/EU origins only).
It raises if any registered tier's origin is outside the allowlist.

### `Router` and `RouteDecision`

`Router.route(query, *, force_tier=None)` returns a `RouteDecision`.
`force_tier="frontier"` overrides classification (the classifier still runs;
its verdict goes into the decision and the log, with `forced=True`).

### `UsageLogger`

ABC with `record(query, decision)`. `NullUsageLogger` discards.
`JsonlUsageLogger` appends one JSON record per decision.

**Privacy posture:** `JsonlUsageLogger` writes only the query *length*
(`query_len`), never the body. Deployments that want richer logging supply
their own `UsageLogger` and own the privacy decision.

## Migration from the deployment-side `clawrouter.py`

The original `mayor/clawrouter.py` (still in this repo) is finance-specific:
it imports `firefly_tools`, hardcodes `gemma4:26b` / `claude-sonnet-4`, and
writes to `$SLOS_DIR/vaults/finance/router-usage.jsonl`. That's deployment
code masquerading as product code.

This package is the generic primitive. Migration plan:

1. **(this commit)** Carryall ships the primitive. `mayor/clawrouter.py`
   stays in place; nothing breaks.
2. **(follow-up)** bjornswarm migrates `pipelines/clawrouter.py` to import
   `authority_runtime.router.Router` with a finance-specific classifier and
   a finance-log path.
3. **(later)** `mayor/clawrouter.py` either becomes a thin shim around the
   primitive or is deleted in favor of bjornswarm's wired-up version.
