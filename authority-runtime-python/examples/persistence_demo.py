#!/usr/bin/env python3
"""
Persistence Demo: SQLite-backed Envelope and Audit Storage

This demo shows how to use EnvelopeStore for production-ready persistence:
- Save envelopes to SQLite database
- Query envelopes by agent, policy, time range
- Store audit trail with decision context
- Retrieve full delegation chains
- Generate compliance statistics

Run: python examples/persistence_demo.py
"""

import os
from authority_runtime import (
    create_envelope,
    generate_key_pair,
    EnvelopeStore,
    EnforcedTool,
    Skill,
    SkillParameters,
    Authority,
    Context,
    ExecutionConfig,
    DecisionContext,
    create_audit_entry,
)


def main():
    print("=" * 70)
    print("ENVELOPE PERSISTENCE DEMO")
    print("Production-ready SQLite storage for envelopes and audit trail")
    print("=" * 70)
    print()

    # Setup - use local database
    db_path = "./authority_demo.db"
    if os.path.exists(db_path):
        os.remove(db_path)  # Start fresh for demo
        print(f"🗑️  Removed existing database")

    store = EnvelopeStore(db_path)
    print(f"✅ Created database: {db_path}")
    print()

    # Generate signing keys
    private_key, public_key = generate_key_pair()

    # =========================================================================
    # SCENARIO 1: Create and persist root envelope
    # =========================================================================
    print("SCENARIO 1: Creating Root Envelope")
    print("-" * 70)

    root_envelope = create_envelope(
        agent_id="production-agent-001",
        provider="openai",
        step_number=1,
        root_policy_id="prod-policy-v2",
        skill=Skill(
            id="skill-root",
            name="admin_operations",
            tool="Admin toolkit",
            parameters=SkillParameters(allowed=["user_id", "resource_id"], constraints={})
        ),
        authority=Authority(
            scopes=["read:users", "write:users", "read:resources"],
            resources=["*"]
        ),
        context=Context(included=["user_id", "resource_id"], excluded=["password"]),
        execution=ExecutionConfig(provider_config={}),
        private_key=private_key,
        ttl_seconds=3600,  # 1 hour
    )

    store.save_envelope(root_envelope)
    print(f"✅ Saved root envelope: {root_envelope.envelope_id}")
    print(f"   - Agent: {root_envelope.agent_id}")
    print(f"   - Scopes: {root_envelope.authority.scopes}")
    print(f"   - TTL: {root_envelope.ttl_seconds}s")
    print()

    # =========================================================================
    # SCENARIO 2: Create child envelope (narrowed authority)
    # =========================================================================
    print("SCENARIO 2: Creating Child Envelope (Delegated Authority)")
    print("-" * 70)

    child_envelope = create_envelope(
        agent_id="sub-agent-readonly",
        provider="openai",
        step_number=2,
        root_policy_id="prod-policy-v2",
        parent_envelope_id=root_envelope.envelope_id,  # Link to parent
        skill=Skill(
            id="skill-child",
            name="read_operations",
            tool="Read-only toolkit",
            parameters=SkillParameters(allowed=["user_id"], constraints={})
        ),
        authority=Authority(
            scopes=["read:users"],  # Narrowed from parent (removed write:users, read:resources)
            resources=["*"]
        ),
        context=Context(included=["user_id"], excluded=["password"]),
        execution=ExecutionConfig(provider_config={}),
        private_key=private_key,
        ttl_seconds=1800,  # 30 minutes (less than parent)
    )

    store.save_envelope(child_envelope)
    print(f"✅ Saved child envelope: {child_envelope.envelope_id}")
    print(f"   - Parent: {child_envelope.parent_envelope_id}")
    print(f"   - Agent: {child_envelope.agent_id}")
    print(f"   - Scopes: {child_envelope.authority.scopes} (narrowed)")
    print(f"   - TTL: {child_envelope.ttl_seconds}s (shorter than parent)")
    print()

    # =========================================================================
    # SCENARIO 3: Execute action with decision context + audit
    # =========================================================================
    print("SCENARIO 3: Action with Decision Context + Audit Trail")
    print("-" * 70)

    # Create envelope with decision context
    action_envelope = create_envelope(
        agent_id="compliance-agent",
        provider="openai",
        step_number=1,
        root_policy_id="compliance-policy",
        skill=Skill(
            id="skill-delete",
            name="user_deletion",
            tool="Delete user account",
            parameters=SkillParameters(allowed=["user_id"], constraints={})
        ),
        authority=Authority(
            scopes=["delete:users"],
            resources=["user-*"]
        ),
        context=Context(included=["user_id", "reason"], excluded=["payment_info"]),
        execution=ExecutionConfig(provider_config={}),
        private_key=private_key,
        ttl_seconds=300,
        decision_context=DecisionContext(
            intent="GDPR Article 17 deletion request",
            inputs={
                "user_request": "Delete my account under GDPR Article 17",
                "user_tier": "premium",
                "support_ticket": "#12345",
            },
            constraints_applied=[
                "GDPR Article 17 compliance required",
                "Premium tier has 90-day retention obligation",
            ],
            alternatives_considered=[
                "Immediate permanent deletion",
                "90-day soft delete (selected)",
                "Account anonymization",
            ],
            selected_because=(
                "Premium tier has contractual 90-day retention obligation. "
                "Soft delete satisfies both GDPR compliance and retention requirements."
            ),
            policy_references=["gdpr-article-17", "premium-retention-policy"],
            confidence=0.98,
            risk_factors=["Sensitive user data handling"],
        ),
    )

    store.save_envelope(action_envelope)
    print(f"✅ Saved envelope with decision context: {action_envelope.envelope_id}")
    print(f"   - Intent: {action_envelope.decision_context.intent}")
    print(f"   - Confidence: {action_envelope.decision_context.confidence}")
    print()

    # Create and save audit entry
    audit_entry = create_audit_entry(
        action="soft_delete_user",
        envelope=action_envelope,
        public_key=public_key,
        result="success",
        user_id="user-67890",
        retention_days=90,
    )

    store.save_audit_entry(audit_entry)
    print(f"✅ Saved audit entry: {audit_entry.action}")
    print(f"   - Result: {audit_entry.result}")
    print(f"   - Signature Valid: {audit_entry.signature_valid}")
    print()

    # =========================================================================
    # SCENARIO 4: Query database
    # =========================================================================
    print("SCENARIO 4: Querying Stored Data")
    print("-" * 70)

    # Query envelopes by agent
    agent_envelopes = store.get_envelopes_by_agent("production-agent-001")
    print(f"✅ Query by agent 'production-agent-001': {len(agent_envelopes)} envelope(s)")
    for env in agent_envelopes:
        print(f"   - {env.envelope_id}: {env.authority.scopes}")
    print()

    # Get delegation chain
    chain = store.get_envelope_chain(child_envelope.envelope_id)
    print(f"✅ Delegation chain for {child_envelope.envelope_id}:")
    for i, env in enumerate(chain):
        indent = "   " + ("  " * i)
        print(f"{indent}{'└─' if i > 0 else '├─'} {env.envelope_id}")
        print(f"{indent}   Agent: {env.agent_id}")
        print(f"{indent}   Scopes: {env.authority.scopes}")
    print()

    # Query audit trail
    audit_trail = store.get_audit_trail(agent_id="compliance-agent")
    print(f"✅ Audit trail for 'compliance-agent': {len(audit_trail)} entry/entries")
    for entry in audit_trail:
        print(f"   - Action: {entry['action']}")
        print(f"     Result: {entry['result']}")
        print(f"     Timestamp: {entry['timestamp']}")
        if entry['decision_context']:
            print(f"     Intent: {entry['decision_context']['intent']}")
    print()

    # Get statistics
    stats = store.get_stats()
    print("✅ Database Statistics:")
    print(f"   Envelopes:")
    print(f"     - Total: {stats['envelopes']['total']}")
    print(f"     - Unique Agents: {stats['envelopes']['unique_agents']}")
    print(f"     - Unique Policies: {stats['envelopes']['unique_policies']}")
    print(f"   Audit Trail:")
    print(f"     - Total Actions: {stats['audit_trail']['total_actions']}")
    print(f"     - Successful: {stats['audit_trail']['successful']}")
    print(f"     - Blocked: {stats['audit_trail']['blocked']}")
    print(f"     - Signature Failures: {stats['audit_trail']['signature_failures']}")
    print()

    # =========================================================================
    # PRODUCTION VALUE
    # =========================================================================
    print("=" * 70)
    print("PRODUCTION VALUE")
    print("=" * 70)
    print("""
With EnvelopeStore, you get:

✅ Persistent Authorization Records
   - Every envelope is cryptographically signed and stored
   - Full audit trail for compliance (SOC2, GDPR, HIPAA)

✅ Decision Context Captured
   - Know WHY decisions were made, not just WHAT happened
   - Reconstruct decision logic months later for audits

✅ Delegation Chain Reconstruction
   - Trace authority from child → parent → root
   - Prove that delegated permissions never exceeded parent authority

✅ Query & Analytics
   - Filter by agent, policy, time range, result
   - Identify patterns (which agents are blocked most often?)
   - Compliance reporting with cryptographic proof

✅ Production-Ready
   - SQLite for local/embedded use
   - Indexed queries for performance
   - Ready to extend to PostgreSQL/MySQL for scale
""")

    print(f"Database saved to: {os.path.abspath(db_path)}")
    print(f"Run: sqlite3 {db_path}")
    print(f"  SELECT * FROM envelopes;")
    print(f"  SELECT * FROM audit_trail;")


if __name__ == "__main__":
    main()
