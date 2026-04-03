#!/usr/bin/env python3
"""
SLOS Context Compactor — compress old messages into DAG summaries.

Leaf compaction (depth 0): Groups old messages, summarizes via Ollama,
inserts as promoted depth-0 summaries. Always local — vault content is sensitive.

Condensed compaction (depth 1+): Groups 5+ depth-0 siblings >24h old,
summarizes via Claude Sonnet, inserts as unpromoted depth-1+ summaries.
Requires manual --promote or future sentinel approval.

Usage:
    python usecases/context_compactor.py --compact              # all vaults
    python usecases/context_compactor.py --domain finance       # one vault
    python usecases/context_compactor.py --status               # stats
    python usecases/context_compactor.py --review-pending       # list unpromoted summaries
    python usecases/context_compactor.py --promote <id>         # approve a summary
    python usecases/context_compactor.py --dry-run --compact    # preview without changes
"""

from __future__ import annotations
import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# Add project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from usecases.context_manager import ContextStore

log = logging.getLogger("slos-compactor")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_COMPILER_MODEL", "gemma4:26b")

# Sonnet for condensed (depth 1+) summaries
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SONNET_MODEL = "claude-sonnet-4-20250514"

LEAF_BATCH_SIZE = 20
LEAF_MIN_AGE_HOURS = 24
CONDENSED_MIN_SIBLINGS = 5
CONDENSED_MIN_AGE_HOURS = 24

# ── Leaf compaction prompt ────────────────────────────────────

LEAF_PROMPT = """You are summarizing a batch of agent messages from a personal knowledge management system. These are operational records — factual, not interpretive.

Rules:
- Preserve ALL specific data: numbers, dates, names, amounts, decisions
- Use bullet points, not prose
- Include the time range covered
- Mark any action items or pending decisions
- Keep under 300 words
- Do NOT add interpretation or recommendations

Messages to summarize:
"""

# ── Condensed compaction prompt ───────────────────────────────

CONDENSED_PROMPT = """You are condensing multiple operational summaries into a higher-level summary. These summaries cover different time periods in the same domain.

Rules:
- Identify patterns and trends across the summaries
- Preserve critical data points (financial figures, health metrics, key decisions)
- Note any contradictions or changes in direction
- Include the full time range covered
- Keep under 500 words
- Separate facts from any interpretive notes

Summaries to condense:
"""


def _call_ollama(prompt: str, system: str = "") -> str | None:
    """Call Ollama for leaf summarization. Returns None on failure."""
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/v1/chat/completions",
            json={
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": system or "You are a concise, factual summarizer."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 1024,
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        log.error("Ollama call failed: %s", e)
        return None


def _call_sonnet(prompt: str, system: str = "") -> str | None:
    """Call Claude Sonnet for condensed summarization. Returns None on failure."""
    if not ANTHROPIC_API_KEY:
        log.warning("ANTHROPIC_API_KEY not set — cannot use Sonnet for condensed summaries")
        return None
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": SONNET_MODEL,
                "max_tokens": 1024,
                "system": system or "You are a concise, factual summarizer.",
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]
    except Exception as e:
        log.error("Sonnet call failed: %s", e)
        return None


def compact_leaves(
    store: ContextStore,
    vault_domain: str,
    dry_run: bool = False,
) -> int:
    """
    Compact old messages into depth-0 leaf summaries.

    Groups oldest unsummarized messages (>24h old), sends to Ollama,
    inserts promoted depth-0 summary. Returns number of summaries created.
    """
    created = 0

    while True:
        batch = store.get_compactable_messages(
            vault_domain,
            min_age_hours=LEAF_MIN_AGE_HOURS,
            batch_size=LEAF_BATCH_SIZE,
        )
        if not batch:
            break

        # Build prompt from batch
        msg_text = ""
        for m in batch:
            msg_text += f"\n[{m['created_at'][:19]}] {m['role']}: {m['content'][:500]}\n"

        prompt = LEAF_PROMPT + msg_text

        if dry_run:
            print(f"  [DRY RUN] Would compact {len(batch)} messages in {vault_domain}")
            print(f"    Time range: {batch[0]['created_at'][:19]} → {batch[-1]['created_at'][:19]}")
            print(f"    Token estimate: {sum(m['token_count'] for m in batch)}")
            break  # Only show first batch in dry run

        # Call Ollama
        summary_text = _call_ollama(prompt)
        if not summary_text:
            log.error("Failed to summarize batch for %s — skipping", vault_domain)
            break

        # Pick an agent_id from the batch (most frequent)
        agent_counts: dict[str, int] = {}
        for m in batch:
            agent_counts[m["agent_id"]] = agent_counts.get(m["agent_id"], 0) + 1
        agent_id = max(agent_counts, key=agent_counts.get)

        # Insert summary
        summary_id = store.insert_summary(
            vault_domain=vault_domain,
            agent_id=agent_id,
            depth=0,
            content=summary_text,
            model_used=OLLAMA_MODEL,
            source_message_ids=[m["id"] for m in batch],
            promoted=True,  # Leaf summaries auto-promote
            covers_from=batch[0]["created_at"],
            covers_to=batch[-1]["created_at"],
        )
        created += 1
        print(f"  Created leaf summary {summary_id[:8]}... covering {len(batch)} messages in {vault_domain}")

    return created


