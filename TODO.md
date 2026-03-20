# TODO: Agency-Agents Integration

Changes required to make local-memory-mcp compatible with the agency-agents workflow expectations.

## Phase 1: Schema Changes — DONE

- [x] **1.1** Add `tags TEXT[]` column to PostgreSQL schema in `sql/setup_database.sql`
- [x] **1.2** Add `tags` column (JSON-encoded list) to SQLite schema in `sqlite_memory_api.py`
- [x] **1.3** Add `memory_versions` table to PostgreSQL schema for rollback support
- [x] **1.4** Add `memory_versions` table to SQLite schema for rollback support
- [x] **1.5** FAISS metadata unchanged (tags stored in DB, not FAISS) — N/A
- [x] **1.6** SQLite migration: `ALTER TABLE` adds `tags` column to existing databases

## Phase 2: API Layer — DONE

- [x] **2.1** `PostgresMemoryAPI.store_memory()` — accepts `tags: List[str]`
- [x] **2.2** `SQLiteMemoryAPI.store_memory()` — accepts `tags: List[str]`
- [x] **2.3** `PostgresMemoryAPI.retrieve_memories()` — `tags` filter with `@>` operator
- [x] **2.4** `SQLiteMemoryAPI.retrieve_memories()` — `tags` filter with JSON matching
- [x] **2.5** `PostgresMemoryAPI.update_memory()` — snapshots to `memory_versions` before update
- [x] **2.6** `SQLiteMemoryAPI.update_memory()` — snapshots to `memory_versions` before update
- [x] **2.7** `PostgresMemoryAPI.rollback_memory(memory_id, domain)`
- [x] **2.8** `SQLiteMemoryAPI.rollback_memory(memory_id)`

## Phase 3: MCP Tool Layer — DONE

- [x] **3.1** `remember` tool on both servers
- [x] **3.2** `recall` tool on both servers
- [x] **3.3** `rollback` tool on both servers
- [x] **3.4** `search` tool on both servers
- [x] **3.5** Legacy tools kept as aliases (Option A)

## Phase 4: Tests — DONE

- [x] **4.1** Schema tests updated (tags column, memory_versions table, migration)
- [x] **4.2** Tag-based storage and retrieval tests for both backends
- [x] **4.3** Version history creation on update tests
- [x] **4.4** Rollback tests (restore, not-found, no-history, multiple rollbacks, vector store)
- [x] **4.5** `remember`, `recall`, `rollback`, `search` MCP tool tests for both servers
- [x] **4.6** Integration tests: multi-agent handoff, tag-based recall
- [x] **4.7** 100% coverage maintained (187 tests, 714 statements, 0 misses)

## Phase 5: Documentation & Compatibility — DONE

- [x] **5.1** CLAUDE.md updated with new tool names and signatures
- [x] **5.2** README.md updated with agency-agents integration section, workflow patterns, handoff examples
- [x] **5.3** MCP config snippets added (Claude Code CLI, .mcp.json, Docker) matching agency-agents format
- [x] **5.4** `setup_database.sql` updated with new schema

---

## Phase 6: Named Checkpoints (atomic multi-memory rollback)

**Problem:** `rollback(memory_id)` only reverts a single memory. In real workflows, a bad
decision spawns multiple new memories and updates several existing ones. There is no way to
atomically undo all of that — the agent would have to track every ID it touched and roll each
back individually, and newly created memories can't be rolled back at all.

**Solution:** Named checkpoints, with a tool rename to respect agency-agents conventions:

- **`rollback(checkpoint_id)`** becomes the atomic multi-memory rollback (what agency-agents
  expects when it says "roll back to a known-good state"). Creates/deletes/restores as needed.
- **`rollback_memory(memory_id)`** takes over the current per-memory version revert logic.
  Surgical single-memory tool, still useful but not the primary interface.

This way the agency-agents contract (`remember`, `recall`, `rollback`, `search`) stays intact —
`rollback` just gains checkpoint-level power instead of single-memory scope.

### Phase 6.1: Schema — DONE

- [x] **6.1.1** `{domain}_checkpoints` table in PostgreSQL `setup_database.sql`
- [x] **6.1.2** `checkpoints` table in SQLite `_initialize_db()`
- [x] **6.1.3** SQLite migration: `CREATE TABLE IF NOT EXISTS` handles existing DBs

### Phase 6.2: API Layer — DONE

- [x] **6.2.1** `PostgresMemoryAPI.create_checkpoint()`
- [x] **6.2.2** `SQLiteMemoryAPI.create_checkpoint()`
- [x] **6.2.3** `PostgresMemoryAPI.rollback_to_checkpoint()` — full 5-step atomic rollback
- [x] **6.2.4** `SQLiteMemoryAPI.rollback_to_checkpoint()` — with FAISS vector store sync
- [x] **6.2.5** `rollback_memory()` kept unchanged on both APIs
- [x] **6.2.6** `PostgresMemoryAPI.list_checkpoints()`
- [x] **6.2.7** `SQLiteMemoryAPI.list_checkpoints()`

