"""Tests for the router primitive."""

import json
from pathlib import Path

import pytest

from authority_runtime.router import (
    JsonlUsageLogger,
    ModelRegistry,
    NeverSensitiveClassifier,
    NullUsageLogger,
    RouteDecision,
    RouteError,
    Router,
    Sensitivity,
    SensitivityClassifier,
    Tier,
    UsageLogger,
)


# ── Sensitivity ───────────────────────────────────────────────


def test_sensitivity_requires_level():
    with pytest.raises(ValueError, match="non-empty"):
        Sensitivity(level="")


def test_sensitivity_level_must_be_string():
    with pytest.raises(ValueError):
        Sensitivity(level=None)  # type: ignore[arg-type]


# ── ModelRegistry ─────────────────────────────────────────────


def test_registry_add_and_lookup_tier():
    reg = ModelRegistry()
    tier = reg.add_tier("local", model="gemma4:26b", origin="Google", note="primary")
    assert isinstance(tier, Tier)
    assert reg.tier("local").model == "gemma4:26b"
    assert reg.tier("local").metadata["note"] == "primary"


def test_registry_rejects_duplicate_tier():
    reg = ModelRegistry()
    reg.add_tier("local", model="m", origin="o")
    with pytest.raises(ValueError, match="already registered"):
        reg.add_tier("local", model="m2", origin="o")


def test_registry_rejects_empty_fields():
    reg = ModelRegistry()
    with pytest.raises(ValueError, match="name"):
        reg.add_tier("", model="m", origin="o")
    with pytest.raises(ValueError, match="model"):
        reg.add_tier("t", model="", origin="o")
    with pytest.raises(ValueError, match="origin"):
        reg.add_tier("t", model="m", origin="")


def test_registry_unknown_tier_raises():
    reg = ModelRegistry()
    with pytest.raises(KeyError):
        reg.tier("nope")


def test_registry_map_sensitivity_requires_known_tier():
    reg = ModelRegistry()
    with pytest.raises(KeyError):
        reg.map_sensitivity("sensitive", "missing")


def test_registry_tiers_with_origin():
    reg = ModelRegistry()
    reg.add_tier("a", model="m1", origin="Google")
    reg.add_tier("b", model="m2", origin="Anthropic")
    reg.add_tier("c", model="m3", origin="Google")
    google_tiers = reg.tiers_with_origin("Google")
    assert {t.name for t in google_tiers} == {"a", "c"}


def test_registry_assert_origins_allowed_passes():
    reg = ModelRegistry()
    reg.add_tier("a", model="m", origin="Anthropic")
    reg.add_tier("b", model="m", origin="Google")
    reg.assert_origins_allowed({"Anthropic", "Google", "Mistral"})


def test_registry_assert_origins_allowed_raises_on_violation():
    reg = ModelRegistry()
    reg.add_tier("a", model="qwen2", origin="Alibaba")
    reg.add_tier("b", model="claude", origin="Anthropic")
    with pytest.raises(ValueError, match="disallowed"):
        reg.assert_origins_allowed({"Anthropic", "Google"})


# ── Router ────────────────────────────────────────────────────


def _two_tier_setup() -> tuple[ModelRegistry, SensitivityClassifier]:
    reg = ModelRegistry()
    reg.add_tier("local", model="gemma4:26b", origin="Google")
    reg.add_tier("frontier", model="claude-sonnet-4", origin="Anthropic")
    reg.map_sensitivity("public", "frontier")
    reg.map_sensitivity("sensitive", "local")

    class _C(SensitivityClassifier):
        def classify(self, query: str) -> Sensitivity:
            if "$" in query or "balance" in query.lower():
                return Sensitivity(level="sensitive", reasons=["financial-token"])
            return Sensitivity(level="public", reasons=["default"])

    return reg, _C()


def test_router_routes_public_to_frontier():
    reg, classifier = _two_tier_setup()
    router = Router(classifier=classifier, registry=reg)
    decision = router.route("what's the weather?")
    assert isinstance(decision, RouteDecision)
    assert decision.tier == "frontier"
    assert decision.model == "claude-sonnet-4"
    assert decision.sensitivity.level == "public"
    assert decision.forced is False
    assert "sensitivity=public" in decision.reason


