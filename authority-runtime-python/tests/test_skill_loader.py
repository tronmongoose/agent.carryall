"""Tests for the SKILL.md loader and tool-access enforcer."""

from pathlib import Path

import pytest

from authority_runtime.skill_loader import (
    SkillManifest,
    SkillManifestError,
    enforce_tool_access,
    load_skill,
)


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


# ── load_skill ────────────────────────────────────────────────


def test_load_skill_parses_valid_frontmatter(tmp_path: Path):
    skill = _write(
        tmp_path / "SKILL.md",
        """---
name: my-skill
description: Does a thing
tools:
  - tool_alpha
  - tool_beta
---

# Body

Some body text.
""",
    )
    manifest = load_skill(skill)
    assert manifest.name == "my-skill"
    assert manifest.description == "Does a thing"
    assert manifest.allowed_tools == ["tool_alpha", "tool_beta"]
    assert "Some body text" in manifest.body
    assert manifest.path == skill


def test_load_skill_empty_tools_is_explicit_no_tools(tmp_path: Path):
    skill = _write(
        tmp_path / "SKILL.md",
        """---
name: noop
description: No tools
tools: []
---
""",
    )
    manifest = load_skill(skill)
    assert manifest.allowed_tools == []


def test_load_skill_missing_tools_key_defaults_to_empty(tmp_path: Path):
    skill = _write(
        tmp_path / "SKILL.md",
        """---
name: legacy
description: Old-style SKILL.md with no tools key
---
""",
    )
    manifest = load_skill(skill)
    assert manifest.allowed_tools == []


def test_load_skill_null_tools_defaults_to_empty(tmp_path: Path):
    skill = _write(
        tmp_path / "SKILL.md",
        """---
name: nulled
description: Explicit null tools
tools:
---
""",
    )
    manifest = load_skill(skill)
    assert manifest.allowed_tools == []


# ── failure modes ─────────────────────────────────────────────


def test_load_skill_missing_file_raises(tmp_path: Path):
    with pytest.raises(SkillManifestError, match="not found"):
        load_skill(tmp_path / "nope.md")


def test_load_skill_no_frontmatter_raises(tmp_path: Path):
    skill = _write(tmp_path / "SKILL.md", "# Just a body, no frontmatter\n")
    with pytest.raises(SkillManifestError, match="frontmatter delimiter"):
        load_skill(skill)


def test_load_skill_unterminated_frontmatter_raises(tmp_path: Path):
    skill = _write(
        tmp_path / "SKILL.md",
        """---
name: broken
description: never closed
""",
    )
    with pytest.raises(SkillManifestError, match="closing"):
        load_skill(skill)


def test_load_skill_invalid_yaml_raises(tmp_path: Path):
    skill = _write(
        tmp_path / "SKILL.md",
        """---
name: bad
description: "unclosed string
tools:
  - x
---
""",
    )
    with pytest.raises(SkillManifestError, match="not valid YAML"):
        load_skill(skill)


def test_load_skill_missing_name_raises(tmp_path: Path):
    skill = _write(
        tmp_path / "SKILL.md",
        """---
description: nameless
---
""",
    )
    with pytest.raises(SkillManifestError, match="`name`"):
        load_skill(skill)


def test_load_skill_missing_description_raises(tmp_path: Path):
    skill = _write(
        tmp_path / "SKILL.md",
        """---
name: undescribed
---
""",
    )
    with pytest.raises(SkillManifestError, match="`description`"):
        load_skill(skill)


def test_load_skill_tools_not_a_list_raises(tmp_path: Path):
    skill = _write(
        tmp_path / "SKILL.md",
        """---
name: bad-tools
description: tools is a string not a list
tools: just_one_tool
---
""",
    )
    with pytest.raises(SkillManifestError, match="must be a YAML list"):
        load_skill(skill)


def test_load_skill_empty_string_tool_raises(tmp_path: Path):
    skill = _write(
        tmp_path / "SKILL.md",
        """---
name: bad-tool-entry
description: empty tool name
tools:
  - ""
  - real_tool
---
""",
    )
    with pytest.raises(SkillManifestError, match="non-empty strings"):
        load_skill(skill)


def test_load_skill_frontmatter_not_a_mapping_raises(tmp_path: Path):
    skill = _write(
        tmp_path / "SKILL.md",
        """---
- just
- a
- list
---
""",
    )
    with pytest.raises(SkillManifestError, match="YAML mapping"):
        load_skill(skill)


# ── enforce_tool_access ───────────────────────────────────────


def test_enforce_tool_access_permits_listed_tool():
    manifest = SkillManifest(
        name="s", description="d", allowed_tools=["alpha", "beta"]
    )
    assert enforce_tool_access(manifest, "alpha") is True
    assert enforce_tool_access(manifest, "beta") is True


def test_enforce_tool_access_denies_unlisted_tool():
    manifest = SkillManifest(
        name="s", description="d", allowed_tools=["alpha"]
    )
    assert enforce_tool_access(manifest, "beta") is False


def test_enforce_tool_access_empty_allowlist_denies_everything():
    manifest = SkillManifest(name="s", description="d", allowed_tools=[])
    assert enforce_tool_access(manifest, "alpha") is False
    assert enforce_tool_access(manifest, "") is False


def test_enforce_tool_access_no_wildcard_semantics():
    """`*` is not a wildcard; it only matches the literal string '*'."""
    manifest = SkillManifest(
        name="s", description="d", allowed_tools=["*"]
    )
    assert enforce_tool_access(manifest, "*") is True
    assert enforce_tool_access(manifest, "anything") is False


def test_enforce_tool_access_no_prefix_matching():
    manifest = SkillManifest(
        name="s", description="d", allowed_tools=["carryall_compile_policy"]
    )
    assert enforce_tool_access(manifest, "carryall_compile") is False
    assert enforce_tool_access(manifest, "carryall_compile_policy_v2") is False
    assert enforce_tool_access(manifest, "carryall_compile_policy") is True


def test_enforce_tool_access_rejects_non_string():
    manifest = SkillManifest(
        name="s", description="d", allowed_tools=["alpha"]
    )
    assert enforce_tool_access(manifest, None) is False  # type: ignore[arg-type]
    assert enforce_tool_access(manifest, 42) is False  # type: ignore[arg-type]
