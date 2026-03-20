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

## Phase 5: Documentation & Compatibility

- [x] **5.1** CLAUDE.md updated with new tool names and signatures
- [x] **5.2** README.md updated with agency-agents integration section, workflow patterns, handoff examples
- [x] **5.3** MCP config snippets added (Claude Code CLI, .mcp.json, Docker) matching agency-agents format
- [x] **5.4** `setup_database.sql` updated with new schema
