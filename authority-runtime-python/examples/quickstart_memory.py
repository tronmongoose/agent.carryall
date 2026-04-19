"""
Quickstart: end-to-end Authority Runtime flow with zero external dependencies.

Run this to see the whole loop in under a second:

    pip install authority-runtime
    python examples/quickstart_memory.py

What it does:
    1. Generates an Ed25519 keypair.
    2. Builds a parent (root) envelope granting broad scopes.
    3. Uses FakeCompiler (no LLM, no API key) to pick minimal scopes for an intent.
    4. Narrows authority into a signed child envelope.
    5. Asks MemoryBackend to check access for an in-scope and an out-of-scope URI.
    6. Prints the signed envelope and the two policy decisions.

No OpenAI key, no Anthropic key, no SLOS, no network.
"""

from __future__ import annotations

import asyncio
import json

from authority_runtime import (
    Authority,
    Context,
    ExecutionConfig,
    Skill,
    SkillParameters,
    create_envelope,
    generate_key_pair,
    validate_envelope,
)
from authority_runtime.envelope import narrow_authority
from authority_runtime.backends import MemoryBackend, Decision, load_backend
from authority_runtime.compiler import FakeCompiler


AGENT_ID = "quickstart-agent"
ROOT_POLICY_ID = "policy-quickstart"


def build_root_envelope(private_key: str) -> object:
    skill = Skill(
        id="skill-vault-read",
        name="vault-read",
        tool="Read documents from a vault",
        parameters=SkillParameters(allowed=["vault", "document_id"], constraints={}),
    )
    authority = Authority(
        scopes=["vault:finance:read", "vault:shared:read", "audit:read"],
        resources=["*"],
        constraints={},
    )
    context = Context(
        included=["intent", "purpose"],
        excluded=[],
        max_size_bytes=10_000,
    )
    return create_envelope(
        agent_id=AGENT_ID,
        provider="custom",
        step_number=0,
        root_policy_id=ROOT_POLICY_ID,
        skill=skill,
        authority=authority,
        context=context,
        execution=ExecutionConfig(provider_config={}),
        private_key=private_key,
        ttl_seconds=600,
    )


async def run() -> None:
    print("Authority Runtime quickstart — MemoryBackend + FakeCompiler\n")

    private_key, public_key = generate_key_pair()
    root_envelope = build_root_envelope(private_key)
    print(f"Root envelope:  {root_envelope.envelope_id}")
    print(f"  scopes:       {root_envelope.authority.scopes}")

    compiler = FakeCompiler(
        keyword_map={
            "finance": ["vault:finance:read"],
            "audit": ["audit:read"],
            "shared": ["vault:shared:read"],
        },
    )
    selection = await compiler.select_skill(
        user_request="Read the Q1 finance summary so I can audit scope creep.",
        current_step=1,
        parent_authority=root_envelope.authority,
        available_context_fields=root_envelope.context.included,
        available_skills=[root_envelope.skill],
        available_scopes=root_envelope.authority.scopes,
    )
    print(f"\nCompiled intent -> scopes: {selection.required_scopes}")
    print(f"  reasoning: {selection.reasoning}")

    narrowing = narrow_authority(
        parent_envelope=root_envelope,
        required_scopes=selection.required_scopes,
        required_context_fields=selection.required_context_fields,
    )
    child = create_envelope(
        agent_id=AGENT_ID,
        provider="custom",
        step_number=1,
        root_policy_id=ROOT_POLICY_ID,
        skill=selection.selected_skill,
        authority=narrowing.narrowed_authority,
        context=narrowing.narrowed_context,
        execution=ExecutionConfig(provider_config={}),
        private_key=private_key,
        parent_envelope_id=root_envelope.envelope_id,
        ttl_seconds=300,
    )
    print(f"\nChild envelope: {child.envelope_id}")
    print(f"  scopes:       {child.authority.scopes}")
    print(f"  signature:    {child.signature[:32]}...")

    validation = validate_envelope(child, parent_envelope=root_envelope, public_key=public_key)
    print(f"  signed+valid: {validation['valid']}")

    backend: MemoryBackend = load_backend()  # no config -> default MemoryBackend
    assert isinstance(backend, MemoryBackend)
    backend = MemoryBackend(initial_data={
        "finance": {
            "q1-summary": {
                "content": "# Q1 Finance Summary\nRevenue: $1.2M",
                "title": "Q1 Finance Summary",
                "sensitivity": "confidential",
            },
        },
        "hr": {
            "headcount-2026": {
                "content": "# Headcount\n42 engineers",
                "title": "HR Headcount 2026",
                "sensitivity": "restricted",
                "denied_agents": [AGENT_ID],
            },
        },
    })

    ok = backend.check_access(child, "read", "slos://vaults/finance/q1-summary")
    no = backend.check_access(child, "read", "slos://vaults/hr/headcount-2026")
    print(f"\nAccess check (finance/q1-summary):  {ok}")
    print(f"Access check (hr/headcount-2026):   {no}")

    # Full envelope (for interview / debugging) as canonical JSON.
    print("\nEnvelope payload:")
    print(json.dumps(child.model_dump(), indent=2, sort_keys=True, default=str))

    assert ok.decision == Decision.ALLOW, "expected ALLOW on in-scope URI"
    assert no.decision == Decision.DENY, "expected DENY on out-of-scope URI"
    print("\nAll expected decisions matched. Quickstart complete.")


if __name__ == "__main__":
    asyncio.run(run())
