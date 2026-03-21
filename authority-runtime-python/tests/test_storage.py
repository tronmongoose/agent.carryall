#!/usr/bin/env python3
"""
Test suite for EnvelopeStore persistence layer
"""

import os
import tempfile
from datetime import datetime, timezone, timedelta
from authority_runtime import (
    create_envelope,
    generate_key_pair,
    EnvelopeStore,
    Skill,
    SkillParameters,
    Authority,
    Context,
    ExecutionConfig,
    DecisionContext,
    create_audit_entry,
)


def test_envelope_storage():
    """Test basic envelope save and retrieval."""
    print("\n=== Testing Envelope Storage ===")

    # Use temporary database
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        # Setup
        private_key, public_key = generate_key_pair()
        store = EnvelopeStore(db_path)

        # Create envelope
        envelope = create_envelope(
            agent_id="test-agent-001",
            provider="openai",
            step_number=1,
            root_policy_id="test-policy",
            skill=Skill(
                id="skill-1",
                name="test_skill",
                tool="Test tool",
                parameters=SkillParameters(allowed=["test_param"], constraints={})
            ),
            authority=Authority(
                scopes=["read:test", "write:test"],
                resources=["test-resource-*"]
            ),
            context=Context(included=["test_context"], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key,
            ttl_seconds=300,
        )

        # Save envelope
        store.save_envelope(envelope)
        print(f"✅ Saved envelope: {envelope.envelope_id}")

        # Retrieve envelope
        retrieved = store.get_envelope(envelope.envelope_id)
        assert retrieved is not None, "Envelope should be retrievable"
        assert retrieved.envelope_id == envelope.envelope_id
        assert retrieved.agent_id == envelope.agent_id
        assert retrieved.authority.scopes == envelope.authority.scopes
        print(f"✅ Retrieved envelope: {retrieved.envelope_id}")

        # Query by agent
        agent_envelopes = store.get_envelopes_by_agent("test-agent-001")
        assert len(agent_envelopes) == 1
        assert agent_envelopes[0].envelope_id == envelope.envelope_id
        print(f"✅ Queried by agent: {len(agent_envelopes)} envelopes found")

    finally:
        # Cleanup
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_envelope_chain():
    """Test envelope delegation chain retrieval."""
    print("\n=== Testing Envelope Chain ===")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        private_key, public_key = generate_key_pair()
        store = EnvelopeStore(db_path)

        # Create root envelope
        root_envelope = create_envelope(
            agent_id="root-agent",
            provider="openai",
            step_number=1,
            root_policy_id="root-policy",
            skill=Skill(
                id="skill-1",
                name="root_skill",
                tool="Root tool",
                parameters=SkillParameters(allowed=["param"], constraints={})
            ),
            authority=Authority(
                scopes=["read:data", "write:data", "delete:data"],
                resources=["*"]
            ),
            context=Context(included=["context1"], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key,
            ttl_seconds=600,
        )
        store.save_envelope(root_envelope)
        print(f"✅ Created root envelope: {root_envelope.envelope_id}")

        # Create child envelope (narrowed authority)
        child_envelope = create_envelope(
            agent_id="child-agent",
            provider="openai",
            step_number=2,
            root_policy_id="root-policy",
            parent_envelope_id=root_envelope.envelope_id,
            skill=Skill(
                id="skill-2",
                name="child_skill",
                tool="Child tool",
                parameters=SkillParameters(allowed=["param"], constraints={})
            ),
            authority=Authority(
                scopes=["read:data"],  # Narrowed from parent
                resources=["*"]
            ),
            context=Context(included=["context1"], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key,
            ttl_seconds=300,
        )
        store.save_envelope(child_envelope)
        print(f"✅ Created child envelope: {child_envelope.envelope_id}")

        # Get chain
        chain = store.get_envelope_chain(child_envelope.envelope_id)
        assert len(chain) == 2, f"Expected 2 envelopes in chain, got {len(chain)}"
        assert chain[0].envelope_id == child_envelope.envelope_id
        assert chain[1].envelope_id == root_envelope.envelope_id
        print(f"✅ Retrieved chain: {len(chain)} envelopes")
        print(f"   - Child: {chain[0].envelope_id} (scopes: {chain[0].authority.scopes})")
        print(f"   - Root: {chain[1].envelope_id} (scopes: {chain[1].authority.scopes})")

    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_decision_context_persistence():
    """Test that DecisionContext is persisted and retrieved correctly."""
    print("\n=== Testing Decision Context Persistence ===")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        private_key, public_key = generate_key_pair()
        store = EnvelopeStore(db_path)

        # Create envelope with decision context
        envelope = create_envelope(
            agent_id="decision-agent",
            provider="openai",
            step_number=1,
            root_policy_id="decision-policy",
            skill=Skill(
                id="skill-1",
                name="decision_skill",
                tool="Decision tool",
                parameters=SkillParameters(allowed=["param"], constraints={})
            ),
            authority=Authority(
                scopes=["delete:account"],
                resources=["account-*"]
            ),
            context=Context(included=["user_id"], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key,
            ttl_seconds=300,
            decision_context=DecisionContext(
                intent="Test deletion with full decision context",
                inputs={
                    "user_request": "Delete my account",
                    "user_tier": "free",
                },
                constraints_applied=[
                    "Free tier deletion policy",
                    "No retention obligations",
                ],
                alternatives_considered=[
                    "Soft delete",
                    "Anonymization",
                ],
                selected_because="User explicitly requested permanent deletion",
                policy_references=["deletion-policy-v1"],
                confidence=0.95,
                risk_factors=["Irreversible action"],
            ),
        )

        store.save_envelope(envelope)
        print(f"✅ Saved envelope with decision context")

        # Retrieve and verify decision context
        retrieved = store.get_envelope(envelope.envelope_id)
        assert retrieved is not None
        assert retrieved.decision_context is not None
        assert retrieved.decision_context.intent == "Test deletion with full decision context"
        assert retrieved.decision_context.confidence == 0.95
        assert "Free tier deletion policy" in retrieved.decision_context.constraints_applied
        assert "Soft delete" in retrieved.decision_context.alternatives_considered
        print(f"✅ Retrieved decision context:")
        print(f"   - Intent: {retrieved.decision_context.intent}")
        print(f"   - Confidence: {retrieved.decision_context.confidence}")
        print(f"   - Constraints: {len(retrieved.decision_context.constraints_applied)}")
        print(f"   - Alternatives: {len(retrieved.decision_context.alternatives_considered)}")

    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_audit_trail_persistence():
    """Test audit entry storage and querying."""
    print("\n=== Testing Audit Trail Persistence ===")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        private_key, public_key = generate_key_pair()
        store = EnvelopeStore(db_path)

        # Create and save envelope
        envelope = create_envelope(
            agent_id="audit-agent",
            provider="openai",
            step_number=1,
            root_policy_id="audit-policy",
            skill=Skill(
                id="skill-1",
                name="audit_skill",
                tool="Audit tool",
                parameters=SkillParameters(allowed=["param"], constraints={})
            ),
            authority=Authority(
                scopes=["read:data"],
                resources=["*"]
            ),
            context=Context(included=["context"], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key,
            ttl_seconds=300,
        )
        store.save_envelope(envelope)

        # Create and save audit entry
        entry = create_audit_entry(
            action="read_sensitive_data",
            envelope=envelope,
            public_key=public_key,
            result="success",
            resource="file-123",
            operation="read",
        )
        store.save_audit_entry(entry)
        print(f"✅ Saved audit entry: {entry.action}")

        # Query audit trail
        trail = store.get_audit_trail(agent_id="audit-agent")
        assert len(trail) == 1
        assert trail[0]["action"] == "read_sensitive_data"
        assert trail[0]["result"] == "success"
        assert trail[0]["signature_valid"] == True
        assert trail[0]["resource"] == "file-123"
        print(f"✅ Retrieved audit trail: {len(trail)} entries")
        print(f"   - Action: {trail[0]['action']}")
        print(f"   - Result: {trail[0]['result']}")
        print(f"   - Signature Valid: {trail[0]['signature_valid']}")

        # Query by result
        successful = store.get_audit_trail(result="success")
        assert len(successful) == 1
        print(f"✅ Queried by result='success': {len(successful)} entries")

    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_statistics():
    """Test statistics aggregation."""
    print("\n=== Testing Statistics ===")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        private_key, public_key = generate_key_pair()
        store = EnvelopeStore(db_path)

        # Create multiple envelopes
        for i in range(3):
            envelope = create_envelope(
                agent_id=f"agent-{i}",
                provider="openai",
                step_number=1,
                root_policy_id=f"policy-{i % 2}",  # 2 unique policies
                skill=Skill(
                    id=f"skill-{i}",
                    name=f"skill_{i}",
                    tool=f"Tool {i}",
                    parameters=SkillParameters(allowed=[], constraints={})
                ),
                authority=Authority(scopes=["read:data"], resources=["*"]),
                context=Context(included=[], excluded=[]),
                execution=ExecutionConfig(provider_config={}),
                private_key=private_key,
                ttl_seconds=300,
            )
            store.save_envelope(envelope)

            # Create audit entries
            entry = create_audit_entry(
                action=f"action_{i}",
                envelope=envelope,
                public_key=public_key,
                result="success" if i % 2 == 0 else "blocked",
            )
            store.save_audit_entry(entry)

        # Get statistics
        stats = store.get_stats()
        assert stats["envelopes"]["total"] == 3
        assert stats["envelopes"]["unique_agents"] == 3
        assert stats["envelopes"]["unique_policies"] == 2
        assert stats["audit_trail"]["total_actions"] == 3
        assert stats["audit_trail"]["successful"] == 2  # indices 0 and 2
        assert stats["audit_trail"]["blocked"] == 1  # index 1

        print(f"✅ Statistics:")
        print(f"   - Total envelopes: {stats['envelopes']['total']}")
        print(f"   - Unique agents: {stats['envelopes']['unique_agents']}")
        print(f"   - Unique policies: {stats['envelopes']['unique_policies']}")
        print(f"   - Total actions: {stats['audit_trail']['total_actions']}")
        print(f"   - Successful: {stats['audit_trail']['successful']}")
        print(f"   - Blocked: {stats['audit_trail']['blocked']}")

    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


if __name__ == "__main__":
    print("=" * 70)
    print("ENVELOPE STORE PERSISTENCE TESTS")
    print("=" * 70)

    test_envelope_storage()
    test_envelope_chain()
    test_decision_context_persistence()
    test_audit_trail_persistence()
    test_statistics()

    print("\n" + "=" * 70)
    print("ALL TESTS PASSED ✅")
    print("=" * 70)
