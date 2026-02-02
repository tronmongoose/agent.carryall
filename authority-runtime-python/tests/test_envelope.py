"""
Tests for Authority Envelope system
"""

import pytest
from datetime import datetime, timedelta

from authority_runtime.envelope import (
    generate_key_pair,
    create_envelope,
    create_simple_envelope,
    create_child_envelope,
    validate_envelope,
    narrow_authority,
)
from authority_runtime.types import (
    Skill,
    SkillParameters,
    Authority,
    Context,
    ExecutionConfig,
)
from authority_runtime.validation import ValidationError


def test_generate_key_pair():
    """Test Ed25519 key pair generation"""
    private_key, public_key = generate_key_pair()

    assert isinstance(private_key, str)
    assert isinstance(public_key, str)
    assert len(private_key) == 64  # 32 bytes hex = 64 chars
    assert len(public_key) == 64


def test_create_envelope():
    """Test envelope creation with signature"""
    private_key, public_key = generate_key_pair()

    skill = Skill(
        id="skill-001",
        name="test_skill",
        tool="Test tool",
        parameters=SkillParameters(allowed=["param1"], constraints={"param1": "string"}),
    )

    authority = Authority(scopes=["read:user"], resources=["*"], constraints={})
    context = Context(included=["email"], excluded=[], max_size_bytes=10000)
    execution = ExecutionConfig(provider_config={"openai": {"skill_name": "test_skill"}})

    envelope = create_envelope(
        agent_id="test-agent",
        provider="openai",
        step_number=1,
        root_policy_id="policy-001",
        skill=skill,
        authority=authority,
        context=context,
        execution=execution,
        private_key=private_key,
        ttl_seconds=300,
    )

    assert envelope.agent_id == "test-agent"
    assert envelope.provider == "openai"
    assert envelope.step_number == 1
    assert envelope.signature != ""
    assert len(envelope.envelope_id) > 0


def test_validate_envelope_signature():
    """Test envelope signature validation"""
    private_key, public_key = generate_key_pair()

    skill = Skill(
        id="skill-001",
        name="test_skill",
        tool="Test tool",
        parameters=SkillParameters(allowed=["param1"], constraints={"param1": "string"}),
    )

    authority = Authority(scopes=["read:user"], resources=["*"], constraints={})
    context = Context(included=["email"], excluded=[], max_size_bytes=10000)
    execution = ExecutionConfig(provider_config={"openai": {"skill_name": "test_skill"}})

    envelope = create_envelope(
        agent_id="test-agent",
        provider="openai",
        step_number=1,
        root_policy_id="policy-001",
        skill=skill,
        authority=authority,
        context=context,
        execution=execution,
        private_key=private_key,
        ttl_seconds=300,
    )

    # Valid signature
    validation = validate_envelope(envelope, public_key=public_key)
    assert validation["valid"] is True
    assert len(validation["errors"]) == 0


def test_narrow_authority():
    """Test authority narrowing"""
    private_key, _ = generate_key_pair()

    skill = Skill(
        id="skill-001",
        name="test_skill",
        tool="Test tool",
        parameters=SkillParameters(allowed=["param1"], constraints={"param1": "string"}),
    )

    parent_authority = Authority(
        scopes=["read:user", "write:user", "send:email"],
        resources=["*"],
        constraints={},
    )
    parent_context = Context(
        included=["email", "name", "bio", "preferences"],
        excluded=[],
        max_size_bytes=10000,
    )
    execution = ExecutionConfig(provider_config={"openai": {"skill_name": "test_skill"}})

    parent_envelope = create_envelope(
        agent_id="test-agent",
        provider="openai",
        step_number=1,
        root_policy_id="policy-001",
        skill=skill,
        authority=parent_authority,
        context=parent_context,
        execution=execution,
        private_key=private_key,
        ttl_seconds=300,
    )

    # Narrow to minimal scopes and context
    result = narrow_authority(
        parent_envelope,
        required_scopes=["read:user"],
        required_context_fields=["email"],
    )

    assert result.narrowed_authority.scopes == ["read:user"]
    assert result.narrowed_context.included == ["email"]
    assert result.authority_reduction_ratio > 0.5  # 2/3 scopes removed
    assert result.context_reduction_ratio > 0.5  # 3/4 fields removed


