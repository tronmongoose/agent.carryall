"""
SLOS Context Manager — DAG-backed context persistence for carryall agents.

Persists every agent message to SQLite, compresses history into hierarchical
summaries (leaf → condensed → durable), and provides recall tools. Async-first:
compaction never blocks context assembly.

DB: ~/slos/config/context/context.db (WAL mode for concurrent multi-agent access)

Usage:
    store = ContextStore()
    store.ingest("finance-agent", "finance", "assistant", "Q4 budget looks tight...")
    context = store.assemble("finance-agent", "finance", token_budget=4096)
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator

# Ensure usecases/ is on path for sibling imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

log = logging.getLogger("slos-context")

DEFAULT_DB_PATH = Path.home() / "slos" / "config" / "context" / "context.db"
FRESH_TAIL_COUNT = 24
LARGE_FILE_TOKEN_THRESHOLD = 8192


def estimate_tokens(text: str) -> int:
    """Estimate token count from text. Coarse but dependency-free."""
    return max(1, int(len(text.split()) / 0.75))


def ingest_brief(agent_id: str, vault_domain: str, content: str, role: str = "assistant",
                  session_id: str | None = None, tags: str | None = None) -> str | None:
    """Convenience: ingest a pipeline brief into the context store. ACL-enforced."""
    try:
        from context_acl import can_write
        if not can_write(agent_id, vault_domain):
            log.warning("ACL DENIED: %s cannot write to %s", agent_id, vault_domain)
            return None
        # Prepend untrusted content tag to content if specified
        if tags and "UNTRUSTED_CONTENT" in tags:
            content = f"[UNTRUSTED_CONTENT] {content}"
        store = ContextStore()
        return store.ingest(agent_id, vault_domain, role, content,
                            session_id=session_id)
    except Exception as e:
        log.warning("Context ingest failed (non-fatal): %s", e)
        return None


def assemble_context_block(agent_id: str, vault_domain: str, token_budget: int = 1024) -> str:
    """Assemble prior context as a text block for prompt injection. ACL-enforced."""
    try:
        from context_acl import can_read
        if not can_read(agent_id, vault_domain):
            log.warning("ACL DENIED: %s cannot read %s", agent_id, vault_domain)
            return ""
        store = ContextStore()
        items = store.assemble(agent_id, vault_domain, token_budget=token_budget)
        if not items:
            return ""
        lines = ["--- Prior Context ---"]
        for item in items:
            # Flag untrusted content in assembled context
            content = item.get("content", "")
            prefix = "[UNTRUSTED] " if "[UNTRUSTED_CONTENT]" in content else ""
            if item["type"] == "summary":
                lines.append(
                    f"{prefix}[Summary {item.get('covers_from', '?')[:10]}\u2192{item.get('covers_to', '?')[:10]}] "
                    f"{content}"
                )
            else:
                lines.append(f"{prefix}[{item.get('created_at', '?')[:16]}] {content}")
        lines.append("--- End Prior Context ---")
        return "\n".join(lines)
    except Exception as e:
        log.warning("Context assembly failed (non-fatal): %s", e)
        return ""


# ── Schema ────────────────────────────────────────────────

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    vault_domain TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system', 'tool')),
    content TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    session_id TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_msg_vault ON messages(vault_domain);
CREATE INDEX IF NOT EXISTS idx_msg_agent ON messages(agent_id);
CREATE INDEX IF NOT EXISTS idx_msg_created ON messages(created_at);
CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id);

CREATE TABLE IF NOT EXISTS summaries (
    id TEXT PRIMARY KEY,
    vault_domain TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    depth INTEGER NOT NULL DEFAULT 0,
    parent_ids TEXT NOT NULL DEFAULT '[]',
    content TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    model_used TEXT NOT NULL DEFAULT 'manual',
    promoted INTEGER NOT NULL DEFAULT 0,
    covers_from TEXT,
    covers_to TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sum_vault ON summaries(vault_domain);
CREATE INDEX IF NOT EXISTS idx_sum_depth ON summaries(depth);
CREATE INDEX IF NOT EXISTS idx_sum_promoted ON summaries(promoted);
CREATE INDEX IF NOT EXISTS idx_sum_created ON summaries(created_at);

CREATE TABLE IF NOT EXISTS summary_sources (
    summary_id TEXT NOT NULL REFERENCES summaries(id),
    message_id TEXT NOT NULL REFERENCES messages(id),
    PRIMARY KEY (summary_id, message_id)
);

CREATE TABLE IF NOT EXISTS large_files (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES messages(id),
    vault_domain TEXT NOT NULL,
    file_path TEXT,
    original_tokens INTEGER NOT NULL,
    exploration_summary TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
    USING fts5(content, content=messages, content_rowid=rowid);

CREATE VIRTUAL TABLE IF NOT EXISTS summaries_fts
    USING fts5(content, content=summaries, content_rowid=rowid);
"""

