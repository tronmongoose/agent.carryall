"""Tests for the harness_audit framework."""

import json
from pathlib import Path

import pytest

from authority_runtime.harness_audit import (
    AuditError,
    Finding,
    HarnessAuditor,
    Rule,
    builtin_rules,
)


# ── Finding ───────────────────────────────────────────────────


def test_finding_rejects_unknown_severity():
    with pytest.raises(AuditError, match="severity"):
        Finding(rule_id="x", severity="catastrophic", summary="nope")


def test_finding_to_jsonl_is_parseable():
    f = Finding(rule_id="x", severity="low", summary="hi", file="/a/b")
    obj = json.loads(f.to_jsonl())
    assert obj["rule_id"] == "x"
    assert obj["severity"] == "low"
    assert obj["file"] == "/a/b"


# ── HarnessAuditor lifecycle ──────────────────────────────────


def test_auditor_rejects_missing_root(tmp_path: Path):
    with pytest.raises(AuditError, match="does not exist"):
        HarnessAuditor(config_root=tmp_path / "nope")


def test_auditor_rejects_file_root(tmp_path: Path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    with pytest.raises(AuditError, match="must be a directory"):
        HarnessAuditor(config_root=f)


def test_auditor_register_rejects_duplicate_id(tmp_path: Path):
    auditor = HarnessAuditor(config_root=tmp_path)
    rule = Rule(id="r", severity="low", description="d", check_fn=lambda _: [])
    auditor.register(rule)
    with pytest.raises(AuditError, match="already registered"):
        auditor.register(rule)


def test_auditor_scan_runs_rules_and_collects_findings(tmp_path: Path):
    auditor = HarnessAuditor(config_root=tmp_path)
    f = Finding(rule_id="r", severity="low", summary="hi")
    auditor.register(
        Rule(id="r", severity="low", description="d", check_fn=lambda _: [f])
    )
    findings = auditor.scan()
    assert len(findings) == 1
    assert findings[0].rule_id == "r"


def test_auditor_scan_appends_to_jsonl_log(tmp_path: Path):
    log = tmp_path / "logs" / "findings.jsonl"
    auditor = HarnessAuditor(config_root=tmp_path, findings_log=log)
    f = Finding(rule_id="r", severity="low", summary="hi")
    auditor.register(
        Rule(id="r", severity="low", description="d", check_fn=lambda _: [f])
    )
    auditor.scan()
    auditor2 = HarnessAuditor(config_root=tmp_path, findings_log=log)
    auditor2.register(
        Rule(id="r2", severity="low", description="d", check_fn=lambda _: [f])
    )
    auditor2.scan()

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2  # append, not overwrite
    for line in lines:
        json.loads(line)  # valid JSON


def test_auditor_isolates_rule_exceptions(tmp_path: Path):
    auditor = HarnessAuditor(config_root=tmp_path)

    def boom(_root: Path):
        raise RuntimeError("rule blew up")

    auditor.register(
        Rule(id="explody", severity="low", description="d", check_fn=boom)
    )
    auditor.register(
        Rule(id="ok", severity="low", description="d", check_fn=lambda _: [])
    )
    findings = auditor.scan()
    assert len(findings) == 1
    assert findings[0].rule_id == "explody"
    assert findings[0].severity == "high"
    assert "rule blew up" in findings[0].detail


def test_auditor_flags_non_finding_yields(tmp_path: Path):
    auditor = HarnessAuditor(config_root=tmp_path)
    auditor.register(
        Rule(
            id="bad-yield",
            severity="low",
            description="d",
            check_fn=lambda _: ["not a Finding"],  # type: ignore[list-item]
        )
    )
    findings = auditor.scan()
    assert len(findings) == 1
    assert findings[0].rule_id == "bad-yield"
    assert "non-Finding" in findings[0].summary


# ── Built-in: no-dangerous-mode-skip ──────────────────────────


def test_no_dangerous_mode_skip_clean(tmp_path: Path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(
        json.dumps({"hooks": {}, "permissions": {"allow": []}})
    )
    auditor = HarnessAuditor(config_root=tmp_path, rules=builtin_rules())
    findings = [f for f in auditor.scan() if f.rule_id == "no-dangerous-mode-skip"]
    assert findings == []


def test_no_dangerous_mode_skip_detects_top_level(tmp_path: Path):
    (tmp_path / "settings.json").write_text(
        json.dumps({"skipDangerousModePermissionPrompt": True})
    )
    auditor = HarnessAuditor(config_root=tmp_path, rules=builtin_rules())
    findings = [f for f in auditor.scan() if f.rule_id == "no-dangerous-mode-skip"]
    assert len(findings) == 1
    assert findings[0].severity == "critical"


def test_no_dangerous_mode_skip_detects_nested(tmp_path: Path):
    (tmp_path / "settings.json").write_text(
        json.dumps({"deeply": {"nested": {"skipDangerousModePermissionPrompt": False}}})
    )
    auditor = HarnessAuditor(config_root=tmp_path, rules=builtin_rules())
    findings = [f for f in auditor.scan() if f.rule_id == "no-dangerous-mode-skip"]
    # Even setting it to False is a violation: the key must not appear at all.
    assert len(findings) == 1


def test_no_dangerous_mode_skip_skips_noisy_dirs(tmp_path: Path):
    bad = tmp_path / "node_modules" / "settings.json"
    bad.parent.mkdir(parents=True)
    bad.write_text(json.dumps({"skipDangerousModePermissionPrompt": True}))
    auditor = HarnessAuditor(config_root=tmp_path, rules=builtin_rules())
    findings = [f for f in auditor.scan() if f.rule_id == "no-dangerous-mode-skip"]
    assert findings == []


def test_no_dangerous_mode_skip_invalid_json_flagged(tmp_path: Path):
    (tmp_path / "settings.json").write_text("{not valid")
    auditor = HarnessAuditor(config_root=tmp_path, rules=builtin_rules())
    findings = [f for f in auditor.scan() if f.rule_id == "no-dangerous-mode-skip"]
    assert len(findings) == 1
    assert findings[0].severity == "medium"


# ── Built-in: skills-declare-tools ────────────────────────────


def test_skills_declare_tools_clean_explicit_empty(tmp_path: Path):
    skill = tmp_path / "skills" / "doc-only" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: doc-only\ndescription: docs\ntools: []\n---\n# body\n"
    )
    auditor = HarnessAuditor(config_root=tmp_path, rules=builtin_rules())
    findings = [f for f in auditor.scan() if f.rule_id == "skills-declare-tools"]
    assert findings == []


def test_skills_declare_tools_clean_with_tools(tmp_path: Path):
    skill = tmp_path / "skills" / "real" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: real\ndescription: x\ntools:\n  - a\n  - b\n---\n"
    )
    auditor = HarnessAuditor(config_root=tmp_path, rules=builtin_rules())
    findings = [f for f in auditor.scan() if f.rule_id == "skills-declare-tools"]
    assert findings == []