def test_narrow_authority_rejects_invalid_scopes():
    """Test that narrowing rejects scopes not in parent"""
    private_key, _ = generate_key_pair()

    skill = Skill(
        id="skill-001",
        name="test_skill",
        tool="Test tool",
        parameters=SkillParameters(allowed=["param1"], constraints={"param1": "string"}),
    )

    parent_authority = Authority(scopes=["read:user"], resources=["*"], constraints={})
    parent_context = Context(included=["email"], excluded=[], max_size_bytes=10000)
    execution = ExecutionConfig(provider_config={"openai": {"skill_name": "test_skill"}})

    parent_envelope = create_envelope(
        agent_id="test-agent",
        provider="openai",
        step_number=1,
        root_policy_id="policy-001",
        skill=skill,
        authority=parent_authority,
        context=parent_context,
        execution=execution,
        private_key=private_key,
        ttl_seconds=300,
    )

    # Try to narrow to scopes not in parent (should fail)
    with pytest.raises(ValidationError) as exc_info:
        narrow_authority(
            parent_envelope,
            required_scopes=["write:user"],  # Not in parent!
            required_context_fields=["email"],
        )

    assert "not in parent" in str(exc_info.value)
    assert exc_info.value.field == "required_scopes"


def test_validate_child_parent_relationship():
    """Test validation of child ⊆ parent invariant"""
    private_key, public_key = generate_key_pair()

    skill = Skill(
        id="skill-001",
        name="test_skill",
        tool="Test tool",
        parameters=SkillParameters(allowed=["param1"], constraints={"param1": "string"}),
    )

    # Create parent
    parent_authority = Authority(
        scopes=["read:user", "write:user"],
        resources=["*"],
        constraints={},
    )
    parent_context = Context(included=["email", "name"], excluded=[], max_size_bytes=10000)
    execution = ExecutionConfig(provider_config={"openai": {"skill_name": "test_skill"}})

    parent_envelope = create_envelope(
        agent_id="test-agent",
        provider="openai",
        step_number=1,
        root_policy_id="policy-001",
        skill=skill,
        authority=parent_authority,
        context=parent_context,
        execution=execution,
        private_key=private_key,
        ttl_seconds=600,
    )

    # Create valid child (subset of parent)
    narrowing = narrow_authority(
        parent_envelope,
        required_scopes=["read:user"],
        required_context_fields=["email"],
    )

    child_envelope = create_envelope(
        agent_id="test-agent",
        provider="openai",
        step_number=2,
        root_policy_id="policy-001",
        skill=skill,
        authority=narrowing.narrowed_authority,
        context=narrowing.narrowed_context,
        execution=execution,
        private_key=private_key,
        parent_envelope_id=parent_envelope.envelope_id,
        ttl_seconds=300,
    )

    # Validate child against parent
    validation = validate_envelope(child_envelope, parent_envelope, public_key)
    assert validation["valid"] is True
    assert len(validation["errors"]) == 0


class TestCreateSimpleEnvelope:
    """Tests for create_simple_envelope() helper"""

    def test_minimal_usage(self):
        """Test creating envelope with minimal parameters"""
        private_key, public_key = generate_key_pair()

        envelope = create_simple_envelope(
            agent_id="my-agent",
            scopes=["read:files", "write:files"],
            private_key=private_key,
        )

        # Check basic fields
        assert envelope.agent_id == "my-agent"
        assert envelope.authority.scopes == ["read:files", "write:files"]
        assert envelope.provider == "claude"  # default
        assert envelope.step_number == 1
        assert envelope.signature != ""

        # Check defaults were applied
        assert envelope.authority.resources == ["*"]
        assert envelope.context.included == ["user_id", "session_id"]
        assert envelope.root_policy_id == "policy-my-agent"

        # Verify signature is valid
        validation = validate_envelope(envelope, public_key=public_key)
        assert validation["valid"] is True

    def test_custom_options(self):
        """Test creating envelope with custom options"""
        private_key, _ = generate_key_pair()

        envelope = create_simple_envelope(
            agent_id="custom-agent",
            scopes=["read:db"],
            private_key=private_key,
            skill_name="database-reader",
            resources=["/data/*"],
            context_fields=["user_id", "org_id", "role"],
            provider="openai",
            ttl_seconds=600,
            root_policy_id="custom-policy-123",
        )

        assert envelope.skill.name == "database-reader"
        assert envelope.authority.resources == ["/data/*"]
        assert envelope.context.included == ["user_id", "org_id", "role"]
        assert envelope.provider == "openai"
        assert envelope.ttl_seconds == 600
        assert envelope.root_policy_id == "custom-policy-123"


