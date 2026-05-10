# Harness Audit

`authority_runtime.harness_audit` — static config-surface audit framework.

Carryall ships the auditor and a small set of universal rules. Deployments
register their own rules. Findings emit to JSONL (append-only) so downstream
tooling can re-process them the same way log records are processed.

## Quick start

```python
from pathlib import Path
from authority_runtime.harness_audit import HarnessAuditor, builtin_rules

auditor = HarnessAuditor(
    config_root="/path/to/deployment",
    rules=builtin_rules(),
    findings_log=Path("/path/to/findings.jsonl"),  # optional, append-only
)
findings = auditor.scan()
for f in findings:
    print(f.severity, f.rule_id, f.summary)
```

## Built-in rules

| Rule id                  | Severity | What it checks |
|--------------------------|----------|----------------|
| `no-dangerous-mode-skip` | critical | No `settings.json` (anywhere under config_root) sets `skipDangerousModePermissionPrompt`. Setting it to `false` is still a violation; the key must not appear at all. |
| `skills-declare-tools`   | medium   | Every `SKILL.md` declares its `tools:` allowlist explicitly, even as `tools: []` (documentation-only skills). |

These are deployment-agnostic — they encode invariants that should hold for
any Carryall deployment. Deployment-specific patterns (e.g., bjornswarm's
financial-data routing or no-Chinese-LLMs rules) belong in the deployment.

## Adding a deployment rule

```python
from pathlib import Path
from authority_runtime.harness_audit import HarnessAuditor, Rule, Finding

def _check_no_telegram_for_finance(root: Path) -> list[Finding]:
    # Deployment-specific predicate: scan the deployment's pipelines/
    # for any finance-tagged routing that targets Telegram.
    ...

my_rule = Rule(
    id="no-telegram-for-finance",
    severity="critical",
    description="Finance data must never route to Telegram",
    check_fn=_check_no_telegram_for_finance,
)

auditor = HarnessAuditor(config_root="/my/deployment")
auditor.register(my_rule)
auditor.scan()
```

## Rule contract

A `Rule` is a pure function of the config surface. It must not write to disk,
network, or mutate state. The auditor isolates rule exceptions: if a rule
raises, the scan continues and the failure is itself recorded as a finding
(`severity=high`, `rule_id=<the rule's id>`).

## Findings format (JSONL)

```json
{
  "rule_id": "no-dangerous-mode-skip",
  "severity": "critical",
  "summary": "skipDangerousModePermissionPrompt set; permission prompts must not be bypassed",
  "file": "/path/to/.claude/settings.json",
  "detail": "",
  "timestamp": "2026-05-10T18:42:01.123456+00:00"
}
```

One finding per line, sorted keys. Severities: `info`, `low`, `medium`,
`high`, `critical`.
