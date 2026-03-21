"""
Shared utilities for SLOS agent pipelines.

Extracted from 25+ files to eliminate duplication. Contains:
- SLOS_DIR — canonical SLOS root path
- load_env() — parse .env files
- call_anthropic() — Anthropic Messages API
- call_ollama() — Ollama chat completions
- split_frontmatter() — parse YAML frontmatter from markdown
- slos_frontmatter() — generate SLOS vault document frontmatter
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Canonical SLOS root — import this instead of redefining in every file
SLOS_DIR = os.path.expanduser(os.environ.get("SLOS_DIR", "~/slos"))

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_COMPILER_MODEL", "mistral-small-3.2")


def load_env(dotenv_path: Path | str | None = None):
    """Load env vars from .env file. Defaults to project root .env.

    Uses setdefault so existing env vars are not overwritten.
    """
    if dotenv_path is None:
        # Walk up from this file to find .env at project root
        dotenv_path = Path(__file__).resolve().parent.parent / ".env"
    else:
        dotenv_path = Path(dotenv_path)

    if dotenv_path.exists():
        for line in dotenv_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                val = val.strip().strip("'\"")
                os.environ.setdefault(key.strip(), val)


def call_anthropic(system_prompt: str, user_prompt: str,
                   model: str = "claude-sonnet-4-20250514",
                   max_tokens: int = 3000) -> str | None:
    """Call Anthropic Messages API via urllib. Returns text or None."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("  No ANTHROPIC_API_KEY — skipping Anthropic call")
        return None

    payload = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }).encode("utf-8")

    req = Request("https://api.anthropic.com/v1/messages", method="POST", data=payload)
    req.add_header("Content-Type", "application/json")
    req.add_header("x-api-key", api_key)
    req.add_header("anthropic-version", "2023-06-01")

    start = time.time()
    try:
        with urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        elapsed = time.time() - start
        content = ""
        for block in result.get("content", []):
            if block.get("type") == "text":
                content += block.get("text", "")
        tokens = result.get("usage", {})
        cost = (tokens.get("input_tokens", 0) * 3 + tokens.get("output_tokens", 0) * 15) / 1_000_000
        print(f"  Anthropic response in {elapsed:.1f}s ({model}, ~${cost:.4f})")
        return content.strip()
    except (HTTPError, URLError, TimeoutError) as e:
        print(f"  Anthropic error: {e}")
        return None


def call_ollama(system_prompt: str, user_prompt: str,
                max_tokens: int = 3000, temperature: float = 0.4,
                model: str | None = None) -> str | None:
    """Call Ollama's OpenAI-compatible chat endpoint. Returns text or None."""
    url = f"{OLLAMA_URL}/v1/chat/completions"
    payload = json.dumps({
        "model": model or OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }).encode("utf-8")

    req = Request(url, data=payload,
                  headers={"Content-Type": "application/json"}, method="POST")
    start = time.time()
    try:
        with urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        elapsed = time.time() - start
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        content = re.sub(r"^```(?:json|markdown)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        print(f"  Ollama response in {elapsed:.1f}s")
        return content.strip()
    except (HTTPError, URLError, TimeoutError) as e:
        print(f"  Ollama error: {e}")
        return None


def split_frontmatter(text: str) -> tuple[str, str]:
    """Split markdown into (frontmatter_block, body). Returns ('', text) if no frontmatter."""
    parts = text.split("---", 2)
    if len(parts) >= 3:
        return f"---{parts[1]}---", parts[2]
    return "", text


def slos_frontmatter(
    doc_id: str,
    data_type: str,
    tags: list,
    author: str = "finance-agent",
    domain: str = "finance",
    sensitivity: str = "confidential",
    allowed_agents: list | None = None,
    denied_agents: list | None = None,
    requires_approval: list | None = None,
) -> str:
    """Generate SLOS vault document frontmatter."""
    now = datetime.now(timezone.utc).isoformat()
    tags_yaml = "\n".join(f"  - {t}" for t in tags)

    fm = f"""---
id: "{doc_id}"
created: "{now}"
modified: "{now}"
author: "{author}"
domain:
  - {domain}
sensitivity: "{sensitivity}"
data_type: "{data_type}"
tags:
{tags_yaml}
"""
    if allowed_agents:
        fm += "allowed_agents:\n" + "\n".join(f"  - {a}" for a in allowed_agents) + "\n"
    if denied_agents:
        fm += "denied_agents:\n" + "\n".join(f"  - {a}" for a in denied_agents) + "\n"
    if requires_approval:
        fm += "requires_approval:\n" + "\n".join(f"  - {a}" for a in requires_approval) + "\n"
    fm += "---\n"
    return fm