class TestCreateChildEnvelope:
    """Tests for create_child_envelope() helper"""

    def test_basic_child_creation(self):
        """Test creating child envelope with narrowed scopes"""
        private_key, public_key = generate_key_pair()

        # Create parent with broad permissions
        parent = create_simple_envelope(
            agent_id="my-agent",
            scopes=["read:files", "write:files", "delete:files"],
            private_key=private_key,
            context_fields=["user_id", "session_id", "org_id"],
        )

        # Create child with narrowed permissions
        child = create_child_envelope(
            parent_envelope=parent,
            scopes=["read:files"],
            private_key=private_key,
        )

        # Verify narrowing
        assert child.authority.scopes == ["read:files"]
        assert child.step_number == parent.step_number + 1
        assert child.parent_envelope_id == parent.envelope_id
        assert child.agent_id == parent.agent_id

        # Verify signature
        validation = validate_envelope(child, public_key=public_key)
        assert validation["valid"] is True

        # Verify child ⊆ parent
        validation = validate_envelope(child, parent_envelope=parent, public_key=public_key)
        assert validation["valid"] is True

    def test_child_rejects_privilege_escalation(self):
        """Test that child cannot request scopes not in parent"""
        private_key, _ = generate_key_pair()

        parent = create_simple_envelope(
            agent_id="my-agent",
            scopes=["read:files"],
            private_key=private_key,
        )

        # Try to escalate privileges - should fail
        with pytest.raises(ValidationError) as exc_info:
            create_child_envelope(
                parent_envelope=parent,
                scopes=["write:files"],  # Not in parent!
                private_key=private_key,
            )

        assert "not in parent" in str(exc_info.value)
        assert exc_info.value.field == "required_scopes"

    def test_child_cannot_outlive_parent(self):
        """Test that child TTL is capped at parent's remaining time"""
        private_key, _ = generate_key_pair()

        # Create parent with 120 second TTL (gives room for test execution)
        parent = create_simple_envelope(
            agent_id="my-agent",
            scopes=["read:files"],
            private_key=private_key,
            ttl_seconds=120,  # 2 minutes
        )

        # Create child - even if we request longer TTL, it should be capped
        child = create_child_envelope(
            parent_envelope=parent,
            scopes=["read:files"],
            private_key=private_key,
            ttl_seconds=600,  # Request 10 minutes
        )

        # Child TTL should be <= parent's remaining time (approximately 120 seconds)
        # Allow 5 second buffer for test execution time
        assert child.ttl_seconds <= 120
        assert child.ttl_seconds >= 60  # Should still be at least 60 (minimum)

    def test_child_with_narrowed_context(self):
        """Test creating child with narrowed context fields"""
        private_key, _ = generate_key_pair()

        parent = create_simple_envelope(
            agent_id="my-agent",
            scopes=["read:files"],
            private_key=private_key,
            context_fields=["user_id", "session_id", "org_id"],
        )

        child = create_child_envelope(
            parent_envelope=parent,
            scopes=["read:files"],
            private_key=private_key,
            context_fields=["user_id"],  # Narrower context
        )

        assert child.context.included == ["user_id"]

    def test_child_rejects_invalid_context_fields(self):
        """Test that child cannot request context fields not in parent"""
        private_key, _ = generate_key_pair()

        parent = create_simple_envelope(
            agent_id="my-agent",
            scopes=["read:files"],
            private_key=private_key,
            context_fields=["user_id"],
        )

        with pytest.raises(ValidationError) as exc_info:
            create_child_envelope(
                parent_envelope=parent,
                scopes=["read:files"],
                private_key=private_key,
                context_fields=["user_id", "secret_data"],  # secret_data not in parent
            )

        assert "not in parent" in str(exc_info.value)
        assert exc_info.value.field == "required_context_fields"
