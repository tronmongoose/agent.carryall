"""
SKILL.md loader and tool-access enforcer.

Skills are declared on disk as `SKILL.md` files with YAML frontmatter:

    ---
    name: my-skill
    description: "What the skill does"
    tools:
      - tool_one
      - tool_two
    ---

    # Markdown body

The frontmatter `tools:` key is a fail-closed allowlist. A skill with no
`tools:` entry, an empty list, or a missing frontmatter block is treated
as "no tools allowed" — there is no implicit wildcard.

The convention mirrors the `Authority.scopes` allowlist pattern at the
skill-tool boundary: declare what is permitted; everything else denies.

A skill may optionally ship a sibling ``SOUL.md`` file alongside its
``SKILL.md``. SKILL.md is the operational contract; SOUL.md is voice,
posture, and refusals. Carryall parses SOUL.md if present and attaches it
to the manifest as ``manifest.soul`` — but the convention is **opt-in and
under evaluation** (bjornswarm eval `sl-qvby`, window closes 2026-06-02).
The product ships the parser; deployments decide whether to use it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from pydantic import BaseModel, Field, field_validator


class SkillManifestError(ValueError):
    """Raised when a SKILL.md file cannot be parsed or is malformed."""


class SkillSoulError(ValueError):
    """Raised when a SOUL.md file cannot be parsed."""


class SkillSoul(BaseModel):
    """Parsed SOUL.md file. Voice, posture, refusals — opt-in convention.

    Frontmatter is optional; SOUL.md may be pure markdown. When present,
    frontmatter is exposed as the `frontmatter` dict; the rest of the file
    is in `body`. The `eval` field is a marker bjornswarm uses to flag
    SOUL constructs still under evaluation (e.g. `eval: v0`); Carryall
    surfaces it but does not enforce semantics.
    """

    frontmatter: Dict[str, Any] = Field(
        default_factory=dict,
        description="SOUL.md frontmatter as a dict; empty if absent",
    )
    body: str = Field(default="", description="Markdown body of SOUL.md")
    path: Optional[Path] = Field(
        default=None, description="Source file path, if loaded from disk"
    )

    model_config = {"arbitrary_types_allowed": True}

    @property
    def eval_marker(self) -> Optional[str]:
        """Convenience: the `eval:` frontmatter value, or None."""
        value = self.frontmatter.get("eval")
        if value is None:
            return None
        return str(value)


class SkillManifest(BaseModel):
    """Parsed SKILL.md file. Loader output."""

    name: str = Field(description="Skill name (matches frontmatter `name`)")
    description: str = Field(description="One-line skill description")
    allowed_tools: List[str] = Field(
        default_factory=list,
        description=(
            "Fail-closed tool allowlist. Empty list means no tools are "
            "permitted. There is no implicit wildcard."
        ),
    )
    body: str = Field(default="", description="Markdown body after frontmatter")
    path: Optional[Path] = Field(
        default=None, description="Source file path, if loaded from disk"
    )
    soul: Optional[SkillSoul] = Field(
        default=None,
        description=(
            "Sibling SOUL.md file, if present. Opt-in convention; "
            "deployments decide whether to author/use SOUL.md files."
        ),
    )

    model_config = {"arbitrary_types_allowed": True}

    @field_validator("name")
    @classmethod
    def _name_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("SKILL.md frontmatter `name` must be non-empty")
        return v

    @field_validator("allowed_tools")
    @classmethod
    def _tools_are_strings(cls, v: List[str]) -> List[str]:
        for tool in v:
            if not isinstance(tool, str) or not tool.strip():
                raise ValueError(
                    f"SKILL.md `tools:` entries must be non-empty strings; "
                    f"got {tool!r}"
                )
        return v


def _split_frontmatter(text: str) -> Tuple[str, str]:
    """Split a SKILL.md file into (frontmatter_yaml, body).

    Frontmatter is the block between the first two `---` lines at the
    start of the file. Raises SkillManifestError if absent or unterminated.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip() != "---":
        raise SkillManifestError(
            "SKILL.md must start with a `---` frontmatter delimiter"
        )
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            frontmatter = "".join(lines[1:i])
            body = "".join(lines[i + 1 :])
            return frontmatter, body
    raise SkillManifestError(
        "SKILL.md frontmatter missing closing `---` delimiter"
    )


