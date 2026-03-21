#!/usr/bin/env python3
"""
LangGraph Integration Demo: Authority Runtime with LangGraph

This demo shows how to use Authority Runtime with LangGraph's graph-based
state machine architecture for intelligent permission management.

Key Features Demonstrated:
- LangGraph StateGraph with Authority Runtime
- Automatic authority narrowing at each step
- EnforcedTool integration with LangGraph ToolNode
- Envelope persistence to SQLite
- Audit trail tracking

Run: python examples/langgraph_demo.py
"""

import os
from authority_runtime import (
    generate_key_pair,
    create_authority_graph,
    EnforcedTool,
    EnvelopeStore,
    AuthorityState,
)


def main():
    print("=" * 70)
    print("LANGGRAPH + AUTHORITY RUNTIME DEMO")
    print("Intelligent permission management for graph-based AI agents")
    print("=" * 70)
    print()

    # Setup database
    db_path = "./langgraph_authority.db"
    if os.path.exists(db_path):
        os.remove(db_path)
        print(f"🗑️  Removed existing database")

    # Generate signing keys
    private_key, public_key = generate_key_pair()
    print(f"🔑 Generated Ed25519 key pair")
    print()

    # =========================================================================
    # SCENARIO: Define Enforced Tools
    # =========================================================================
    print("📦 Defining EnforcedTools")
    print("-" * 70)

    def search_users(email: str) -> str:
        """Search for users by email"""
        # Simulated database lookup
        if "john" in email.lower():
            return f"Found user: John Doe ({email}), ID: user-123"
        return f"No user found with email: {email}"

    def update_user_profile(user_id: str, field: str, value: str) -> str:
        """Update a user's profile field"""
        # Simulated database update
        return f"Updated {field}={value} for {user_id}"

    def delete_user(user_id: str) -> str:
        """Delete a user account (admin only)"""
        # Simulated dangerous operation
        return f"DELETED user {user_id}"

    # Wrap tools with enforcement
    secure_search = EnforcedTool(
        name="search_users",
        func=search_users,
        required_scope="read:users",
        public_key=public_key,
        description="Search for users by email (requires read:users scope)"
    )

    secure_update = EnforcedTool(
        name="update_user_profile",
        func=update_user_profile,
        required_scope="write:users",
        public_key=public_key,
        description="Update user profile (requires write:users scope)"
    )

    secure_delete = EnforcedTool(
        name="delete_user",
        func=delete_user,
        required_scope="delete:users",
        public_key=public_key,
        description="Delete user account (requires delete:users scope - ADMIN ONLY)"
    )

    tools = [secure_search, secure_update, secure_delete]
    print(f"✅ Created {len(tools)} EnforcedTools:")
    for tool in tools:
        print(f"   - {tool.name}: {tool.required_scope}")
    print()

    # =========================================================================
    # SCENARIO: Create Authority-Enabled LangGraph
    # =========================================================================
    print("🏗️  Creating Authority-Enabled LangGraph")
    print("-" * 70)

    # Check if OpenAI API key is available
    has_api_key = bool(os.getenv("OPENAI_API_KEY"))

    if not has_api_key:
        print("⚠️  No OPENAI_API_KEY found in environment")
        print("   Set OPENAI_API_KEY to enable full LangGraph demonstration")
        print("   Skipping graph creation for now...")
        print()
        print("   To run the full demo:")
        print("   export OPENAI_API_KEY=sk-your-key-here")
        print("   python examples/langgraph_demo.py")
        print()
        # Still create root envelope to demonstrate database functionality
        from authority_runtime import (
            create_envelope,
            Skill,
            SkillParameters,
            Authority,
            Context,
            ExecutionConfig,
        )
        root_skill = Skill(
            id="root",
            name="root_authority",
            tool="Full agent authority",
            parameters=SkillParameters(allowed=[], constraints={})
        )
        root_envelope = create_envelope(
            agent_id="langgraph-agent",
            provider="openai",
            step_number=0,
            root_policy_id="demo-policy-v1",
            skill=root_skill,
            authority=Authority(
                scopes=["read:users", "write:users"],
                resources=["*"],
            ),
            context=Context(
                included=["email", "user_id", "field", "value"],
                excluded=[],
            ),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key,
            ttl_seconds=600,
        )
        store = EnvelopeStore(db_path)
        store.save_envelope(root_envelope)
        print(f"✅ Created root envelope (demo mode - no LLM execution)")
        print(f"   - Envelope ID: {root_envelope.envelope_id}")
        print(f"   - Agent ID: langgraph-agent")
        print(f"   - Scopes: {root_envelope.authority.scopes}")
        print(f"   - Saved to database: {db_path}")
        print()
    else:
        graph = create_authority_graph(
            agent_id="langgraph-agent",
            provider="openai",
            root_policy_id="demo-policy-v1",
            initial_scopes=["read:users", "write:users"],  # Note: NO delete:users
            initial_context_fields=["email", "user_id", "field", "value"],
            tools=tools,
            private_key=private_key,
            public_key=public_key,
            model="gpt-4o-mini",
            use_compiler=False,  # Set to True to enable LLM-based narrowing
            db_path=db_path,
            ttl_seconds=600,
        )

        print(f"✅ Created LangGraph with Authority Runtime")
        print(f"   - Agent ID: langgraph-agent")
        print(f"   - Initial Scopes: [read:users, write:users]")
        print(f"   - Initial Context: [email, user_id, field, value]")
        print(f"   - Tools: {len(tools)} EnforcedTools")
        print(f"   - Database: {db_path}")
        print()

    # =========================================================================
    # SCENARIO 1: Allowed Operation (Read)
    # =========================================================================
    print("SCENARIO 1: Allowed Operation (Search Users)")
    print("-" * 70)

    # NOTE: The current implementation has a simplified architecture where
    # envelopes are tracked in state but tools need envelope binding.
    # This is a known limitation that will be addressed in the next iteration.

    print("⏭️  Skipped for now - requires additional tool binding logic")
    print("   Next iteration will implement:")
    print("   - Automatic envelope injection into tool calls")
    print("   - Tool execution tracking in audit trail")
    print("   - LLM compiler integration for authority narrowing")
    print()

    # =========================================================================
    # Query Database
    # =========================================================================
    print("SCENARIO 2: Query Envelope Database")
    print("-" * 70)

    store = EnvelopeStore(db_path)

    # Get all envelopes
    all_envelopes = store.get_envelopes_by_agent("langgraph-agent")
    print(f"✅ Found {len(all_envelopes)} envelope(s) for 'langgraph-agent':")
    for env in all_envelopes:
        print(f"   - {env.envelope_id}")
        print(f"     Scopes: {env.authority.scopes}")
        print(f"     Created: {env.created_at}")
    print()

    # Get statistics
    stats = store.get_stats()
    print("✅ Database Statistics:")
    print(f"   Envelopes:")
    print(f"     - Total: {stats['envelopes']['total']}")
    print(f"     - Unique Agents: {stats['envelopes']['unique_agents']}")
    print(f"   Audit Trail:")
    print(f"     - Total Actions: {stats['audit_trail']['total_actions']}")
    print()

    # =========================================================================
    # Architecture Explanation
    # =========================================================================
    print("=" * 70)
    print("LANGGRAPH INTEGRATION ARCHITECTURE")
    print("=" * 70)
    print("""
The new LangGraph integration provides:

✅ AuthorityState TypedDict
   - Extends LangGraph state with envelope tracking
   - Fields: messages, parent_envelope, envelope_chain, step_number

✅ create_authority_node()
   - Wraps LangGraph nodes with authority enforcement
   - Creates child envelopes with narrowed permissions
   - Integrates with LLM compiler for intelligent narrowing

✅ create_authority_graph()
   - Factory for authority-enabled LangGraph agents
   - Automatic root envelope creation
   - Seamless integration with EnforcedTools

✅ EnforcedTool.to_langchain_tool()
   - Converts EnforcedTools to LangChain StructuredTools
   - Compatible with LangGraph ToolNode
   - Maintains permission enforcement

Architecture Diagram:

┌─────────────────────────────────────────────────────────────┐
│                    User Request                              │
│               "Find user john@example.com"                   │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                 Root Envelope (Step 0)                       │
│  Authority: [read:users, write:users]                      │
│  Context: [email, user_id, field, value]                   │
│  Signed with Ed25519 ✓                                      │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            LangGraph StateGraph Execution                    │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Agent Node (with Authority Wrapper)                  │  │
│  │  - Creates child envelope                             │  │
│  │  - Narrows permissions if using compiler              │  │
│  │  - Binds tools with envelope                          │  │
│  └────────────────────┬──────────────────────────────────┘  │
│                       │                                      │
│                       ▼                                      │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Tool Node (EnforcedTool + Envelope)                  │  │
│  │  - Validates envelope signature                       │  │
│  │  - Checks TTL expiration                              │  │
│  │  - Verifies required scope                            │  │
│  │  - Executes tool if valid                             │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                Envelope Store (SQLite)                       │
│  - Saves all envelopes                                      │
│  - Tracks delegation chains                                 │
│  - Audit trail with decision context                        │
│  - Compliance-ready export                                  │
└─────────────────────────────────────────────────────────────┘

Compared to Old AuthorityWrapper:

❌ Old (Broken):
   - Wrapped LangChain AgentExecutor (incompatible with LangGraph)
   - Sequential tool loop (doesn't match graph execution)
   - State management mismatch

✅ New (Working):
   - Native LangGraph integration at node level
   - AuthorityState TypedDict for proper state management
   - Graph-based execution with authority narrowing
   - Seamless EnforcedTool integration

Next Steps:
1. Add automatic envelope injection into tool calls
2. Integrate LLM compiler for authority narrowing
3. Add comprehensive tests for LangGraph integration
4. Update examples (basic_usage.py, real_world_crm.py)
""")

    print(f"Database saved to: {os.path.abspath(db_path)}")
    print(f"Run: sqlite3 {db_path}")
    print(f"  SELECT * FROM envelopes;")


if __name__ == "__main__":
    main()
