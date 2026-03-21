#!/usr/bin/env python3
"""
Decision Context Demo: Bridging "What Happened" and "Why It Happened"

This demo shows how DecisionContext captures the FULL context of a decision:
- What was the intent?
- What data informed the decision?
- What constraints applied?
- What alternatives were considered?
- Why was this specific option chosen?

Run: python examples/decision_context_demo.py
"""

import json
from authority_runtime import (
    create_envelope,
    generate_key_pair,
    EnforcedTool,
    Skill,
    Authority,
    Context,
    ExecutionConfig,
    SkillParameters,
    DecisionContext,
    create_audit_entry,
    export_audit_trail,
)


def main():
    print("=" * 70)
    print("DECISION CONTEXT DEMO")
    print("Bridging the gap: What happened vs. Why it happened")
    print("=" * 70)
    print()

    # Setup
    private_key, public_key = generate_key_pair()
    audit_log = []

    # Define operations
    def soft_delete_account(user_id: str) -> str:
        """Soft delete - marks account as deleted but retains data for 30 days."""
        return f"Account {user_id} marked for deletion (30-day retention)"

    def anonymize_account(user_id: str) -> str:
        """Anonymize - removes PII but keeps analytics data."""
        return f"Account {user_id} anonymized (PII removed, analytics retained)"

    def hard_delete_account(user_id: str) -> str:
        """Hard delete - permanent, irreversible deletion."""
        return f"Account {user_id} PERMANENTLY DELETED (irreversible)"

    # Wrap with enforcement
    secure_soft_delete = EnforcedTool(
        name="soft_delete_account",
        func=soft_delete_account,
        required_scope="delete:account_soft",
        public_key=public_key,
    )

    secure_anonymize = EnforcedTool(
        name="anonymize_account",
        func=anonymize_account,
        required_scope="delete:account_anonymize",
        public_key=public_key,
    )

    secure_hard_delete = EnforcedTool(
        name="hard_delete_account",
        func=hard_delete_account,
        required_scope="delete:account_permanent",
        public_key=public_key,
    )

    # =========================================================================
    # SCENARIO: GDPR Article 17 "Right to be Forgotten" Request
    # =========================================================================
    print("SCENARIO: GDPR Article 17 Deletion Request")
    print("-" * 70)
    print()
    print("Incoming Request:")
    print("  User: user-12345 (tier: free, registered: 45 days ago)")
    print("  Request: 'Delete my account permanently under GDPR Article 17'")
    print("  Channel: Support ticket #98765")
    print()

    # Decision-making process
    print("Agent Decision Process:")
    print("  1. Analyzing user tier... free tier (no retention obligations)")
    print("  2. Checking GDPR compliance... Article 17 applies")
    print("  3. Evaluating alternatives...")
    print("     - Soft delete (30-day retention) - NO (user requested permanent)")
    print("     - Anonymization - NO (user explicitly said 'delete permanently')")
    print("     - Hard delete - YES (matches user intent and GDPR requirement)")
    print("  4. Risk assessment...")
    print("     - Irreversible action detected")
    print("     - No linked accounts found")
    print("     - Free tier (low business impact)")
    print()

    # Create envelope WITH decision context
    envelope_with_context = create_envelope(
        agent_id="gdpr-compliance-agent",
        provider="openai",
        step_number=1,
        root_policy_id="gdpr-policy-v2.1",
        skill=Skill(
            id="skill-delete",
            name="account_deletion",
            tool="Delete user account",
            parameters=SkillParameters(allowed=["user_id"], constraints={}),
        ),
        authority=Authority(
            scopes=["delete:account_permanent"],
            resources=["*"],
        ),
        context=Context(included=["user_id", "deletion_reason"], excluded=["payment_info"]),
        execution=ExecutionConfig(provider_config={}),
        private_key=private_key,
        ttl_seconds=300,
        decision_context=DecisionContext(
            intent="User requested permanent account deletion under GDPR Article 17",
            inputs={
                "user_request": "Delete my account permanently under GDPR Article 17",
                "user_tier": "free",
                "account_age_days": 45,
                "support_ticket": "#98765",
                "linked_accounts": 0,
            },
            constraints_applied=[
                "GDPR Article 17 (Right to be Forgotten)",
                "Free tier deletion policy (no retention obligations)",
                "Irreversible action (requires permanent deletion)",
            ],
            alternatives_considered=[
                "Soft delete with 30-day retention",
                "Account anonymization",
                "Account suspension",
            ],
            selected_because=(
                "User explicitly requested 'permanent deletion' in support ticket #98765. "
                "GDPR Article 17 requires deletion when requested and no legal basis exists "
                "for retention. Free tier has no retention obligations. "
                "Alternatives (soft delete, anonymization) do not satisfy user's explicit request."
            ),
            policy_references=[
                "gdpr-article-17",
                "privacy-policy-v2.1-section-8",
                "data-retention-policy-free-tier",
            ],
            confidence=0.95,
            escalation_reason=None,  # High confidence, no escalation needed
            risk_factors=[
                "Irreversible action",
                "Permanent data loss",
            ],
        ),
    )

    # Execute the deletion
    result = secure_hard_delete(user_id="user-12345", _envelope=envelope_with_context)
    print(f"✅ EXECUTED: {result}")
    print()

    # Record in audit log
    audit_log.append(
        create_audit_entry(
            action="hard_delete_account",
            envelope=envelope_with_context,
            public_key=public_key,
            user_id="user-12345",
        )
    )

    # =========================================================================
    # AUDIT TRAIL: Decision Context Captured
    # =========================================================================
    print("=" * 70)
    print("AUDIT TRAIL: Full Decision Context")
    print("=" * 70)
    print()

    report = export_audit_trail(audit_log, include_envelope_chain=False)

    # Extract and display the decision context
    entry = report["entries"][0]
    print("What Happened:")
    print(f"  Action: {entry['action']}")
    print(f"  Result: {entry['result']}")
    print(f"  Timestamp: {entry['timestamp']}")
    print()

    if "decision_context" in entry["envelope"]:
        dc = entry["envelope"]["decision_context"]
        print("Why It Happened:")
        print(f"  Intent: {dc['intent']}")
        print()
        print("  Inputs at Decision Time:")
        for key, value in dc["inputs"].items():
            print(f"    - {key}: {value}")
        print()
        print("  Constraints Applied:")
        for constraint in dc["constraints_applied"]:
            print(f"    - {constraint}")
        print()
        print("  Alternatives Considered:")
        for alt in dc["alternatives_considered"]:
            print(f"    - {alt}")
        print()
        print("  Selected Because:")
        print(f"    {dc['selected_because']}")
        print()
        print("  Policy References:")
        for policy in dc["policy_references"]:
            print(f"    - {policy}")
        print()
        print("  Confidence: {:.0%}".format(dc["confidence"]))
        print()
        print("  Risk Factors:")
        for risk in dc["risk_factors"]:
            print(f"    - {risk}")

    print()
    print("=" * 70)
    print("COMPLIANCE VALUE")
    print("=" * 70)
    print("""
Traditional Systems:
  Record: "User user-12345 deleted at 2026-01-03T16:30:00Z"

  Compliance Question: "Why was this account deleted?"
  Answer: "Unknown - no decision context captured"

With Decision Context:
  Record: "User user-12345 deleted at 2026-01-03T16:30:00Z"

  Compliance Question: "Why was this account deleted?"
  Answer:
    - Intent: GDPR Article 17 request (support ticket #98765)
    - Constraints: GDPR compliance, free tier policy
    - Alternatives: Soft delete and anonymization considered
    - Selected: Permanent deletion (user's explicit request)
    - Confidence: 95%
    - Policies: gdpr-article-17, privacy-policy-v2.1-section-8

This is the difference between "records" and "understanding".
""")

    # Show full JSON for reference
    print("=" * 70)
    print("FULL AUDIT TRAIL (JSON)")
    print("=" * 70)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