def load_skill(path: Path | str, *, load_soul: bool = True) -> SkillManifest:
    """Load and validate a SKILL.md file.

    If a sibling ``SOUL.md`` exists in the same directory, parse and attach
    it as ``manifest.soul``. Pass ``load_soul=False`` to skip the lookup.

    Fail-closed on SKILL.md errors: any parse error, missing required field,
    or malformed ``tools:`` value raises ``SkillManifestError``. SOUL.md
    parse errors raise ``SkillSoulError`` separately so deployments can
    distinguish operational-contract failures from voice-doc failures.
    """
    skill_path = Path(path)
    if not skill_path.is_file():
        raise SkillManifestError(f"SKILL.md not found at {skill_path}")

    text = skill_path.read_text(encoding="utf-8")
    frontmatter_yaml, body = _split_frontmatter(text)

    try:
        data = yaml.safe_load(frontmatter_yaml) or {}
    except yaml.YAMLError as e:
        raise SkillManifestError(
            f"SKILL.md frontmatter at {skill_path} is not valid YAML: {e}"
        ) from e

    if not isinstance(data, dict):
        raise SkillManifestError(
            f"SKILL.md frontmatter at {skill_path} must be a YAML mapping"
        )

    for required in ("name", "description"):
        if required not in data:
            raise SkillManifestError(
                f"SKILL.md at {skill_path} missing required frontmatter "
                f"field `{required}`"
            )

    tools_raw = data.get("tools", [])
    if tools_raw is None:
        tools_raw = []
    if not isinstance(tools_raw, list):
        raise SkillManifestError(
            f"SKILL.md `tools:` at {skill_path} must be a YAML list; "
            f"got {type(tools_raw).__name__}"
        )

    soul: Optional[SkillSoul] = None
    if load_soul:
        sibling = skill_path.parent / "SOUL.md"
        if sibling.is_file():
            soul = _load_soul_file(sibling)

    try:
        return SkillManifest(
            name=str(data["name"]),
            description=str(data["description"]),
            allowed_tools=list(tools_raw),
            body=body,
            path=skill_path,
            soul=soul,
        )
    except SkillManifestError:
        raise
    except Exception as e:
        raise SkillManifestError(
            f"SKILL.md at {skill_path} failed validation: {e}"
        ) from e


def _load_soul_file(soul_path: Path) -> SkillSoul:
    """Parse a SOUL.md file. Frontmatter is optional."""
    text = soul_path.read_text(encoding="utf-8")
    frontmatter: Dict[str, Any] = {}
    body = text
    lines = text.splitlines(keepends=True)
    if lines and lines[0].rstrip() == "---":
        try:
            frontmatter_yaml, body = _split_frontmatter(text)
        except SkillManifestError as e:
            raise SkillSoulError(
                f"SOUL.md at {soul_path} has malformed frontmatter: {e}"
            ) from e
        try:
            parsed = yaml.safe_load(frontmatter_yaml) or {}
        except yaml.YAMLError as e:
            raise SkillSoulError(
                f"SOUL.md frontmatter at {soul_path} is not valid YAML: {e}"
            ) from e
        if not isinstance(parsed, dict):
            raise SkillSoulError(
                f"SOUL.md frontmatter at {soul_path} must be a YAML mapping"
            )
        frontmatter = parsed
    return SkillSoul(frontmatter=frontmatter, body=body, path=soul_path)


def load_soul(path: Path | str) -> SkillSoul:
    """Load a SOUL.md file directly. Frontmatter optional."""
    soul_path = Path(path)
    if not soul_path.is_file():
        raise SkillSoulError(f"SOUL.md not found at {soul_path}")
    return _load_soul_file(soul_path)


def enforce_tool_access(manifest: SkillManifest, tool_name: str) -> bool:
    """Check whether `tool_name` is permitted for the given skill manifest.

    Fail-closed: returns True only if `tool_name` appears verbatim in the
    manifest's `allowed_tools`. No wildcard, no prefix matching.
    """
    if not isinstance(tool_name, str) or not tool_name:
        return False
    return tool_name in manifest.allowed_tools


__all__ = [
    "SkillManifest",
    "SkillManifestError",
    "SkillSoul",
    "SkillSoulError",
    "load_skill",
    "load_soul",
    "enforce_tool_access",
]
