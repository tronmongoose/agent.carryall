"""Tests for rule_packs: numbered hard-rule enforcement."""

from pathlib import Path

import pytest

from authority_runtime.rule_packs import (
    PredicateRegistry,
    Rule,
    RulePack,
    RulePackError,
    RuleViolation,
    enforces,
)


# ── Rule validation ──────────────────────────────────────────


def test_rule_requires_id():
    with pytest.raises(RulePackError, match="id"):
        Rule(id="", description="d", predicate="p", enforcement=["x"])


def test_rule_requires_predicate():
    with pytest.raises(RulePackError, match="predicate"):
        Rule(id="r", description="d", predicate="", enforcement=["x"])


def test_rule_requires_enforcement_point():
    with pytest.raises(RulePackError, match="enforcement"):
        Rule(id="r", description="d", predicate="p", enforcement=[])


def test_rule_rejects_non_string_enforcement():
    with pytest.raises(RulePackError, match="enforcement"):
        Rule(id="r", description="d", predicate="p", enforcement=[""])


def test_rule_rejects_non_int_number():
    with pytest.raises(RulePackError, match="number"):
        Rule(
            id="r",
            description="d",
            predicate="p",
            enforcement=["x"],
            number="three",  # type: ignore[arg-type]
        )


# ── RulePack.from_dict ────────────────────────────────────────


def _make_registry(**predicates):
    reg = PredicateRegistry()
    for name, fn in predicates.items():
        reg.register(name, fn)
    return reg


def test_rule_pack_loads_minimal():
    reg = _make_registry(noop=lambda ctx: None)
    pack = RulePack.from_dict(
        {
            "version": 1,
            "rules": [
                {
                    "id": "r1",
                    "description": "first",
                    "predicate": "noop",
                    "enforcement": ["pre-x"],
                }
            ],
        },
        registry=reg,
    )
    assert len(pack.rules) == 1
    assert pack.rules_for("pre-x")[0].id == "r1"
    assert pack.rules_for("nope") == []


def test_rule_pack_rejects_unsupported_version():
    with pytest.raises(RulePackError, match="version"):
        RulePack.from_dict({"version": 99, "rules": []})


def test_rule_pack_rejects_non_mapping():
    with pytest.raises(RulePackError, match="mapping"):
        RulePack.from_dict([])  # type: ignore[arg-type]


def test_rule_pack_rejects_non_list_rules():
    with pytest.raises(RulePackError, match="`rules`"):
        RulePack.from_dict({"version": 1, "rules": {"id": "x"}})


def test_rule_pack_rejects_duplicate_ids():
    with pytest.raises(RulePackError, match="Duplicate"):
        RulePack(
            rules=[
                Rule(id="dup", description="a", predicate="p", enforcement=["x"]),
                Rule(id="dup", description="b", predicate="p", enforcement=["y"]),
            ]
        )


# ── RulePack.load (from disk) ─────────────────────────────────


def test_rule_pack_load_from_yaml(tmp_path: Path):
    yaml_path = tmp_path / "rules.yaml"
    yaml_path.write_text(
        """version: 1
rules:
  - id: financial-no-telegram
    number: 3
    description: Financial figures route to ntfy only
    predicate: contains_financial_data
    enforcement:
      - pre-notify
""",
        encoding="utf-8",
    )
    reg = _make_registry(contains_financial_data=lambda ctx: None)
    pack = RulePack.load(yaml_path, registry=reg)
    rule = pack.rules[0]
    assert rule.id == "financial-no-telegram"
    assert rule.number == 3


def test_rule_pack_load_missing_file_raises(tmp_path: Path):
    with pytest.raises(RulePackError, match="not found"):
        RulePack.load(tmp_path / "nope.yaml")