def test_skills_declare_tools_flags_missing_tools_key(tmp_path: Path):
    skill = tmp_path / "skills" / "legacy" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: legacy\ndescription: no tools\n---\n")
    auditor = HarnessAuditor(config_root=tmp_path, rules=builtin_rules())
    findings = [f for f in auditor.scan() if f.rule_id == "skills-declare-tools"]
    assert len(findings) == 1
    assert findings[0].severity == "medium"
    assert "explicit" in findings[0].summary


def test_skills_declare_tools_flags_unparseable(tmp_path: Path):
    skill = tmp_path / "skills" / "broken" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("no frontmatter at all\n")
    auditor = HarnessAuditor(config_root=tmp_path, rules=builtin_rules())
    findings = [f for f in auditor.scan() if f.rule_id == "skills-declare-tools"]
    assert len(findings) == 1
    assert findings[0].severity == "high"


# ── Self-dogfood: scan the carryall repo's own skills/ ────────


def test_self_dogfood_carryall_skills_pass_audit():
    """The two SKILL.md files Carryall ships must pass the universal rules."""
    repo_root = Path(__file__).resolve().parents[2]
    skills_dir = repo_root / "skills"
    if not skills_dir.is_dir():
        pytest.skip("skills/ not present in this checkout")
    auditor = HarnessAuditor(config_root=skills_dir, rules=builtin_rules())
    findings = auditor.scan()
    skill_findings = [
        f for f in findings if f.rule_id == "skills-declare-tools"
    ]
    assert skill_findings == [], (
        f"carryall's own SKILL.md files failed audit: {skill_findings}"
    )
