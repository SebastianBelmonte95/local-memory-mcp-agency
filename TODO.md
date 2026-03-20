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
