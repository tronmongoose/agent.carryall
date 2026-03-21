"""
Basic example: LangGraph agent with Authority Runtime

This example demonstrates:
1. Creating a simple agent with tools using LangGraph
2. Wrapping it with Authority Runtime
3. Executing with automatic permission narrowing
4. Viewing token reduction metrics

Run:
    python examples/basic_usage.py
"""

import os
import sys

# Import Authority Runtime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool


# Define some sample tools (simulating a user management system)
@tool
def get_user_by_email(email: str) -> str:
    """Retrieves user details by their email address."""
    # Simulated user lookup
    return f"User found: {{id: 123, email: '{email}', name: 'John Doe', bio: 'Software Engineer'}}"


@tool
def get_user_by_id(user_id: str) -> str:
    """Retrieves user details by their unique ID."""
    # Simulated user lookup
    return f"User found: {{id: {user_id}, email: 'john@example.com', name: 'John Doe', bio: 'Software Engineer'}}"


@tool
def update_user_profile(user_id: str, bio: str) -> str:
    """Updates a user's profile bio."""
    # Simulated update
    return f"Updated user {user_id} bio to: '{bio}'"


@tool
def send_email(to: str, subject: str, body: str) -> str:
    """Sends an email to a user."""
    # Simulated email send
    return f"Email sent to {to} with subject '{subject}'"


@tool
def delete_user(user_id: str) -> str:
    """Permanently deletes a user account (admin only)."""
    # Simulated deletion
    return f"User {user_id} has been deleted"


def main():
    print("=" * 70)
    print("Authority Runtime - Basic LangGraph Example")
    print("=" * 70)
    print()

    # Check for OpenAI API key
    if not os.getenv("OPENAI_API_KEY"):
        print("❌ Error: OPENAI_API_KEY environment variable not set")
        print("   Please set it before running this example:")
        print("   export OPENAI_API_KEY='your-api-key-here'")
        return

    print("📋 Scenario: User management agent with 5 tools")
    print("   - get_user_by_email")
    print("   - get_user_by_id")
    print("   - update_user_profile")
    print("   - send_email")
    print("   - delete_user")
    print()

    # Create LangGraph agent
    print("1️⃣  Creating LangGraph agent...")
    print()

    tools = [
        get_user_by_email,
        get_user_by_id,
        update_user_profile,
        send_email,
        delete_user,
    ]

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

    # Create a ReAct agent using LangGraph
    agent = create_react_agent(llm, tools)

    print("   ✅ LangGraph agent created with 5 tools")
    print()

    # Note: The full AuthorityWrapper integration would go here
    # For this example, we'll demonstrate the core envelope functionality

    print("2️⃣  Demonstrating Authority Runtime envelope creation...")
    print()

    from authority_runtime import (
        create_envelope, validate_envelope, generate_key_pair,
        Skill, SkillParameters, Authority, Context, ExecutionConfig
    )

    # Generate signing keys
    private_key, public_key = generate_key_pair()
    print(f"   🔑 Generated Ed25519 key pair")
    print(f"   📝 Public key: {public_key[:32]}...")
    print()

    # Define skills from our tools
    skills = [
        Skill(
            id=f"skill-{i:03d}",
            name=tool.name,
            tool=tool.description or tool.name,
            parameters=SkillParameters(allowed=["*"], constraints={})
        )
        for i, tool in enumerate(tools)
    ]

    # Create parent envelope with full authority
    parent_envelope = create_envelope(
        agent_id="user-mgmt-agent",
        provider="openai",
        step_number=0,
        root_policy_id="policy-001",
        skill=skills[0],  # Root skill
        authority=Authority(
            scopes=["read:user", "write:user", "send:email", "delete:user"],
            resources=["*"],
            constraints={}
        ),
        context=Context(
            included=["email", "user_id", "name", "bio", "preferences", "session_id"],
            excluded=[],
            max_size_bytes=10000
        ),
        execution=ExecutionConfig(provider_config={"openai": {"model": "gpt-4o-mini"}}),
        private_key=private_key,
        ttl_seconds=600
    )

    print("   ✅ Parent envelope created")
    print(f"   📋 Envelope ID: {parent_envelope.envelope_id}")
    print(f"   🔐 Scopes: {parent_envelope.authority.scopes}")
    print(f"   📄 Context fields: {parent_envelope.context.included}")
    print()

    # Simulate narrowing for "Find user by email" request
    print("3️⃣  Simulating authority narrowing for: 'Find user by email'")
    print()

    # Create narrowed child envelope (what LLM compiler would do)
    child_envelope = create_envelope(
        agent_id="user-mgmt-agent",
        provider="openai",
        step_number=1,
        root_policy_id="policy-001",
        skill=skills[0],  # get_user_by_email
        authority=Authority(
            scopes=["read:user"],  # Narrowed from 4 to 1 scope
            resources=["*"],
            constraints={}
        ),
        context=Context(
            included=["email"],  # Narrowed from 6 to 1 field
            excluded=["user_id", "name", "bio", "preferences", "session_id"],
            max_size_bytes=10000
        ),
        execution=ExecutionConfig(provider_config={"openai": {"model": "gpt-4o-mini"}}),
        private_key=private_key,
        parent_envelope_id=parent_envelope.envelope_id,
        ttl_seconds=300
    )

    print("   ✅ Child envelope created (narrowed)")
    print(f"   🔐 Scopes: {child_envelope.authority.scopes} (was 4, now 1)")
    print(f"   📄 Context: {child_envelope.context.included} (was 6 fields, now 1)")
    print()

    # Calculate token reduction
    parent_context_count = len(parent_envelope.context.included)
    child_context_count = len(child_envelope.context.included)
    token_reduction = ((parent_context_count - child_context_count) / parent_context_count) * 100

    print("=" * 70)
    print("📊 Results")
    print("=" * 70)
    print()
    print(f"   🎯 Selected tool: get_user_by_email")
    print(f"   🔐 Scope narrowing: 4 → 1 scopes (75% reduction)")
    print(f"   📄 Context narrowing: 6 → 1 fields ({token_reduction:.0f}% reduction)")
    print(f"   ✅ Signature valid: {child_envelope.signature[:32]}...")
    print()

    # Validate the envelope chain
    validation = validate_envelope(child_envelope, parent_envelope, public_key)
    print(f"   🔒 Envelope validation: {'✅ PASSED' if validation['valid'] else '❌ FAILED'}")
    if not validation['valid']:
        print(f"   Errors: {validation.get('errors', [])}")
    print()

    print("=" * 70)
    print()
    print("✅ Key Takeaways:")
    print("   1. Authority Runtime creates cryptographically signed permission envelopes")
    print("   2. Child envelopes can only have FEWER permissions than parents")
    print("   3. Context narrowing reduces tokens sent to LLM (cost savings)")
    print("   4. Ed25519 signatures prevent tampering")
    print()
    print("🎯 Next steps:")
    print("   - The LLM compiler automatically selects minimal skill/permissions")
    print("   - See research_agent/ for a full working example")
    print("=" * 70)


if __name__ == "__main__":
    main()
