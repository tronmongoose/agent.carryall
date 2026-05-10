"""Universal harness audit rules shipped by Carryall.

These are deployment-agnostic — they encode invariants that should hold
for any Carryall deployment. Deployment-specific rules live in the
deployment, not here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

from ..auditor import Finding
from . import Rule

# Files we recurse into, scoped to keep scan time bounded.
_SETTINGS_GLOBS = ("**/settings.json", "**/settings.local.json")
_SKILL_GLOB = "**/SKILL.md"

# Directories never worth scanning.
_SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "dist",
    "build",
}


def _iter_files(root: Path, glob: str) -> List[Path]:
    """Yield matching files, skipping noisy directories."""
    out: List[Path] = []
    for path in root.glob(glob):
        if any(part in _SKIP_DIR_NAMES for part in path.parts):
            continue
        if path.is_file():
            out.append(path)
    return out


# ── Rule 1: no skipDangerousModePermissionPrompt in settings.json ────


def _check_no_dangerous_mode_skip(root: Path) -> List[Finding]:
    findings: List[Finding] = []
    for glob in _SETTINGS_GLOBS:
        for path in _iter_files(root, glob):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                findings.append(
                    Finding(
                        rule_id="no-dangerous-mode-skip",
                        severity="medium",
                        summary=f"settings file is not valid JSON: {path.name}",
                        file=str(path),
                        detail=str(e),
                    )
                )
                continue
            if _contains_key(data, "skipDangerousModePermissionPrompt"):
                findings.append(
                    Finding(
                        rule_id="no-dangerous-mode-skip",
                        severity="critical",
                        summary=(
                            "skipDangerousModePermissionPrompt set; "
                            "permission prompts must not be bypassed"
                        ),
                        file=str(path),
                    )
                )
    return findings


def _contains_key(obj: object, key: str) -> bool:
    """Recursively check whether `key` appears anywhere in the JSON tree."""
    if isinstance(obj, dict):
        if key in obj:
            return True
        return any(_contains_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_key(item, key) for item in obj)
    return False


# ── Rule 2: SKILL.md files declare tools: explicitly ─────────────────


def _check_skills_declare_tools(root: Path) -> List[Finding]:
    # Local import to avoid circulars at package init.
    from authority_runtime.skill_loader import (
        SkillManifestError,
        load_skill,
    )

    findings: List[Finding] = []
    for path in _iter_files(root, _SKILL_GLOB):
        try:
            manifest = load_skill(path)
        except SkillManifestError as e:
            findings.append(
                Finding(
                    rule_id="skills-declare-tools",
                    severity="high",
                    summary=f"SKILL.md failed to parse: {path.name}",
                    file=str(path),
                    detail=str(e),
                )
            )
            continue

        # Re-read to detect "tools key absent" vs "tools: []".
        # load_skill normalizes both to []; we want explicit declaration.
        text = path.read_text(encoding="utf-8")
        frontmatter = _extract_frontmatter_text(text)
        if frontmatter is not None and "tools:" not in frontmatter:
            findings.append(
                Finding(
                    rule_id="skills-declare-tools",
                    severity="medium",
                    summary=(
                        f"SKILL.md missing explicit `tools:` key: {manifest.name}"
                    ),
                    file=str(path),
                    detail=(
                        "Add `tools: []` for documentation-only skills, "
                        "or list permitted tools."
                    ),
                )
            )
    return findings


def _extract_frontmatter_text(text: str) -> str | None:
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            return "\n".join(lines[1:i])
    return None


# ── Rule list (exported) ─────────────────────────────────────────────


RULES: List[Rule] = [
    Rule(
        id="no-dangerous-mode-skip",
        severity="critical",
        description=(
            "settings.json must not set skipDangerousModePermissionPrompt; "
            "permission prompts must not be bypassed."
        ),
        check_fn=_check_no_dangerous_mode_skip,
    ),
    Rule(
        id="skills-declare-tools",
        severity="medium",
        description=(
            "Every SKILL.md must declare its tools: allowlist explicitly, "
            "even if empty (documentation-only)."
        ),
        check_fn=_check_skills_declare_tools,
    ),
]


__all__ = ["RULES"]
