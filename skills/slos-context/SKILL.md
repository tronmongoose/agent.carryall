---
name: slos-context
description: Manages persistent agent context — recall conversation history, search across vault interactions, compact old messages into summaries. Use when asked "recall context", "what happened in [vault]", "search context for", "context status", "compact history", or "what did [agent] do".
---

# SLOS Context Manager

Persistent DAG-backed context for carryall agents. Every agent message is stored in SQLite, old messages compress into hierarchical summaries, and agents can recall relevant history.

## Quick Commands

### Recall recent context for a vault

```python
from usecases.context_manager import ContextStore
from usecases.context_acl import can_read

store = ContextStore()

# Check ACL first
agent_id = "finance-agent"  # use appropriate agent
vault = "finance"

if can_read(agent_id, vault):
    summaries = store.slos_recall(agent_id, vault, n=5)
    for s in summaries:
        print(f"[depth {s['depth']}] {s['content'][:200]}")
```

### Search context (FTS5 grep)

```python
results = store.slos_grep(agent_id, vault, "budget overrun", limit=20)
for r in results:
    print(f"[{r['source']}] {r['content'][:200]}")
```

### Ingest a message

```python
from usecases.context_acl import can_write

if can_write(agent_id, vault):
    store.ingest(agent_id, vault, "assistant", "Q4 budget analysis complete...")
```

### Assemble context for an agent prompt

```python
context = store.assemble(agent_id, vault, token_budget=4096)
# Returns: list of {"role": ..., "content": ..., "source": "summary"|"message"}
```

### Check context stats

```python
stats = store.get_stats()
for vault_name, counts in stats.items():
    print(f"{vault_name}: {counts['messages']} messages, {counts['summaries']} summaries")
```

### Compact old messages (Phase 1+)

```bash
python usecases/context_compactor.py --compact              # all vaults
python usecases/context_compactor.py --domain finance        # one vault
python usecases/context_compactor.py --status                # stats
python usecases/context_compactor.py --review-pending        # Phase 2: list unreviewed summaries
python usecases/context_compactor.py --promote <summary_id>  # Phase 2: approve a summary
```

Or via Makefile:

```bash
make context-compact   # compact all vaults
make context-status    # show message/summary counts
```

## Architecture

```
Messages (raw agent I/O)
    │
    ▼ compact_leaves (Ollama, depth 0)
Leaf Summaries (promoted=1, auto)
    │
    ▼ compact_condensed (Sonnet, depth 1+)
Condensed Summaries (promoted=0, manual review)
    │
    ▼ promote / sentinel (future)
Durable Context (promoted=1, available to assemble)
```

## ACL Rules

Each agent can only read/write context for vaults matching their policy scopes:

| Agent | Read | Write |
|-------|------|-------|
| executive-agent | all except community | meta |
| finance-agent | finance | finance |
| startup-agent | startup | startup |
| health-agent | health | health |
| personal-agent | personal | personal |
| community-agent | community | community |
| email-agent | personal | personal |

ACL enforcement is in `usecases/context_acl.py`. Executive-agent has broad read for cross-domain context assembly.

## DB Location

`~/slos/config/context/context.db` — SQLite with WAL mode. Outside vaults (matches `authority.db` pattern).

## Error Handling

- **DB locked**: WAL mode + `busy_timeout=5000` handles concurrent agents. If persistent, check for stale locks.
- **Large files**: Messages >8K tokens are stored separately in `large_files` table with a summary placeholder.
- **Token budget exceeded**: `assemble()` drops oldest promoted summaries first, always keeps fresh tail.

## Constraints
- **NEVER**: Delete messages or summaries from the context store
- **BUDGET**: 100 messages per embedding batch
- **GATE**: sqlite-vec + nomic-embed-text model required for semantic search
- **FAIL**: Fall back to keyword search (FTS5) if vector search is unavailable

## Gotchas

- **sqlite-vec must be installed separately.** Semantic search requires `pip install sqlite-vec`. Without it, `slos_grep` still works (FTS5 text search), but vector similarity search is unavailable.
- **Ollama nomic-embed-text model must be pulled.** Embedding generation requires this model: `ollama pull nomic-embed-text`. If it's missing, embedding calls fail silently and semantic search returns no results.
- **New messages aren't searchable immediately.** Embeddings are generated during the compaction cycle, not at ingest time. A message ingested now won't appear in semantic search results until the next `make context-compact` or scheduled compaction runs.