### Phase 6.3: MCP Tool Layer — DONE

- [x] **6.3.1** `checkpoint` tool on both servers
- [x] **6.3.2** `rollback` rewired to `rollback_to_checkpoint` (checkpoint-based)
- [x] **6.3.3** `rollback_memory` tool on both servers (per-memory, old `rollback` logic)
- [x] **6.3.4** `list_checkpoints` tool on both servers
- [x] **6.3.5** Docstrings updated
- [x] **6.3.6** Existing tests updated for new `rollback` signature

### Phase 6.4: Tests — DONE

- [x] **6.4.1** Schema tests: checkpoints table on both backends
- [x] **6.4.2** `create_checkpoint` tests on both backends
- [x] **6.4.3–6.4.8** `rollback_to_checkpoint` tests: deletes new, restores updated, mixed scenario,
  not found, cleanup, later checkpoints, with/without embeddings
- [x] **6.4.9** `rollback_memory` tests preserved (existing behavior)
- [x] **6.4.10** `list_checkpoints` tests on both backends
- [x] **6.4.11** MCP tool tests for `checkpoint`, `rollback`, `rollback_memory`, `list_checkpoints`
- [x] **6.4.12** Integration test: full agency-agents scenario (3 new + 2 updated → rollback → verified)
- [x] **6.4.13** Vector store consistency test for checkpoint rollback
- [x] **6.4.14** 100% coverage maintained (225 tests, 843 statements, 0 misses)

### Phase 6.5: Documentation — DONE

- [x] **6.5.1** CLAUDE.md updated with checkpoint tools and revised rollback
- [x] **6.5.2** README.md agency-agents integration section updated with checkpoint workflow
- [x] **6.5.3** README.md tools section updated: `checkpoint`, `rollback`, `rollback_memory`, `list_checkpoints`

---

## Phase 7: Memory Lifecycle Management (unbounded growth)

**Problem:** Memory tables, version history, and checkpoints grow indefinitely. A long-lived
project with many agents, frequent updates, and regular checkpointing will accumulate data
that degrades search quality (signal drowns in noise) and consumes storage. There is no
pruning, consolidation, expiration, or archival mechanism.

Three distinct sub-problems:

1. **Version bloat** — Every `update_memory` creates a version snapshot. A memory updated
   50 times has 50 historical rows. After a successful checkpoint rollback, orphaned versions
   are cleaned up, but versions from normal (non-rolled-back) updates accumulate forever.

2. **Stale memories** — Memories stored months ago for a project that's long finished still
   appear in search results. Low-importance memories from casual conversations compete with
   high-importance current decisions.

3. **Checkpoint accumulation** — Checkpoints that were never rolled back linger. They're small
   (just a row with a timestamp), but they add noise to `list_checkpoints` output and signal
   that no one is cleaning up.

**Solution:** A three-layer retention system, each layer independently useful:

**Layer 1 — Version retention policy:** Cap the number of version snapshots per memory. When
a new version is created and the count exceeds the limit, delete the oldest. Default: 20
versions per memory. This bounds the version table to `N_memories * 20` rows maximum.

**Layer 2 — TTL expiration for temporal memories:** Memories with a natural expiration
(meeting times, sprint status, temporary workarounds) get an optional `expires_at` field.
A `purge_expired` method deletes them after expiry. Memories without `expires_at` live
forever (the default, preserving current behavior). This targets memories that the agent
*can* predict are ephemeral at store time.

**Layer 3 — Consolidation summaries for accumulated context:** Memories that are valuable
but accumulating (project decisions, architecture choices, client preferences) shouldn't
expire — but 50 of them should eventually become 5 summaries. An agent or human explicitly
triggers `consolidate_memories()` at a project milestone. An LLM reads the batch and
produces summary memories that preserve key decisions, constraints, and outcomes. The
originals are then deleted. Information survives in compressed form; storage and search
noise are reduced. This is lossy compression — granular details (exact URLs, error codes)
may not survive, but the narrative and decision rationale do.

**When to use which:**
- **TTL** for *inherently temporal* memories: "meeting moved to 3pm Tuesday", "deploy is
  blocked on CI fix", "use workaround X until patch ships". These have a natural shelf life.
- **Consolidation** for *valuable but numerous* memories: project architecture decisions,
  client preferences, sprint retrospective notes. These shouldn't expire, but they should
  eventually be compressed.
- **Neither** for *permanently critical* memories: compliance requirements, security
  constraints, core user preferences. These stay as-is forever.

**Layer 4 — Checkpoint auto-cleanup:** Checkpoints older than a configurable age are
automatically deleted during `create_checkpoint` (e.g., delete checkpoints older than 7 days,
except the most recent checkpoint). This keeps the checkpoint list manageable without
requiring manual cleanup.

