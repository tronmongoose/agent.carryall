"""Reviewed model allowlist data: exact IDs, origins, and the banned-vendor second layer.

Exports: ALLOWLIST_VERSION, ALLOWED_ORIGINS, PROVIDER_LOCALITY, FORBIDDEN_PROVIDERS,
ModelEntry, BUILTIN_ENTRIES, BANNED_MODEL_PREFIXES, banned_slug.

This file is the primary control. Every change here must bump ALLOWLIST_VERSION so
audit records and backtests can name the exact list a decision was made against.
Only IDs verifiable from vendor documentation or already referenced in this public
repo belong here. Deployment-specific local tags go in a local additions file
(see policy.ModelPolicy.load), never in this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

ALLOWLIST_VERSION = "2026-09-17.1"

ALLOWED_ORIGINS = frozenset({"Anthropic", "Google", "Meta", "Microsoft", "Nous", "Mistral"})

# provider -> where inference runs. Only these providers exist; anything else raises.
PROVIDER_LOCALITY = {"anthropic": "remote", "ollama": "local"}

# Named so the refusal reason is explicit rather than a generic unknown-provider.
FORBIDDEN_PROVIDERS = frozenset({"openai", "azure-openai", "azure_openai", "chatgpt"})


@dataclass(frozen=True)
class ModelEntry:
    """One allowlisted (provider, exact model ID) pair."""

    provider: str
    model_id: str
    origin: str
    status: str = "active"


_ANTHROPIC_ACTIVE = (
    "claude-fable-5-1", "claude-fable-5", "claude-opus-5", "claude-opus-4-8",
    "claude-opus-4-7", "claude-opus-4-6", "claude-opus-4-5", "claude-opus-4-5-20251101",
    "claude-sonnet-5", "claude-sonnet-4-6", "claude-sonnet-4-5", "claude-sonnet-4-5-20250929",
    "claude-haiku-4-5", "claude-haiku-4-5-20251001",
)
# Deprecated but still served, and referenced as a default in this repo.
_ANTHROPIC_DEPRECATED = ("claude-sonnet-4-20250514",)

BUILTIN_ENTRIES: Tuple[ModelEntry, ...] = (
    *(ModelEntry("anthropic", m, "Anthropic") for m in _ANTHROPIC_ACTIVE),
    *(ModelEntry("anthropic", m, "Anthropic", "deprecated") for m in _ANTHROPIC_DEPRECATED),
    ModelEntry("ollama", "gemma4:26b", "Google"),
)

# Defense in depth only: an unknown ID is already refused by the allowlist. Concept
# ported from bjorn-harness coding_harness/models/ollama.py.
BANNED_MODEL_PREFIXES: Tuple[str, ...] = (
    "qwen", "qwq", "alibaba", "deepseek", "yi", "yi:", "yi-", "01-ai", "baichuan", "chatglm",
    "glm-", "glm4", "zhipu", "internlm", "internvl", "minimax", "kimi", "moonshot", "hunyuan",
    "tencent", "bytedance", "doubao", "seed-", "ernie", "baidu", "stepfun", "skywork",
    "inclusionai", "ling-", "cogvlm", "cogagent", "marco-o1", "pangu", "telechat", "xverse",
    "aquila",
)
_BOUNDARY = r"[/:\-_.]"


def _slug_pattern(slug: str) -> str:
    """Long plain slugs match as substrings; short ones must fill a whole name segment."""
    esc = re.escape(slug)
    if len(slug) >= 4 and slug[-1] not in ":-":
        return f"({esc})"
    if slug[-1] in ":-":
        return f"((?:^|{_BOUNDARY}){esc})"
    return f"((?:^|{_BOUNDARY}){esc}(?:$|{_BOUNDARY}))"


_BANNED_RE = re.compile("|".join(_slug_pattern(s) for s in BANNED_MODEL_PREFIXES))


def banned_slug(model_id: str) -> Optional[str]:
    """Return the banned vendor slug found in model_id (case-insensitive), or None."""
    m = _BANNED_RE.search(model_id.lower())
    return next((g for g in m.groups() if g), None) if m else None
