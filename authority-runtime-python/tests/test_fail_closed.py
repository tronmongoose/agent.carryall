"""Tests that an unconstrained envelope fails closed rather than skipping the gate."""

import pytest

from authority_runtime.constraints import UNCONSTRAINED, check_constraints
from authority_runtime.enforce import ConstraintViolation, check_envelope
from authority_runtime.envelope import create_simple_envelope, generate_key_pair


@pytest.fixture
def key_pair():
    return generate_key_pair()


def _envelope(private_key, **kwargs):
    """Mint a signed envelope; constraints must be set at mint time, not mutated after."""
    return create_simple_envelope(
        agent_id="agent-1", skill_name="reader", scopes=["vault:finance:read"],
        private_key=private_key, resources=["slos://vaults/finance/*"], **kwargs,
    )


def test_empty_constraints_denies():
    result = check_constraints({}, "read")
    assert result.allowed is False
    assert result.violated and "no constraints" in result.violated[0].lower()
    assert check_constraints({}, "write", resource="slos://vaults/health/x").allowed is False


def test_explicit_unconstrained_allows():
    result = check_constraints({UNCONSTRAINED: True}, "read")
    assert result.allowed is True
    assert result.warnings, "an explicitly unconstrained envelope should warn"


@pytest.mark.parametrize("value", [False, "true", 1, None, {}])
def test_unconstrained_must_be_literally_true(value):
    assert check_constraints({UNCONSTRAINED: value}, "read").allowed is False


def test_unconstrained_does_not_skip_other_constraints():
    result = check_constraints(
        {UNCONSTRAINED: True, "denied_resources": ["slos://vaults/health/*"]},
        "read", resource="slos://vaults/health/rec",
    )
    assert result.allowed is False


def test_check_envelope_denies_unconstrained_envelope(key_pair):
    private_key, public_key = key_pair
    envelope = _envelope(private_key, constraints={})
    with pytest.raises(ConstraintViolation, match="no constraints"):
        check_envelope(envelope, public_key, "vault:finance:read",
                       action="read", resource="slos://vaults/finance/q4")


def test_check_envelope_allows_explicitly_unconstrained(key_pair):
    private_key, public_key = key_pair
    envelope = _envelope(private_key, constraints={UNCONSTRAINED: True})
    check_envelope(envelope, public_key, "vault:finance:read",
                   action="read", resource="slos://vaults/finance/q4")


def test_minted_envelopes_declare_constraints_explicitly(key_pair):
    """create_simple_envelope must not emit the bare {} that now fails closed."""
    private_key, public_key = key_pair
    envelope = _envelope(private_key)
    assert envelope.authority.constraints, "minting produced an empty constraints dict"
    check_envelope(envelope, public_key, "vault:finance:read",
                   action="read", resource="slos://vaults/finance/q4")


def test_minting_accepts_real_constraints(key_pair):
    private_key, public_key = key_pair
    envelope = create_simple_envelope(
        agent_id="agent-1", skill_name="reader", scopes=["vault:finance:read"],
        private_key=private_key, constraints={"require_purpose": True},
    )
    assert envelope.authority.constraints == {"require_purpose": True}
    with pytest.raises(ConstraintViolation):
        check_envelope(envelope, public_key, "vault:finance:read",
                       action="read", resource="x", context={})
    check_envelope(envelope, public_key, "vault:finance:read",
                   action="read", resource="x", context={"purpose": "quarterly close"})