def test_router_routes_sensitive_to_local():
    reg, classifier = _two_tier_setup()
    router = Router(classifier=classifier, registry=reg)
    decision = router.route("what's my balance?")
    assert decision.tier == "local"
    assert decision.model == "gemma4:26b"
    assert decision.sensitivity.level == "sensitive"
    assert "financial-token" in decision.sensitivity.reasons


def test_router_force_tier_overrides_classification():
    reg, classifier = _two_tier_setup()
    router = Router(classifier=classifier, registry=reg)
    decision = router.route("what's my balance?", force_tier="frontier")
    assert decision.tier == "frontier"
    assert decision.forced is True
    assert decision.sensitivity.level == "sensitive"  # classification still computed


def test_router_force_tier_unknown_raises():
    reg, classifier = _two_tier_setup()
    router = Router(classifier=classifier, registry=reg)
    with pytest.raises(RouteError):
        router.route("hi", force_tier="ghost")


def test_router_unmapped_sensitivity_raises():
    reg = ModelRegistry()
    reg.add_tier("local", model="m", origin="Google")
    # No sensitivity mapping

    class _C(SensitivityClassifier):
        def classify(self, query: str) -> Sensitivity:
            return Sensitivity(level="public")

    router = Router(classifier=_C(), registry=reg)
    with pytest.raises(RouteError):
        router.route("hi")


def test_router_rejects_non_string_query():
    reg, classifier = _two_tier_setup()
    router = Router(classifier=classifier, registry=reg)
    with pytest.raises(RouteError):
        router.route(123)  # type: ignore[arg-type]


# ── NeverSensitiveClassifier ──────────────────────────────────


def test_never_sensitive_classifier_default():
    c = NeverSensitiveClassifier()
    assert c.classify("anything").level == "public"


def test_never_sensitive_classifier_custom_level():
    c = NeverSensitiveClassifier(level="internal")
    assert c.classify("anything").level == "internal"


# ── UsageLogger: Null + JSONL ─────────────────────────────────


def test_null_logger_is_noop():
    logger = NullUsageLogger()
    assert isinstance(logger, UsageLogger)
    decision = RouteDecision(
        tier="t",
        model="m",
        sensitivity=Sensitivity(level="public"),
        reason="r",
    )
    logger.record("q", decision)  # no exception


def test_jsonl_logger_writes_record(tmp_path: Path):
    log = tmp_path / "router.jsonl"
    logger = JsonlUsageLogger(log)
    decision = RouteDecision(
        tier="local",
        model="gemma4:26b",
        sensitivity=Sensitivity(level="sensitive", reasons=["finance"]),
        reason="sensitivity=sensitive",
        forced=False,
    )
    logger.record("hello world", decision)
    line = log.read_text(encoding="utf-8").strip()
    entry = json.loads(line)
    assert entry["tier"] == "local"
    assert entry["model"] == "gemma4:26b"
    assert entry["sensitivity_level"] == "sensitive"
    assert entry["sensitivity_reasons"] == ["finance"]
    assert entry["forced"] is False
    assert entry["query_len"] == len("hello world")
    # privacy: body never appears
    assert "hello world" not in line
    assert "query" not in entry or entry.get("query") is None
    # actually we don't include the body at all
    assert "query_body" not in entry


def test_jsonl_logger_appends_not_overwrites(tmp_path: Path):
    log = tmp_path / "router.jsonl"
    logger = JsonlUsageLogger(log)
    decision = RouteDecision(
        tier="t",
        model="m",
        sensitivity=Sensitivity(level="public"),
        reason="r",
    )
    logger.record("a", decision)
    logger.record("bb", decision)
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["query_len"] == 1
    assert json.loads(lines[1])["query_len"] == 2


def test_router_uses_provided_logger(tmp_path: Path):
    """End-to-end: Router records each decision via the supplied logger."""
    log = tmp_path / "decisions.jsonl"
    reg, classifier = _two_tier_setup()
    router = Router(
        classifier=classifier,
        registry=reg,
        logger=JsonlUsageLogger(log),
    )
    router.route("what's the weather?")
    router.route("balance check", force_tier="frontier")
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["forced"] is False
    assert json.loads(lines[1])["forced"] is True
