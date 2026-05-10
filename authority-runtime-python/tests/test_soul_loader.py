"""Tests for the optional SOUL.md sibling-file loader."""

from pathlib import Path

import pytest

from authority_runtime.skill_loader import (
    SkillManifest,
    SkillSoul,
    SkillSoulError,
    load_skill,
    load_soul,
)


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ── load_soul (direct) ────────────────────────────────────────


def test_load_soul_pure_markdown_no_frontmatter(tmp_path: Path):
    soul = _write(
        tmp_path / "SOUL.md",
        "# Voice\n\nBe direct. No filler. No semicolons.\n",
    )
    parsed = load_soul(soul)
    assert isinstance(parsed, SkillSoul)
    assert parsed.frontmatter == {}
    assert "Be direct" in parsed.body
    assert parsed.eval_marker is None


def test_load_soul_with_frontmatter(tmp_path: Path):
    soul = _write(
        tmp_path / "SOUL.md",
        """---
eval: v0
voice: terse
refusals:
  - flattery
  - speculation
---

# Voice

Body text here.
""",
    )
    parsed = load_soul(soul)
    assert parsed.frontmatter["eval"] == "v0"
    assert parsed.frontmatter["voice"] == "terse"
    assert parsed.frontmatter["refusals"] == ["flattery", "speculation"]
    assert parsed.eval_marker == "v0"
    assert "Body text here" in parsed.body


def test_load_soul_eval_marker_coerces_to_string(tmp_path: Path):
    soul = _write(
        tmp_path / "SOUL.md",
        "---\neval: 1\n---\nbody\n",
    )
    parsed = load_soul(soul)
    assert parsed.eval_marker == "1"


def test_load_soul_missing_file_raises(tmp_path: Path):
    with pytest.raises(SkillSoulError, match="not found"):
        load_soul(tmp_path / "nope.md")


def test_load_soul_unterminated_frontmatter_raises(tmp_path: Path):
    soul = _write(
        tmp_path / "SOUL.md",
        "---\neval: v0\nbody no closing delimiter\n",
    )
    with pytest.raises(SkillSoulError, match="malformed frontmatter"):
        load_soul(soul)


def test_load_soul_invalid_yaml_raises(tmp_path: Path):
    soul = _write(
        tmp_path / "SOUL.md",
        '---\neval: "unclosed string\n---\nbody\n',
    )
    with pytest.raises(SkillSoulError, match="not valid YAML"):
        load_soul(soul)


def test_load_soul_frontmatter_not_a_mapping_raises(tmp_path: Path):
    soul = _write(
        tmp_path / "SOUL.md",
        "---\n- list\n- not\n- mapping\n---\nbody\n",
    )
    with pytest.raises(SkillSoulError, match="YAML mapping"):
        load_soul(soul)


# ── load_skill auto-attaches SOUL.md ─────────────────────────


def _write_skill(dir_path: Path, with_soul: bool = False, soul_text: str = "") -> Path:
    skill = _write(
        dir_path / "SKILL.md",
        """---
name: my-skill
description: docs
tools: []
---

# Body
""",
    )
    if with_soul:
        _write(dir_path / "SOUL.md", soul_text)
    return skill


def test_load_skill_attaches_sibling_soul(tmp_path: Path):
    skill_dir = tmp_path / "skills" / "voice-bearer"
    skill = _write_skill(
        skill_dir,
        with_soul=True,
        soul_text="---\neval: v0\n---\n# Voice\n\nBe terse.\n",
    )
    manifest = load_skill(skill)
    assert isinstance(manifest, SkillManifest)
    assert manifest.soul is not None
    assert manifest.soul.eval_marker == "v0"
    assert "Be terse" in manifest.soul.body


def test_load_skill_when_soul_absent_leaves_field_none(tmp_path: Path):
    skill = _write_skill(tmp_path / "skills" / "no-soul", with_soul=False)
    manifest = load_skill(skill)
    assert manifest.soul is None


def test_load_skill_load_soul_false_skips_lookup(tmp_path: Path):
    skill_dir = tmp_path / "skills" / "skip"
    skill = _write_skill(skill_dir, with_soul=True, soul_text="# present\n")
    manifest = load_skill(skill, load_soul=False)
    assert manifest.soul is None


def test_load_skill_propagates_soul_error(tmp_path: Path):
    """A malformed sibling SOUL.md raises SkillSoulError, not SkillManifestError."""
    skill_dir = tmp_path / "skills" / "bad-soul"
    _write_skill(
        skill_dir,
        with_soul=True,
        soul_text='---\neval: "unclosed\n---\nbody\n',
    )
    with pytest.raises(SkillSoulError):
        load_skill(skill_dir / "SKILL.md")


def test_load_skill_soul_in_unrelated_dir_not_picked_up(tmp_path: Path):
    """SOUL.md is only picked up if it's a *direct* sibling of SKILL.md."""
    skill_dir = tmp_path / "skills" / "isolated"
    skill = _write_skill(skill_dir, with_soul=False)
    # SOUL.md in a sibling skill's dir should not affect this one.
    _write(tmp_path / "skills" / "other" / "SOUL.md", "stray\n")
    manifest = load_skill(skill)
    assert manifest.soul is None
