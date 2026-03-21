"""
Context Embeddings — Semantic search layer for SLOS Context Manager.

Adds vector embeddings (via Ollama nomic-embed-text) and hybrid search
(BM25 + vector similarity + reranking) to the context store. Agents can
find relevant context from weeks ago even with different phrasing.

Uses sqlite-vec for vector storage in the same context.db file.
Embeddings computed lazily — only generated when needed, cached permanently.

Usage:
    from context_embeddings import semantic_search, embed_pending

    # Search with semantic understanding
    results = semantic_search("what was our spending trend?", vault_domain="finance")

    # Backfill embeddings for existing messages
    embed_pending(batch_size=50)
"""

from __future__ import annotations
import json
import logging
import os
import sqlite3
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from context_manager import DEFAULT_DB_PATH

log = logging.getLogger("slos-context-embeddings")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = "nomic-embed-text"
EMBED_DIM = 768

VECTOR_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS message_embeddings USING vec0(
    embedding float[768]
);

CREATE TABLE IF NOT EXISTS embedding_index (
    rowid INTEGER PRIMARY KEY,
    message_id TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL DEFAULT 'message',
    embedded_at TEXT NOT NULL
);
"""


# -- Embedding API ------------------------------------------------------------

def _get_embedding(text: str) -> list[float] | None:
    """Get embedding vector from Ollama. Returns None on failure."""
    result = _get_embeddings_batch([text])
    return result[0] if result else None


def _get_embeddings_batch(texts: list[str]) -> list[list[float] | None]:
    """Get embedding vectors for a batch of texts. Returns list parallel to input."""
    truncated = [t[:8000] for t in texts]
    payload = json.dumps({"model": EMBED_MODEL, "input": truncated}).encode()
    req = Request(f"{OLLAMA_URL}/api/embed", method="POST", data=payload)
    req.add_header("Content-Type", "application/json")

    try:
        with urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        embeddings = data.get("embeddings", [])
        results = []
        for emb in embeddings:
            results.append(emb if len(emb) == EMBED_DIM else None)
        return results
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as e:
        log.warning("Batch embedding failed: %s", e)
    return [None] * len(texts)


def _vec_to_blob(vec: list[float]) -> bytes:
    """Convert float list to sqlite-vec binary format."""
    return struct.pack(f"{len(vec)}f", *vec)


# -- Database -----------------------------------------------------------------

def _init_vec_tables(conn: sqlite3.Connection):
    """Initialize vector tables if not present."""
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.executescript(VECTOR_SCHEMA)
    except Exception as e:
        log.warning("sqlite-vec init failed: %s", e)
        raise


def _get_conn(db_path: Path = None) -> sqlite3.Connection:
    """Get connection with sqlite-vec loaded."""
    path = db_path or DEFAULT_DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    _init_vec_tables(conn)
    return conn


# -- Embed & Index ------------------------------------------------------------

def embed_message(conn: sqlite3.Connection, message_id: str, content: str) -> bool:
    """Compute and store embedding for a single message. Does NOT commit — caller must commit."""
    existing = conn.execute(
        "SELECT rowid FROM embedding_index WHERE message_id = ?", (message_id,)
    ).fetchone()
    if existing:
        return True

    vec = _get_embedding(content)
    if vec is None:
        return False

    blob = _vec_to_blob(vec)
    cursor = conn.execute(
        "INSERT INTO message_embeddings(embedding) VALUES (?)", (blob,)
    )
    conn.execute(
        "INSERT INTO embedding_index(rowid, message_id, source_type, embedded_at) VALUES (?, ?, 'message', ?)",
        (cursor.lastrowid, message_id, datetime.now(timezone.utc).isoformat()),
    )
    return True


def embed_pending(batch_size: int = 50, db_path: Path = None) -> dict:
    """Embed messages that don't have embeddings yet. Uses batch API. Returns stats."""
    conn = _get_conn(db_path)
    stats = {"embedded": 0, "skipped": 0, "failed": 0}

    try:
        rows = conn.execute(
            "SELECT m.id, m.content FROM messages m "
            "LEFT JOIN embedding_index ei ON m.id = ei.message_id "
            "WHERE ei.message_id IS NULL "
            "ORDER BY m.created_at DESC LIMIT ?",
            (batch_size,),
        ).fetchall()

        if not rows:
            print(f"  All messages already embedded")
            return stats

        # Filter short messages, prepare batch
        to_embed = [(r["id"], r["content"]) for r in rows if len(r["content"]) >= 20]
        stats["skipped"] = len(rows) - len(to_embed)

        if not to_embed:
            return stats

        # Batch embed via Ollama (single API call)
        print(f"  Embedding {len(to_embed)} messages (batch)...")
        texts = [content for _, content in to_embed]
        vectors = _get_embeddings_batch(texts)

        for (msg_id, _), vec in zip(to_embed, vectors):
            if vec is None:
                stats["failed"] += 1
                continue
            blob = _vec_to_blob(vec)
            cursor = conn.execute(
                "INSERT INTO message_embeddings(embedding) VALUES (?)", (blob,)
            )
            conn.execute(
                "INSERT INTO embedding_index(rowid, message_id, source_type, embedded_at) "
                "VALUES (?, ?, 'message', ?)",
                (cursor.lastrowid, msg_id, datetime.now(timezone.utc).isoformat()),
            )
            stats["embedded"] += 1

        conn.commit()
        print(f"  Done: {stats['embedded']} embedded, {stats['skipped']} skipped, {stats['failed']} failed")
    finally:
        conn.close()

    return stats


# -- Semantic Search ----------------------------------------------------------