def test_rule_pack_load_invalid_yaml_raises(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1\nrules: [unclosed", encoding="utf-8")
    with pytest.raises(RulePackError, match="not valid YAML"):
        RulePack.load(bad)


# ── enforce_point ─────────────────────────────────────────────


def test_enforce_passes_when_predicate_returns_none():
    reg = _make_registry(ok=lambda ctx: None)
    pack = RulePack(
        rules=[Rule(id="r", description="d", predicate="ok", enforcement=["pt"])],
        registry=reg,
    )
    pack.enforce_point("pt", {})  # no raise


def test_enforce_raises_when_predicate_returns_string():
    reg = _make_registry(boom=lambda ctx: "tripwire")
    pack = RulePack(
        rules=[
            Rule(
                id="rule-3",
                number=3,
                description="financial → ntfy only",
                predicate="boom",
                enforcement=["pre-notify"],
            )
        ],
        registry=reg,
    )
    with pytest.raises(RuleViolation) as exc:
        pack.enforce_point("pre-notify", {"channel": "telegram"})
    v = exc.value
    assert v.rule_id == "rule-3"
    assert v.rule_number == 3
    assert v.enforcement_point == "pre-notify"
    assert v.message == "tripwire"
    assert "Rule #3" in str(v)


def test_enforce_unknown_point_is_noop():
    reg = _make_registry(boom=lambda ctx: "x")
    pack = RulePack(
        rules=[Rule(id="r", description="d", predicate="boom", enforcement=["pt"])],
        registry=reg,
    )
    pack.enforce_point("other-point", {})  # no rules registered there


def test_enforce_unregistered_predicate_raises_pack_error():
    pack = RulePack(
        rules=[
            Rule(id="r", description="d", predicate="missing", enforcement=["pt"])
        ],
        registry=PredicateRegistry(),
    )
    with pytest.raises(RulePackError, match="unregistered predicate"):
        pack.enforce_point("pt", {})


def test_enforce_predicate_returning_non_string_raises():
    reg = _make_registry(weird=lambda ctx: True)  # type: ignore[arg-type,return-value]
    pack = RulePack(
        rules=[
            Rule(id="r", description="d", predicate="weird", enforcement=["pt"])
        ],
        registry=reg,
    )
    with pytest.raises(RulePackError, match="must return Optional"):
        pack.enforce_point("pt", {})


def test_enforce_first_violation_wins():
    """When multiple rules match a point, the first one to fire raises."""
    reg = _make_registry(
        first=lambda ctx: "first failed",
        second=lambda ctx: "second failed",
    )
    pack = RulePack(
        rules=[
            Rule(id="r1", number=1, description="d", predicate="first", enforcement=["pt"]),
            Rule(id="r2", number=2, description="d", predicate="second", enforcement=["pt"]),
        ],
        registry=reg,
    )
    with pytest.raises(RuleViolation) as exc:
        pack.enforce_point("pt", {})
    assert exc.value.rule_id == "r1"


def test_enforce_rule_with_no_number_still_works():
    reg = _make_registry(boom=lambda ctx: "x")
    pack = RulePack(
        rules=[Rule(id="unnumbered", description="d", predicate="boom", enforcement=["pt"])],
        registry=reg,
    )
    with pytest.raises(RuleViolation) as exc:
        pack.enforce_point("pt", {})
    assert exc.value.rule_number is None
    assert "Rule unnumbered" in str(exc.value)


# ── PredicateRegistry ─────────────────────────────────────────


def test_predicate_registry_rejects_duplicate():
    reg = PredicateRegistry()
    reg.register("p", lambda ctx: None)
    with pytest.raises(ValueError, match="already registered"):
        reg.register("p", lambda ctx: None)


def test_predicate_registry_rejects_empty_name():
    reg = PredicateRegistry()
    with pytest.raises(ValueError, match="non-empty"):
        reg.register("", lambda ctx: None)


def test_predicate_registry_get_unknown_raises():
    reg = PredicateRegistry()
    with pytest.raises(KeyError):
        reg.get("missing")


# ── @enforces decorator ──────────────────────────────────────


def test_enforces_decorator_passes_through_when_clean():
    reg = _make_registry(ok=lambda ctx: None)
    pack = RulePack(
        rules=[Rule(id="r", description="d", predicate="ok", enforcement=["pt"])],
        registry=reg,
    )

    @enforces(pack, "pt")
    def send(channel, body):
        return f"sent:{channel}:{body}"

    assert send(channel="ntfy", body="hello") == "sent:ntfy:hello"


def test_enforces_decorator_raises_on_violation():
    captured = {}

    def boom(ctx):
        captured.update(ctx)
        return "blocked"

    reg = _make_registry(boom=boom)
    pack = RulePack(
        rules=[
            Rule(
                id="rule-3",
                number=3,
                description="d",
                predicate="boom",
                enforcement=["pre-notify"],
            )
        ],
        registry=reg,
    )

    @enforces(pack, "pre-notify")
    def send(channel, body):
        return "should not run"

    with pytest.raises(RuleViolation) as exc:
        send(channel="telegram", body="balance: $1000")
    assert exc.value.rule_number == 3
    assert captured == {"channel": "telegram", "body": "balance: $1000"}


def test_enforces_decorator_with_custom_context_builder():
    reg = _make_registry(check=lambda ctx: ctx.get("violation"))
    pack = RulePack(
        rules=[Rule(id="r", description="d", predicate="check", enforcement=["pt"])],
        registry=reg,
    )

    @enforces(
        pack,
        "pt",
        context_builder=lambda payload: {"violation": payload.get("danger")},
    )
    def process(payload):
        return "ok"

    assert process({"danger": None}) == "ok"
    with pytest.raises(RuleViolation):
        process({"danger": "tripped"})