# FTS triggers to keep index in sync
FTS_TRIGGERS_SQL = """
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TRIGGER IF NOT EXISTS summaries_ai AFTER INSERT ON summaries BEGIN
    INSERT INTO summaries_fts(rowid, content) VALUES (new.rowid, new.content);
END;
"""


# ── ContextStore ──────────────────────────────────────────

class ContextStore:
    """SQLite-backed context persistence with DAG summarization support."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = str(db_path or DEFAULT_DB_PATH)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA_SQL)
            # FTS + triggers use executescript (triggers have ; inside BEGIN/END)
            conn.executescript(FTS_SQL + FTS_TRIGGERS_SQL)
            conn.commit()

    # ── Ingest ────────────────────────────────────────────

    def ingest(
        self,
        agent_id: str,
        vault_domain: str,
        role: str,
        content: str,
        session_id: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        """
        Ingest a message. Returns the message ID.

        Large content (>8K tokens) is intercepted: a summary placeholder
        replaces the content, and the original is stored separately.
        """
        msg_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        tokens = estimate_tokens(content)

        # Large file interception
        if tokens > LARGE_FILE_TOKEN_THRESHOLD:
            summary = self._summarize_large(content)
            file_id = str(uuid.uuid4())
            files_dir = Path(self.db_path).parent / "files" / vault_domain
            files_dir.mkdir(parents=True, exist_ok=True)
            file_path = files_dir / f"{file_id}.txt"
            file_path.write_text(content, encoding="utf-8")

            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO large_files (id, message_id, vault_domain, file_path, original_tokens, exploration_summary, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (file_id, msg_id, vault_domain, str(file_path), tokens, summary, now),
                )
                # Store summary placeholder instead of full content
                conn.execute(
                    "INSERT INTO messages (id, agent_id, vault_domain, role, content, token_count, session_id, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (msg_id, agent_id, vault_domain, role, f"[Large content intercepted — {tokens} tokens]\n{summary}\n[Expand with slos_expand for full content, file_id={file_id}]",
                     estimate_tokens(summary), session_id, json.dumps(metadata) if metadata else None, now),
                )
                conn.commit()
            log.info("Ingested large file %s (%d tokens) → %s", msg_id, tokens, file_path)
            return msg_id

        with self._conn() as conn:
            conn.execute(
                "INSERT INTO messages (id, agent_id, vault_domain, role, content, token_count, session_id, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (msg_id, agent_id, vault_domain, role, content, tokens, session_id, json.dumps(metadata) if metadata else None, now),
            )
            conn.commit()
        return msg_id

    def _summarize_large(self, content: str) -> str:
        """Create a brief summary of large content. Simple extractive for now."""
        lines = content.strip().split("\n")
        # Take first 3 lines + last 2 lines as a rough summary
        head = "\n".join(lines[:3])
        tail = "\n".join(lines[-2:]) if len(lines) > 5 else ""
        return f"{head}\n...\n{tail}".strip()

    # ── Assembly ──────────────────────────────────────────

    def assemble(
        self,
        agent_id: str,
        vault_domain: str,
        token_budget: int = 4096,
    ) -> list[dict]:
        """
        Assemble context for an agent turn. Returns ordered list of items.

        Assembly order: oldest promoted summaries first, then fresh tail
        (last N raw messages). If total exceeds budget, oldest summaries
        are dropped first. Fresh tail is never evicted.
        """
        items: list[dict] = []

        with self._conn() as conn:
            # 1. Promoted summaries, oldest first
            summaries = conn.execute(
                "SELECT id, depth, content, token_count, covers_from, covers_to, created_at "
                "FROM summaries WHERE vault_domain = ? AND promoted = 1 ORDER BY covers_from ASC, created_at ASC",
                (vault_domain,),
            ).fetchall()

            for s in summaries:
                items.append({
                    "type": "summary",
                    "id": s["id"],
                    "depth": s["depth"],
                    "content": s["content"],
                    "tokens": s["token_count"],
                    "covers_from": s["covers_from"],
                    "covers_to": s["covers_to"],
                })

            # 2. Fresh tail — last N messages not covered by summaries
            tail = conn.execute(
                "SELECT id, agent_id, role, content, token_count, created_at "
                "FROM messages WHERE vault_domain = ? ORDER BY created_at DESC LIMIT ?",
                (vault_domain, FRESH_TAIL_COUNT),
            ).fetchall()

            tail_items = [
                {
                    "type": "message",
                    "id": m["id"],
                    "agent_id": m["agent_id"],
                    "role": m["role"],
                    "content": m["content"],
                    "tokens": m["token_count"],
                    "created_at": m["created_at"],
                }
                for m in reversed(tail)  # oldest first
            ]

        # Budget enforcement: drop oldest summaries first, never evict tail
        tail_tokens = sum(t["tokens"] for t in tail_items)
        remaining = token_budget - tail_tokens

        if remaining < 0:
            # Tail alone exceeds budget — truncate tail from the front
            trimmed = []
            used = 0
            for t in reversed(tail_items):
                if used + t["tokens"] <= token_budget:
                    trimmed.insert(0, t)
                    used += t["tokens"]
            return trimmed

        # Fit as many summaries as possible
        fitted_summaries = []
        used = 0
        for s in items:
            if used + s["tokens"] <= remaining:
                fitted_summaries.append(s)
                used += s["tokens"]

        return fitted_summaries + tail_items

    # ── Query ─────────────────────────────────────────────

    def get_messages(
        self,
        vault_domain: str,
        agent_id: str | None = None,
        session_id: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query raw messages."""
        with self._conn() as conn:
            clauses = ["vault_domain = ?"]
            params: list = [vault_domain]
            if agent_id:
                clauses.append("agent_id = ?")
                params.append(agent_id)
            if session_id:
                clauses.append("session_id = ?")
                params.append(session_id)
            if since:
                clauses.append("created_at >= ?")
                params.append(since)
            params.append(limit)
            rows = conn.execute(
                f"SELECT * FROM messages WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def get_summaries(
        self,
        vault_domain: str,
        promoted_only: bool = True,
        depth: int | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Query summaries."""
        with self._conn() as conn:
            clauses = ["vault_domain = ?"]
            params: list = [vault_domain]
            if promoted_only:
                clauses.append("promoted = 1")
            if depth is not None:
                clauses.append("depth = ?")
                params.append(depth)
            params.append(limit)
            rows = conn.execute(
                f"SELECT * FROM summaries WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def get_stats(self, vault_domain: str | None = None) -> dict:
        """Get message/summary counts per vault."""
        with self._conn() as conn:
            if vault_domain:
                msg_count = conn.execute("SELECT COUNT(*) FROM messages WHERE vault_domain = ?", (vault_domain,)).fetchone()[0]
                sum_count = conn.execute("SELECT COUNT(*) FROM summaries WHERE vault_domain = ?", (vault_domain,)).fetchone()[0]
                promoted = conn.execute("SELECT COUNT(*) FROM summaries WHERE vault_domain = ? AND promoted = 1", (vault_domain,)).fetchone()[0]
                return {"vault": vault_domain, "messages": msg_count, "summaries": sum_count, "promoted": promoted}

            rows = conn.execute(
                "SELECT vault_domain, COUNT(*) as cnt FROM messages GROUP BY vault_domain"
            ).fetchall()
            msg_counts = {r["vault_domain"]: r["cnt"] for r in rows}

            rows = conn.execute(
                "SELECT vault_domain, COUNT(*) as cnt, SUM(CASE WHEN promoted=1 THEN 1 ELSE 0 END) as promo FROM summaries GROUP BY vault_domain"
            ).fetchall()
            sum_counts = {r["vault_domain"]: {"total": r["cnt"], "promoted": r["promo"]} for r in rows}

            all_vaults = set(msg_counts) | set(sum_counts)
            return {
                v: {
                    "messages": msg_counts.get(v, 0),
                    "summaries": sum_counts.get(v, {}).get("total", 0),
                    "promoted": sum_counts.get(v, {}).get("promoted", 0),
                }
                for v in sorted(all_vaults)
            }

    # ── Recall Tools ──────────────────────────────────────

    def slos_recall(self, vault_domain: str, n: int = 5) -> list[dict]:
        """Retrieve N most recent promoted summaries for a vault."""
        return self.get_summaries(vault_domain, promoted_only=True, limit=n)

    def slos_grep(self, vault_domain: str, query: str, scope: str = "both", limit: int = 40) -> list[dict]:
        """Full-text search across messages and/or summaries."""
        results: list[dict] = []
        with self._conn() as conn:
            if scope in ("messages", "both"):
                rows = conn.execute(
                    "SELECT m.* FROM messages m JOIN messages_fts f ON m.rowid = f.rowid "
                    "WHERE messages_fts MATCH ? AND m.vault_domain = ? LIMIT ?",
                    (query, vault_domain, limit),
                ).fetchall()
                results.extend({"type": "message", **dict(r)} for r in rows)

            if scope in ("summaries", "both"):
                remaining = limit - len(results)
                if remaining > 0:
                    rows = conn.execute(
                        "SELECT s.* FROM summaries s JOIN summaries_fts f ON s.rowid = f.rowid "
                        "WHERE summaries_fts MATCH ? AND s.vault_domain = ? LIMIT ?",
                        (query, vault_domain, remaining),
                    ).fetchall()
                    results.extend({"type": "summary", **dict(r)} for r in rows)
        return results

    def slos_expand(self, summary_id: str) -> dict | None:
        """Walk DAG from a summary to its source messages."""
        with self._conn() as conn:
            summary = conn.execute("SELECT * FROM summaries WHERE id = ?", (summary_id,)).fetchone()
            if not summary:
                return None

            # Get source messages
            sources = conn.execute(
                "SELECT m.* FROM messages m JOIN summary_sources ss ON m.id = ss.message_id WHERE ss.summary_id = ? ORDER BY m.created_at ASC",
                (summary_id,),
            ).fetchall()

            # Also check if this summary was built from other summaries (parent_ids)
            parent_ids = json.loads(summary["parent_ids"]) if summary["parent_ids"] else []
            parent_summaries = []
            if parent_ids:
                placeholders = ",".join("?" * len(parent_ids))
                parent_summaries = conn.execute(
                    f"SELECT * FROM summaries WHERE id IN ({placeholders}) ORDER BY created_at ASC",
                    parent_ids,
                ).fetchall()

            # Check for large file references
            large_file = None
            for src in sources:
                lf = conn.execute("SELECT * FROM large_files WHERE message_id = ?", (src["id"],)).fetchone()
                if lf:
                    large_file = dict(lf)
                    break

            return {
                "summary": dict(summary),
                "source_messages": [dict(s) for s in sources],
                "parent_summaries": [dict(p) for p in parent_summaries],
                "large_file": large_file,
            }

    # ── Summary insertion (used by compactor) ─────────────

    def insert_summary(
        self,
        vault_domain: str,
        agent_id: str,
        depth: int,
        content: str,
        model_used: str,
        source_message_ids: list[str] | None = None,
        parent_summary_ids: list[str] | None = None,
        promoted: bool = False,
        covers_from: str | None = None,
        covers_to: str | None = None,
    ) -> str:
        """Insert a summary node into the DAG."""
        summary_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        tokens = estimate_tokens(content)
        parent_ids_json = json.dumps(parent_summary_ids or [])

        with self._conn() as conn:
            conn.execute(
                "INSERT INTO summaries (id, vault_domain, agent_id, depth, parent_ids, content, token_count, model_used, promoted, covers_from, covers_to, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (summary_id, vault_domain, agent_id, depth, parent_ids_json, content, tokens, model_used, 1 if promoted else 0, covers_from, covers_to, now),
            )
            # Link source messages
            if source_message_ids:
                conn.executemany(
                    "INSERT OR IGNORE INTO summary_sources (summary_id, message_id) VALUES (?, ?)",
                    [(summary_id, mid) for mid in source_message_ids],
                )
            conn.commit()
        return summary_id

    def promote_summary(self, summary_id: str) -> bool:
        """Manually promote a summary for context assembly."""
        with self._conn() as conn:
            cursor = conn.execute("UPDATE summaries SET promoted = 1 WHERE id = ?", (summary_id,))
            conn.commit()
            return cursor.rowcount > 0

    # ── Compaction helpers ─────────────────────────────────

    def get_compactable_messages(
        self,
        vault_domain: str,
        min_age_hours: int = 24,
        batch_size: int = 20,
    ) -> list[dict]:
        """
        Get oldest messages not yet covered by any summary.

        Returns messages older than min_age_hours that don't appear
        in summary_sources, up to batch_size.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=min_age_hours)).isoformat()

        with self._conn() as conn:
            rows = conn.execute(
                "SELECT m.* FROM messages m "
                "LEFT JOIN summary_sources ss ON m.id = ss.message_id "
                "WHERE m.vault_domain = ? AND ss.summary_id IS NULL "
                "AND m.created_at < ? "
                "ORDER BY m.created_at ASC LIMIT ?",
                (vault_domain, cutoff, batch_size),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_vaults_with_messages(self) -> list[str]:
        """Get list of vault domains that have messages."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT vault_domain FROM messages ORDER BY vault_domain"
            ).fetchall()
            return [r["vault_domain"] for r in rows]