def semantic_search(
    query: str,
    vault_domain: str | None = None,
    limit: int = 10,
    db_path: Path = None,
) -> list[dict]:
    """Semantic vector search across context messages.

    Returns list of dicts with message content + similarity score.
    """
    query_vec = _get_embedding(query)
    if query_vec is None:
        log.warning("Could not embed query — falling back to empty results")
        return []

    conn = _get_conn(db_path)
    query_blob = _vec_to_blob(query_vec)

    # Vector similarity search (sqlite-vec requires k=? for KNN)
    k = min(limit * 3, 100)  # Over-fetch for vault filtering, cap at 100
    rows = conn.execute(
        "SELECT me.rowid, me.distance, ei.message_id "
        "FROM message_embeddings me "
        "JOIN embedding_index ei ON me.rowid = ei.rowid "
        "WHERE me.embedding MATCH ? AND k = ?",
        (query_blob, k),
    ).fetchall()

    # Batch fetch messages (avoid N+1)
    msg_ids = [row["message_id"] for row in rows]
    if not msg_ids:
        conn.close()
        return []

    placeholders = ",".join("?" * len(msg_ids))
    vault_filter = " AND vault_domain = ?" if vault_domain else ""
    params = msg_ids + ([vault_domain] if vault_domain else [])
    msgs = conn.execute(
        f"SELECT * FROM messages WHERE id IN ({placeholders}){vault_filter}",
        params,
    ).fetchall()
    msg_map = {m["id"]: m for m in msgs}

    # Build results in KNN distance order
    results = []
    for row in rows:
        msg = msg_map.get(row["message_id"])
        if msg is None:
            continue
        results.append({
            "type": "message",
            "id": msg["id"],
            "agent_id": msg["agent_id"],
            "vault_domain": msg["vault_domain"],
            "role": msg["role"],
            "content": msg["content"],
            "token_count": msg["token_count"],
            "created_at": msg["created_at"],
            "similarity": 1.0 - row["distance"],
        })
        if len(results) >= limit:
            break

    conn.close()
    return results


def hybrid_search(
    query: str,
    vault_domain: str,
    limit: int = 10,
    db_path: Path = None,
) -> list[dict]:
    """Hybrid search: BM25 (FTS5) + vector similarity + reranking.

    Combines keyword matches with semantic similarity for best-of-both recall.
    """
    # Deferred import: prevents circular dependency if context_manager ever imports this module
    from context_manager import ContextStore

    store = ContextStore(db_path=db_path)

    # 1. FTS5 keyword search (BM25)
    fts_results = store.slos_grep(vault_domain, query, scope="both", limit=limit * 2)
    fts_ids = {r["id"] for r in fts_results}

    # 2. Vector semantic search
    vec_results = semantic_search(query, vault_domain=vault_domain, limit=limit * 2, db_path=db_path)
    vec_ids = {r["id"] for r in vec_results}

    # 3. Merge and rerank
    # Score: items found by BOTH methods rank highest
    scored = {}
    for r in fts_results:
        rid = r["id"]
        scored[rid] = {**r, "fts_hit": True, "vec_hit": rid in vec_ids, "similarity": 0.0}

    for r in vec_results:
        rid = r["id"]
        if rid in scored:
            scored[rid]["similarity"] = r.get("similarity", 0.0)
        else:
            scored[rid] = {**r, "fts_hit": False, "vec_hit": True}

    # Rerank: both > vec-only > fts-only, then by similarity
    def rank_key(item):
        both = 1 if item.get("fts_hit") and item.get("vec_hit") else 0
        sim = item.get("similarity", 0.0)
        return (both, sim)

    ranked = sorted(scored.values(), key=rank_key, reverse=True)
    return ranked[:limit]


# -- CLI ----------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Context Embeddings — semantic search")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--embed", action="store_true", help="Embed pending messages")
    group.add_argument("--search", metavar="QUERY", help="Semantic search")
    group.add_argument("--hybrid", metavar="QUERY", help="Hybrid BM25 + vector search")
    group.add_argument("--status", action="store_true", help="Show embedding stats")

    parser.add_argument("--vault", default=None, help="Filter by vault domain")
    parser.add_argument("--limit", type=int, default=10, help="Max results")
    parser.add_argument("--batch", type=int, default=50, help="Batch size for --embed")

    args = parser.parse_args()

    if args.embed:
        stats = embed_pending(batch_size=args.batch)
        print(f"\n  {stats}")

    elif args.search:
        results = semantic_search(args.search, vault_domain=args.vault, limit=args.limit)
        print(f"\n=== Semantic Search: '{args.search}' ===\n")
        for r in results:
            sim = r.get("similarity", 0)
            print(f"  [{sim:.3f}] [{r['vault_domain']}] {r['created_at'][:16]}")
            print(f"    {r['content'][:150]}...")
            print()

    elif args.hybrid:
        results = hybrid_search(args.hybrid, vault_domain=args.vault or "finance", limit=args.limit)
        print(f"\n=== Hybrid Search: '{args.hybrid}' ===\n")
        for r in results:
            fts = "FTS" if r.get("fts_hit") else "   "
            vec = "VEC" if r.get("vec_hit") else "   "
            sim = r.get("similarity", 0)
            print(f"  [{fts}+{vec}] [{sim:.3f}] {r.get('created_at', '?')[:16]}")
            print(f"    {r.get('content', '')[:150]}...")
            print()

    elif args.status:
        conn = _get_conn()
        total_msgs = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        total_embedded = conn.execute("SELECT COUNT(*) FROM embedding_index").fetchone()[0]
        conn.close()
        print(f"\n=== Embedding Status ===")
        print(f"  Messages: {total_msgs}")
        print(f"  Embedded: {total_embedded}")
        print(f"  Pending: {total_msgs - total_embedded}")


if __name__ == "__main__":
    main()