### Phase 7.1: Version Retention Policy — DONE

- [x] **7.1.1** `MAX_VERSIONS_PER_MEMORY` config (default: 20, env var)
- [x] **7.1.2** SQLite auto-prune oldest versions on update
- [x] **7.1.3** PostgreSQL auto-prune oldest versions on update
- [x] **7.1.4** Tests: limit exceeded, limit=1, env var config

### Phase 7.2: TTL Expiration — DONE

- [x] **7.2.1** `expires_at` column on both schemas + SQLite migration
- [x] **7.2.2** `store_memory()` accepts `ttl_seconds` on both APIs
- [x] **7.2.3** `purge_expired()` on both APIs (deletes + cleans versions)
- [x] **7.2.4** `purge_expired` MCP tool on both servers
- [x] **7.2.5** Expired memories filtered from all search paths
- [x] **7.2.6** `remember` tool accepts `ttl_seconds`
- [x] **7.2.7** SQLite migration for `expires_at`
- [x] **7.2.8** Tests: TTL storage, expiry filtering, purge, migration, NULL = permanent

### Phase 7.3: Consolidation Summaries — DONE

- [x] **7.3.1** `consolidate_memories()` on both APIs (pluggable `summarizer_fn`)
- [x] **7.3.2** LLM interface: callable parameter, servers use Ollama generate endpoint
- [x] **7.3.3** `consolidate_memories` MCP tool on both servers
- [x] **7.3.4** Summaries tagged `["consolidated"]` with `consolidated_from` metadata
- [x] **7.3.5** Tests: consolidation lifecycle, originals deleted, metadata correct, skip below threshold
- [x] **7.3.6** Tests: mock summarizer_fn (no real LLM calls)

### Phase 7.4: Checkpoint Auto-Cleanup — DONE

- [x] **7.4.1** `CHECKPOINT_RETENTION_DAYS` config (default: 30, env var)
- [x] **7.4.2** Auto-prune old checkpoints on `create_checkpoint`, most recent always kept
- [x] **7.4.3** Tests: old pruned, recent kept, config override

### Phase 7.5: Tests & Documentation — DONE

- [x] **7.5.1** Version retention tests under heavy updates
- [x] **7.5.2** TTL expiration lifecycle tests
- [x] **7.5.3** Consolidation lifecycle tests
- [x] **7.5.4** 100% coverage maintained (263 tests, 973 statements, 0 misses)
- [x] **7.5.5** CLAUDE.md: lifecycle management section with decision guide
- [x] **7.5.6** README.md: new env vars, tools, Memory Lifecycle section with examples

---

## Phase 8: Memory Export/Import (portability)

**Problem:** Memories are locked to the database instance they were created on. When migrating
from local development to a web server, or moving between devices, there's no way to take your
memories with you.

**Solution:** JSON-based export/import that works across backends. Export from SQLite, import
to PostgreSQL (or vice versa). The export file contains everything: memories, version history,
and checkpoints — a complete snapshot of the memory state.

**Export format:**
```json
{
  "version": 1,
  "exported_at": "2026-03-19T...",
  "source_backend": "sqlite",
  "memories": [...],
  "memory_versions": [...],
  "checkpoints": [...]
}
```

Embeddings are excluded from export (they're large and backend-specific). The target instance
regenerates them via Ollama on import if available.

### Phase 8.1: API Layer — DONE

- [x] **8.1.1** `SQLiteMemoryAPI.export_memories()` — all tables, embeddings excluded
- [x] **8.1.2** `SQLiteMemoryAPI.import_memories()` — skips duplicates, regenerates embeddings
- [x] **8.1.3** `PostgresMemoryAPI.export_memories(domain?)` — domain-scoped
- [x] **8.1.4** `PostgresMemoryAPI.import_memories(data, domain?)` — domain-aware, embedding regen

### Phase 8.2: MCP Tool Layer — DONE

- [x] **8.2.1** `export_memories` tool on both servers
- [x] **8.2.2** `import_memories` tool on both servers

### Phase 8.3: Tests — DONE

- [x] **8.3.1** SQLite export: all tables included, embeddings excluded
- [x] **8.3.2** SQLite import: round-trip with versions (export → import → rollback works)
- [x] **8.3.3** SQLite import: duplicates skipped (memories, versions, checkpoints)
- [x] **8.3.4** PostgreSQL export/import with mocked psycopg2, duplicate skipping, embedding regen
- [x] **8.3.5** MCP tool tests for both servers
- [x] **8.3.6** Integration test: export from one SQLite instance, import to another, verify data
- [x] **8.3.7** 100% coverage maintained (285 tests, 1100 statements, 0 misses)

### Phase 8.4: Documentation — DONE

- [x] **8.4.1** CLAUDE.md updated with export/import tools
- [x] **8.4.2** README.md updated with Migration Guide (local → server workflow)
