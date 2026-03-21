#!/usr/bin/env python3
"""
Tests for validation and error handling
"""

import pytest
from authority_runtime import (
    create_envelope,
    generate_key_pair,
    ValidationError,
    Skill,
    SkillParameters,
    Authority,
    Context,
    ExecutionConfig,
)


def test_validate_agent_id():
    """Test agent_id validation"""
    private_key, public_key = generate_key_pair()

    # Valid agent_id
    envelope = create_envelope(
        agent_id="valid-agent-123",
        provider="openai",
        step_number=1,
        root_policy_id="policy-1",
        skill=Skill(id="s1", name="test", tool="Test", parameters=SkillParameters(allowed=[], constraints={})),
        authority=Authority(scopes=["read:data"], resources=["*"]),
        context=Context(included=[], excluded=[]),
        execution=ExecutionConfig(provider_config={}),
        private_key=private_key
    )
    assert envelope.agent_id == "valid-agent-123"

    # Empty agent_id
    with pytest.raises(ValidationError) as exc_info:
        create_envelope(
            agent_id="",
            provider="openai",
            step_number=1,
            root_policy_id="policy-1",
            skill=Skill(id="s1", name="test", tool="Test", parameters=SkillParameters(allowed=[], constraints={})),
            authority=Authority(scopes=["read:data"], resources=["*"]),
            context=Context(included=[], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key
        )
    assert "cannot be empty" in str(exc_info.value)

    # Invalid characters
    with pytest.raises(ValidationError) as exc_info:
        create_envelope(
            agent_id="invalid agent!",
            provider="openai",
            step_number=1,
            root_policy_id="policy-1",
            skill=Skill(id="s1", name="test", tool="Test", parameters=SkillParameters(allowed=[], constraints={})),
            authority=Authority(scopes=["read:data"], resources=["*"]),
            context=Context(included=[], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key
        )
    assert "alphanumeric" in str(exc_info.value)


def test_validate_provider():
    """Test provider validation"""
    private_key, public_key = generate_key_pair()

    # Valid provider (match Pydantic Literal type in types.py)
    for provider in ["openai", "claude", "gemini", "custom"]:
        envelope = create_envelope(
            agent_id="agent-1",
            provider=provider,
            step_number=1,
            root_policy_id="policy-1",
            skill=Skill(id="s1", name="test", tool="Test", parameters=SkillParameters(allowed=[], constraints={})),
            authority=Authority(scopes=["read:data"], resources=["*"]),
            context=Context(included=[], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key
        )
        assert envelope.provider == provider

    # Invalid provider
    with pytest.raises(ValidationError) as exc_info:
        create_envelope(
            agent_id="agent-1",
            provider="invalid-provider",
            step_number=1,
            root_policy_id="policy-1",
            skill=Skill(id="s1", name="test", tool="Test", parameters=SkillParameters(allowed=[], constraints={})),
            authority=Authority(scopes=["read:data"], resources=["*"]),
            context=Context(included=[], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key
        )
    assert "Unknown provider" in str(exc_info.value)
    assert "custom" in str(exc_info.value).lower()


def test_validate_step_number():
    """Test step_number validation"""
    private_key, public_key = generate_key_pair()

    # Valid step_number
    envelope = create_envelope(
        agent_id="agent-1",
        provider="openai",
        step_number=5,
        root_policy_id="policy-1",
        skill=Skill(id="s1", name="test", tool="Test", parameters=SkillParameters(allowed=[], constraints={})),
        authority=Authority(scopes=["read:data"], resources=["*"]),
        context=Context(included=[], excluded=[]),
        execution=ExecutionConfig(provider_config={}),
        private_key=private_key
    )
    assert envelope.step_number == 5

    # Negative step_number
    with pytest.raises(ValidationError) as exc_info:
        create_envelope(
            agent_id="agent-1",
            provider="openai",
            step_number=-1,
            root_policy_id="policy-1",
            skill=Skill(id="s1", name="test", tool="Test", parameters=SkillParameters(allowed=[], constraints={})),
            authority=Authority(scopes=["read:data"], resources=["*"]),
            context=Context(included=[], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key
        )
    assert "must be >= 0" in str(exc_info.value)

    # Excessive step_number (potential infinite loop)
    with pytest.raises(ValidationError) as exc_info:
        create_envelope(
            agent_id="agent-1",
            provider="openai",
            step_number=10001,
            root_policy_id="policy-1",
            skill=Skill(id="s1", name="test", tool="Test", parameters=SkillParameters(allowed=[], constraints={})),
            authority=Authority(scopes=["read:data"], resources=["*"]),
            context=Context(included=[], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key
        )
    assert "infinite loop" in str(exc_info.value)


def test_validate_scopes():
    """Test scopes validation"""
    private_key, public_key = generate_key_pair()

    # Empty scopes
    with pytest.raises(ValidationError) as exc_info:
        create_envelope(
            agent_id="agent-1",
            provider="openai",
            step_number=1,
            root_policy_id="policy-1",
            skill=Skill(id="s1", name="test", tool="Test", parameters=SkillParameters(allowed=[], constraints={})),
            authority=Authority(scopes=[], resources=["*"]),
            context=Context(included=[], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key
        )
    assert "cannot be empty" in str(exc_info.value)
    assert "must grant at least one permission" in str(exc_info.value)


def test_validate_resources():
    """Test resources validation"""
    private_key, public_key = generate_key_pair()

    # Empty resources
    with pytest.raises(ValidationError) as exc_info:
        create_envelope(
            agent_id="agent-1",
            provider="openai",
            step_number=1,
            root_policy_id="policy-1",
            skill=Skill(id="s1", name="test", tool="Test", parameters=SkillParameters(allowed=[], constraints={})),
            authority=Authority(scopes=["read:data"], resources=[]),
            context=Context(included=[], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key
        )
    assert "cannot be empty" in str(exc_info.value)
    assert "Use ['*']" in str(exc_info.value)


def test_validate_ttl_seconds():
    """Test TTL validation"""
    private_key, public_key = generate_key_pair()

    # Valid TTL
    envelope = create_envelope(
        agent_id="agent-1",
        provider="openai",
        step_number=1,
        root_policy_id="policy-1",
        skill=Skill(id="s1", name="test", tool="Test", parameters=SkillParameters(allowed=[], constraints={})),
        authority=Authority(scopes=["read:data"], resources=["*"]),
        context=Context(included=[], excluded=[]),
        execution=ExecutionConfig(provider_config={}),
        private_key=private_key,
        ttl_seconds=300
    )
    assert envelope.ttl_seconds == 300

    # TTL too short (already enforced by Pydantic, but good to test)
    with pytest.raises((ValidationError, ValueError)) as exc_info:
        create_envelope(
            agent_id="agent-1",
            provider="openai",
            step_number=1,
            root_policy_id="policy-1",
            skill=Skill(id="s1", name="test", tool="Test", parameters=SkillParameters(allowed=[], constraints={})),
            authority=Authority(scopes=["read:data"], resources=["*"]),
            context=Context(included=[], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key,
            ttl_seconds=30
        )
    # Either ValidationError or Pydantic ValidationError
    assert "60" in str(exc_info.value) or "1 minute" in str(exc_info.value)


def test_validate_private_key():
    """Test private key validation"""
    private_key, public_key = generate_key_pair()

    # Valid private key
    envelope = create_envelope(
        agent_id="agent-1",
        provider="openai",
        step_number=1,
        root_policy_id="policy-1",
        skill=Skill(id="s1", name="test", tool="Test", parameters=SkillParameters(allowed=[], constraints={})),
        authority=Authority(scopes=["read:data"], resources=["*"]),
        context=Context(included=[], excluded=[]),
        execution=ExecutionConfig(provider_config={}),
        private_key=private_key
    )
    assert envelope.signature

    # Invalid private key (wrong length)
    with pytest.raises(ValidationError) as exc_info:
        create_envelope(
            agent_id="agent-1",
            provider="openai",
            step_number=1,
            root_policy_id="policy-1",
            skill=Skill(id="s1", name="test", tool="Test", parameters=SkillParameters(allowed=[], constraints={})),
            authority=Authority(scopes=["read:data"], resources=["*"]),
            context=Context(included=[], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key="invalid_key"
        )
    assert "64 hex characters" in str(exc_info.value)
    assert "generate_key_pair()" in str(exc_info.value)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
