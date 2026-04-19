"""Tests for FakeCompiler — deterministic, no-LLM compiler used in CI and quickstarts."""

import pytest

from authority_runtime.compiler import FakeCompiler
from authority_runtime.types import Authority, Skill, SkillParameters


def _skill(skill_id: str) -> Skill:
    return Skill(
        id=skill_id,
        name=skill_id,
        tool=f"{skill_id} tool",
        parameters=SkillParameters(allowed=[], constraints={}),
    )


@pytest.mark.asyncio
async def test_fake_compiler_keyword_match():
    compiler = FakeCompiler(
        keyword_map={
            "finance": ["vault:finance:read"],
            "audit": ["audit:read"],
        },
    )

    selection = await compiler.select_skill(
        user_request="Please read the Q1 finance summary for an audit.",
        current_step=1,
        parent_authority=Authority(scopes=["vault:finance:read", "audit:read"]),
        available_context_fields=["intent"],
        available_skills=[_skill("skill-vault-read")],
        available_scopes=["vault:finance:read", "audit:read"],
    )

    assert selection.selected_skill.id == "skill-vault-read"
    assert set(selection.required_scopes) == {"vault:finance:read", "audit:read"}
    assert selection.confidence == 1.0
    assert "finance" in selection.reasoning or "audit" in selection.reasoning


@pytest.mark.asyncio
async def test_fake_compiler_falls_back_to_first_read_scope():
    compiler = FakeCompiler()

    selection = await compiler.select_skill(
        user_request="something unspecific",
        current_step=1,
        parent_authority=Authority(scopes=["vault:shared:read", "vault:shared:write"]),
        available_context_fields=[],
        available_skills=[_skill("skill-shared")],
        available_scopes=["vault:shared:read", "vault:shared:write"],
    )

    assert selection.required_scopes == ["vault:shared:read"]


@pytest.mark.asyncio
async def test_fake_compiler_honors_default_scopes_override():
    compiler = FakeCompiler(default_scopes=["vault:shared:read"])

    selection = await compiler.select_skill(
        user_request="nothing interesting here",
        current_step=1,
        parent_authority=Authority(scopes=["vault:shared:read", "vault:finance:read"]),
        available_context_fields=[],
        available_skills=[_skill("s")],
        available_scopes=["vault:shared:read", "vault:finance:read"],
    )

    assert selection.required_scopes == ["vault:shared:read"]


@pytest.mark.asyncio
async def test_fake_compiler_drops_keyword_scopes_not_in_available():
    compiler = FakeCompiler(keyword_map={"finance": ["vault:finance:read"]})

    selection = await compiler.select_skill(
        user_request="read finance data",
        current_step=1,
        parent_authority=Authority(scopes=["vault:shared:read"]),
        available_context_fields=[],
        available_skills=[_skill("s")],
        available_scopes=["vault:shared:read"],
    )

    # vault:finance:read is not in available_scopes so it must not be returned.
    assert "vault:finance:read" not in selection.required_scopes
    assert selection.required_scopes == ["vault:shared:read"]


@pytest.mark.asyncio
async def test_fake_compiler_requires_at_least_one_skill():
    compiler = FakeCompiler()

    with pytest.raises(ValueError, match="at least one available skill"):
        await compiler.select_skill(
            user_request="x",
            current_step=1,
            parent_authority=Authority(scopes=[]),
            available_context_fields=[],
            available_skills=[],
            available_scopes=[],
        )
