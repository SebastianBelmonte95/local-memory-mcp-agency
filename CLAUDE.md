# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Local Context Memory MCP — a persistent memory system for AI agents using the Model Context Protocol (MCP). Built with FastMCP and Python 3.12. Two parallel implementations exist: SQLite+FAISS (lightweight/personal) and PostgreSQL+pgvector (production/multi-domain).

## Running the Servers

```bash
# SQLite version (local)
./run_sqlite.sh
# or directly:
pip install -r requirements.sqlite.txt
python3 src/sqlite_memory_server.py

# PostgreSQL version (local)
./run_postgres.sh
# or directly:
pip install -r requirements.pgvector.txt
python3 src/postgres_memory_server.py
```

## Docker Builds

```bash
# Build SQLite image
docker build -f Dockerfile.sqlite_version -t local-memory-mcp:sqlite_version .

# Build PostgreSQL image
docker build -f Dockerfile.postgres_version -t local-memory-mcp:postgres_version .

# Run SQLite container
docker run --rm -i -v $(pwd)/data:/app/data local-memory-mcp:sqlite_version

# Run PostgreSQL container
docker run --rm -i -v $(pwd)/postgres_data:/var/lib/postgresql/data local-memory-mcp:postgres_version
```

Docker Hub images are published as `cunicopia/local-memory-mcp:sqlite` and `cunicopia/local-memory-mcp:postgres`. Multi-platform builds (amd64/arm64) use `docker buildx`. See `docs/docker_build.md` for release procedures.

## Architecture

Both implementations follow the same pattern:

```
MCP Client → FastMCP Server (*_memory_server.py) → Memory API (*_memory_api.py) → Database
                                                         ↓
                                                   Ollama Embeddings (ollama_embeddings.py)
```

**SQLite path:** `sqlite_memory_server.py` → `sqlite_memory_api.py` (SQLite DB) + `sqlite_vector_api.py` (FAISS index)
**PostgreSQL path:** `postgres_memory_server.py` → `postgres_memory_api.py` (PostgreSQL + pgvector)

Key design decisions:
- Ollama is optional — both implementations gracefully degrade to text search (full-text/LIKE) when embeddings are unavailable
- Embeddings are 768-dimensional vectors (nomic-embed-text model by default)
- PostgreSQL version supports domain-based memory isolation via dynamically created tables (`{domain}_memories`)
- The `sql/setup_database.sql` creates a `create_domain_memories_table()` PL/pgSQL function used at runtime
- The PostgreSQL Docker image is self-contained: it runs PostgreSQL internally with pgvector compiled from source, then starts the MCP server on top
- `docker-entrypoint-postgres.sh` routes setup output to stderr and MCP protocol output to stdout (required for Claude Desktop stdio transport)

## MCP Interface

**Agency-Agents compatible tools** (primary interface):
- `remember(content, tags?, source?, importance?, domain?)` — store decisions/deliverables with tags
- `recall(tags?, query?, domain?, limit?)` — retrieve by tag filter and/or semantic search
- `checkpoint(name, tags?, domain?)` — create a named save point before risky work
- `rollback(checkpoint_id, domain?)` — atomically undo all changes made after a checkpoint (deletes new memories, restores updated ones)
- `rollback_memory(memory_id, domain?)` — revert a single memory to its previous version (per-memory, stack-based)
- `search(query, domain?, limit?)` — broad semantic/text search across all memories
- `list_checkpoints(domain?)` — list available checkpoints

**Legacy tools** (kept for backward compatibility):
- `store_memory`, `update_memory`, `search_memories`

Both versions also expose: `summarize_memories` prompt, `memory://` resource URI scheme. PostgreSQL additionally exposes `list_memory_domains`.

**Tags** are the primary organizational mechanism — use agent name, project name, and topic (e.g., `["backend-architect", "retroboard", "api-spec"]`). `recall` filters by ALL provided tags (AND logic).

**Checkpoints** enable atomic multi-memory rollback. `checkpoint()` creates a named save point; `rollback()` undoes everything after it — deleting new memories and restoring updated ones. For single-memory rollback, use `rollback_memory()` which pops one version from the history stack.

## Configuration

Environment variables configured via `.env` (see `.env.example`): database connection (PostgreSQL), Ollama API URL/model, default memory domain.

## Testing

Uses pytest with `pythonpath = src` (configured in `pytest.ini`). All external services (Ollama, PostgreSQL) are mocked; SQLite and FAISS run natively.

```bash
# Run all tests
.venv/python.exe -m pytest

# With coverage
.venv/python.exe -m pytest --cov=src --cov-report=term-missing

# By marker
.venv/python.exe -m pytest -m unit          # unit tests only
.venv/python.exe -m pytest -m integration   # integration tests only
.venv/python.exe -m pytest -m sqlite        # SQLite backend only
.venv/python.exe -m pytest -m postgres      # PostgreSQL backend only

# Single file or test
.venv/python.exe -m pytest tests/test_ollama_embeddings.py
.venv/python.exe -m pytest tests/test_ollama_embeddings.py::TestGetEmbedding::test_caching -v
```

Test dependencies: `pip install -r requirements.test.txt` (pytest, pytest-cov).