def compact_condensed(
    store: ContextStore,
    vault_domain: str,
    dry_run: bool = False,
) -> int:
    """
    Compact depth-0 leaf summaries into depth-1 condensed summaries.

    Groups 5+ leaf summaries >24h old, sends to Sonnet, inserts as
    unpromoted depth-1 summary. Returns number of summaries created.
    """
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=CONDENSED_MIN_AGE_HOURS)).isoformat()

    # Get old leaf summaries
    leaves = store.get_summaries(vault_domain, promoted_only=True, depth=0, limit=100)
    old_leaves = [s for s in leaves if s["created_at"] < cutoff]

    if len(old_leaves) < CONDENSED_MIN_SIBLINGS:
        return 0

    # Build prompt
    text = ""
    for s in old_leaves:
        text += f"\n--- Summary [{s['covers_from'][:10] if s['covers_from'] else '?'} → {s['covers_to'][:10] if s['covers_to'] else '?'}] ---\n"
        text += s["content"][:1000] + "\n"

    prompt = CONDENSED_PROMPT + text

    if dry_run:
        print(f"  [DRY RUN] Would condense {len(old_leaves)} leaf summaries in {vault_domain}")
        return 0

    # Try Sonnet first, fall back to Ollama
    summary_text = _call_sonnet(prompt)
    model = SONNET_MODEL
    if not summary_text:
        log.warning("Sonnet unavailable, falling back to Ollama for condensed summary")
        summary_text = _call_ollama(prompt)
        model = OLLAMA_MODEL
    if not summary_text:
        log.error("Failed to condense summaries for %s", vault_domain)
        return 0

    agent_id = old_leaves[0]["agent_id"]
    covers_from = min(s["covers_from"] for s in old_leaves if s["covers_from"])
    covers_to = max(s["covers_to"] for s in old_leaves if s["covers_to"])

    summary_id = store.insert_summary(
        vault_domain=vault_domain,
        agent_id=agent_id,
        depth=1,
        content=summary_text,
        model_used=model,
        parent_summary_ids=[s["id"] for s in old_leaves],
        promoted=False,  # Condensed summaries need review
        covers_from=covers_from,
        covers_to=covers_to,
    )
    print(f"  Created condensed summary {summary_id[:8]}... from {len(old_leaves)} leaves in {vault_domain} [PENDING REVIEW]")
    return 1


def show_status(store: ContextStore):
    """Print message/summary counts per vault."""
    stats = store.get_stats()
    if not stats:
        print("No data in context store.")
        return

    print(f"\n{'Vault':<15} {'Messages':>10} {'Summaries':>10} {'Promoted':>10}")
    print("-" * 47)
    total_msg, total_sum, total_pro = 0, 0, 0
    for vault, counts in sorted(stats.items()):
        m, s, p = counts["messages"], counts["summaries"], counts["promoted"]
        total_msg += m
        total_sum += s
        total_pro += p
        print(f"{vault:<15} {m:>10} {s:>10} {p:>10}")
    print("-" * 47)
    print(f"{'TOTAL':<15} {total_msg:>10} {total_sum:>10} {total_pro:>10}")


def review_pending(store: ContextStore):
    """List unpromoted condensed summaries awaiting review."""
    vaults = store.get_vaults_with_messages()
    found = 0
    for vault in vaults:
        unpromoted = store.get_summaries(vault, promoted_only=False, limit=100)
        pending = [s for s in unpromoted if not s["promoted"]]
        for s in pending:
            found += 1
            print(f"\n  ID: {s['id']}")
            print(f"  Vault: {s['vault_domain']}  Depth: {s['depth']}  Model: {s['model_used']}")
            print(f"  Covers: {s.get('covers_from', '?')[:10]} → {s.get('covers_to', '?')[:10]}")
            print(f"  Content preview: {s['content'][:200]}...")
    if not found:
        print("No pending summaries to review.")


def main():
    parser = argparse.ArgumentParser(description="SLOS Context Compactor")
    parser.add_argument("--compact", action="store_true", help="Run leaf compaction")
    parser.add_argument("--condense", action="store_true", help="Run condensed compaction (depth 1+)")
    parser.add_argument("--domain", type=str, help="Restrict to a single vault domain")
    parser.add_argument("--status", action="store_true", help="Show context stats")
    parser.add_argument("--review-pending", action="store_true", help="List unpromoted summaries")
    parser.add_argument("--promote", type=str, help="Promote a summary by ID")
    parser.add_argument("--dry-run", action="store_true", help="Preview without changes")
    parser.add_argument("--db", type=str, help="Custom DB path (default: ~/slos/config/context/context.db)")
    args = parser.parse_args()

    store = ContextStore(db_path=args.db) if args.db else ContextStore()

    if args.status:
        show_status(store)
        return

    if args.review_pending:
        review_pending(store)
        return

    if args.promote:
        ok = store.promote_summary(args.promote)
        if ok:
            print(f"Promoted summary {args.promote}")
        else:
            print(f"Summary {args.promote} not found")
            sys.exit(1)
        return

    if args.compact or args.condense:
        if args.domain:
            vaults = [args.domain]
        else:
            vaults = store.get_vaults_with_messages()
            if not vaults:
                print("No messages in context store — nothing to compact.")
                return

        total_leaves = 0
        total_condensed = 0

        for vault in vaults:
            print(f"\n[{vault}]")
            if args.compact:
                total_leaves += compact_leaves(store, vault, dry_run=args.dry_run)
            if args.condense:
                total_condensed += compact_condensed(store, vault, dry_run=args.dry_run)

        print(f"\nDone: {total_leaves} leaf summaries, {total_condensed} condensed summaries created")

        # Auto-embed new messages for semantic search
        try:
            from context_embeddings import embed_pending
            print("\n[embeddings]")
            embed_pending(batch_size=100)
        except Exception as e:
            print(f"  Embedding skipped (non-fatal): {e}")

        return

    parser.print_help()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    main()
