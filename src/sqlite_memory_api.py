import sqlite3
import json
import os
import time
import pathlib
from typing import List, Dict, Any, Optional
from sqlite_vector_api import FAISSVectorAPI

def _normalize_tags(tags: List[str]) -> List[str]:
    """Normalize tags to lowercase and stripped whitespace for consistent matching."""
    return [t.strip().lower() for t in tags] if tags else []


class SQLiteMemoryAPI:
    def __init__(self, db_path: str = None, vector_store: FAISSVectorAPI = None):
        # Set default path or use provided path
        if db_path is None:
            data_dir = os.environ.get("MCP_DATA_DIR", ".")
            pathlib.Path(data_dir).mkdir(parents=True, exist_ok=True)
            self.db_path = os.path.join(data_dir, "memory.db")
        else:
            self.db_path = db_path

        self.max_versions = int(os.environ.get("MAX_VERSIONS_PER_MEMORY", "20"))
        self.checkpoint_retention_days = int(os.environ.get("CHECKPOINT_RETENTION_DAYS", "30"))

        self._initialize_db()

        # Initialize vector store
        self.vector_store = vector_store

    def _initialize_db(self):
        """Initialize the SQLite database with required tables."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Create memories table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '[]',
            metadata TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            expires_at REAL DEFAULT NULL
        )
        ''')

        # Create version history table for rollback support
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS memory_versions (
            version_id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '[]',
            metadata TEXT NOT NULL,
            created_at REAL NOT NULL
        )
        ''')

        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_memory_versions_memory_id
        ON memory_versions (memory_id, version_id DESC)
        ''')

        # Create checkpoints table for atomic multi-memory rollback
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS checkpoints (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '[]',
            created_at REAL NOT NULL
        )
        ''')

        # Migrations for existing databases
        cursor.execute("PRAGMA table_info(memories)")
        columns = [row[1] for row in cursor.fetchall()]
        if "tags" not in columns:
            cursor.execute("ALTER TABLE memories ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'")
        if "expires_at" not in columns:
            cursor.execute("ALTER TABLE memories ADD COLUMN expires_at REAL DEFAULT NULL")

        conn.commit()
        conn.close()

    def store_memory(self, content: str, metadata: Dict[str, Any] = None,
                     tags: List[str] = None, ttl_seconds: int = None) -> str:
        """Store a new memory chunk in the database."""
        memory_id = f"mem_{int(time.time() * 1000)}"
        timestamp = time.time()
        tags = _normalize_tags(tags)
        expires_at = (timestamp + ttl_seconds) if ttl_seconds else None

        metadata = metadata or {}
        metadata.update({
            "created_at": timestamp,
            "updated_at": timestamp,
        })

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            "INSERT INTO memories (id, content, tags, metadata, created_at, updated_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (memory_id, content, json.dumps(tags), json.dumps(metadata), timestamp, timestamp, expires_at)
        )

        conn.commit()
        conn.close()

        # Add to vector store if available
        if self.vector_store:
            try:
                self.vector_store.add_text(memory_id, content, metadata)
            except Exception as e:
                pass  # Silently handle vector store errors
        return memory_id

    def retrieve_memories(self, query: str, limit: int = 5, use_vector: bool = True,
                          tags: List[str] = None) -> List[Dict[str, Any]]:
        """
        Retrieve memories relevant to the query.

        If vector_store is available and use_vector is True, use semantic search.
        Otherwise, fall back to SQL text search.
        When tags are provided, filter results to only include memories matching ALL tags.
        """
        tags = _normalize_tags(tags) if tags else None
        # Try vector search first if available and requested
        if self.vector_store and use_vector:
            try:
                # Performing vector search
                vector_results = self.vector_store.search(query, limit * 3 if tags else limit)

                # Convert to standard format
                results = []
                for result in vector_results:
                    results.append({
                        "id": result["id"],
                        "content": result["content"],
                        "metadata": result["metadata"],
                        "score": result.get("score", 0)
                    })

                if results:
                    # If tags filter requested, apply it by looking up tags from DB
                    if tags:
                        results = self._filter_by_tags(results, tags, limit)
                    return results
                else:
                    # Vector search returned no results, falling back to text search
                    pass
            except Exception as e:
                # Vector search failed, falling back to text search
                pass

        # Fall back to text search
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        now = time.time()
        if tags:
            # Build tag filter: all provided tags must be present
            # We fetch more rows and filter in Python since SQLite JSON support is limited
            cursor.execute(
                "SELECT id, content, tags, metadata FROM memories WHERE (expires_at IS NULL OR expires_at > ?) ORDER BY updated_at DESC LIMIT ?",
                (now, limit * 10,)
            )
            results = []
            for row in cursor.fetchall():
                row_tags = json.loads(row["tags"])
                if all(t in row_tags for t in tags):
                    entry = {
                        "id": row["id"],
                        "content": row["content"],
                        "tags": row_tags,
                        "metadata": json.loads(row["metadata"])
                    }
                    # If there's also a query, check content match
                    if query and query.lower() not in row["content"].lower():
                        continue
                    results.append(entry)
                    if len(results) >= limit:
                        break
        else:
            cursor.execute(
                "SELECT id, content, tags, metadata FROM memories WHERE content LIKE ? AND (expires_at IS NULL OR expires_at > ?) ORDER BY updated_at DESC LIMIT ?",
                (f"%{query}%", now, limit)
            )
            results = []
            for row in cursor.fetchall():
                results.append({
                    "id": row["id"],
                    "content": row["content"],
                    "tags": json.loads(row["tags"]),
                    "metadata": json.loads(row["metadata"])
                })

        conn.close()
        return results

    def _filter_by_tags(self, results: List[Dict[str, Any]], tags: List[str],
                        limit: int) -> List[Dict[str, Any]]:
        """Filter vector search results by tags, looking up tags from the database."""
        if not results:
            return results

        ids = [r["id"] for r in results]
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        placeholders = ",".join("?" for _ in ids)
        cursor.execute(
            f"SELECT id, tags FROM memories WHERE id IN ({placeholders})", ids
        )

        tag_map = {}
        for row in cursor.fetchall():
            tag_map[row["id"]] = json.loads(row["tags"])
        conn.close()

        filtered = []
        for r in results:
            row_tags = tag_map.get(r["id"], [])
            if all(t in row_tags for t in tags):
                r["tags"] = row_tags
                filtered.append(r)
                if len(filtered) >= limit:
                    break
        return filtered

    def update_memory(self, memory_id: str, content: str = None,
                      metadata: Dict[str, Any] = None, tags: List[str] = None) -> bool:
        """Update an existing memory chunk. Snapshots the old version for rollback."""
        tags = _normalize_tags(tags) if tags is not None else None
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Get current memory
        cursor.execute("SELECT content, tags, metadata FROM memories WHERE id = ?", (memory_id,))
        result = cursor.fetchone()

        if not result:
            conn.close()
            return False

        current_content, current_tags_str, current_metadata_str = result
        current_metadata = json.loads(current_metadata_str)
        current_tags = json.loads(current_tags_str)

        # Snapshot current state to version history
        cursor.execute(
            "INSERT INTO memory_versions (memory_id, content, tags, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
            (memory_id, current_content, current_tags_str, current_metadata_str, time.time())
        )

        # Prune old versions if exceeding retention limit
        cursor.execute(
            "SELECT COUNT(*) FROM memory_versions WHERE memory_id = ?", (memory_id,)
        )
        version_count = cursor.fetchone()[0]
        if version_count > self.max_versions:
            cursor.execute("""
                DELETE FROM memory_versions WHERE version_id IN (
                    SELECT version_id FROM memory_versions
                    WHERE memory_id = ? ORDER BY version_id ASC LIMIT ?
                )
            """, (memory_id, version_count - self.max_versions))

        # Update content if provided
        new_content = content if content is not None else current_content

        # Update tags if provided
        new_tags = tags if tags is not None else current_tags

        # Update metadata if provided
        if metadata is not None:
            new_metadata = current_metadata.copy()
            new_metadata.update(metadata)
        else:
            new_metadata = current_metadata

        # Always update the updated_at timestamp
        new_metadata["updated_at"] = time.time()

        cursor.execute(
            "UPDATE memories SET content = ?, tags = ?, metadata = ?, updated_at = ? WHERE id = ?",
            (new_content, json.dumps(new_tags), json.dumps(new_metadata), new_metadata["updated_at"], memory_id)
        )

        conn.commit()
        conn.close()

        # Update vector store if available
        if self.vector_store:
            try:
                if content is not None:
                    self.vector_store.update_text(memory_id, content, new_metadata)
                elif metadata is not None:
                    self.vector_store.update_text(memory_id, None, new_metadata)
            except Exception as e:
                pass  # Silently handle vector store update errors

        return True

    def create_checkpoint(self, name: str, tags: List[str] = None) -> str:
        """Create a named checkpoint for atomic multi-memory rollback."""
        checkpoint_id = f"chk_{int(time.time() * 1000)}"
        timestamp = time.time()
        tags = _normalize_tags(tags)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO checkpoints (id, name, tags, created_at) VALUES (?, ?, ?, ?)",
            (checkpoint_id, name, json.dumps(tags), timestamp)
        )

        # Auto-cleanup: delete checkpoints older than retention period, except the most recent
        retention_cutoff = timestamp - (self.checkpoint_retention_days * 86400)
        cursor.execute("""
            DELETE FROM checkpoints WHERE created_at < ? AND id != (
                SELECT id FROM checkpoints ORDER BY created_at DESC LIMIT 1
            )
        """, (retention_cutoff,))

        conn.commit()
        conn.close()
        return checkpoint_id

    def rollback_to_checkpoint(self, checkpoint_id: str) -> bool:
        """Atomically rollback to a checkpoint: delete new memories, restore updated ones."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Get checkpoint timestamp
        cursor.execute("SELECT created_at FROM checkpoints WHERE id = ?", (checkpoint_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return False
        checkpoint_time = row[0]

        # Step 1: Delete memories created after the checkpoint
        cursor.execute("SELECT id FROM memories WHERE created_at > ?", (checkpoint_time,))
        deleted_ids = [r[0] for r in cursor.fetchall()]
        if deleted_ids:
            placeholders = ",".join("?" for _ in deleted_ids)
            cursor.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", deleted_ids)
            # Clean up any version entries for deleted memories
            cursor.execute(
                f"DELETE FROM memory_versions WHERE memory_id IN ({placeholders})", deleted_ids
            )

        # Step 2: Restore memories that were updated after the checkpoint
        # Find the oldest version snapshot after checkpoint time for each memory
        # (this represents the state just before the first post-checkpoint update)
        cursor.execute("""
            SELECT mv.memory_id, mv.version_id, mv.content, mv.tags, mv.metadata
            FROM memory_versions mv
            INNER JOIN (
                SELECT memory_id, MIN(version_id) as min_version_id
                FROM memory_versions
                WHERE created_at > ?
                GROUP BY memory_id
            ) oldest ON mv.memory_id = oldest.memory_id AND mv.version_id = oldest.min_version_id
        """, (checkpoint_time,))

        restored_ids = []
        for mem_id, version_id, content, tags_str, metadata_str in cursor.fetchall():
            # Skip if this memory was already deleted (created after checkpoint)
            if mem_id in deleted_ids:  # pragma: no cover
                continue
            old_metadata = json.loads(metadata_str)
            old_metadata["updated_at"] = time.time()
            cursor.execute(
                "UPDATE memories SET content = ?, tags = ?, metadata = ?, updated_at = ? WHERE id = ?",
                (content, tags_str, json.dumps(old_metadata), old_metadata["updated_at"], mem_id)
            )
            restored_ids.append(mem_id)

        # Step 3: Delete all version entries created after checkpoint
        cursor.execute("DELETE FROM memory_versions WHERE created_at > ?", (checkpoint_time,))

        # Step 4: Delete the checkpoint and any created after it
        cursor.execute("DELETE FROM checkpoints WHERE created_at >= ?", (checkpoint_time,))

        conn.commit()
        conn.close()

        # Update vector store for restored memories
        if self.vector_store:
            for mem_id in restored_ids:
                try:
                    # Re-read from DB to get the restored state
                    c = sqlite3.connect(self.db_path)
                    cur = c.cursor()
                    cur.execute("SELECT content, metadata FROM memories WHERE id = ?", (mem_id,))
                    r = cur.fetchone()
                    c.close()
                    if r:
                        self.vector_store.update_text(mem_id, r[0], json.loads(r[1]))
                except Exception:
                    pass

        return True

    def list_checkpoints(self) -> List[Dict[str, Any]]:
        """List all checkpoints, newest first."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, tags, created_at FROM checkpoints ORDER BY created_at DESC")
        results = []
        for row in cursor.fetchall():
            results.append({
                "id": row["id"],
                "name": row["name"],
                "tags": json.loads(row["tags"]),
                "created_at": row["created_at"],
            })
        conn.close()
        return results

    def purge_expired(self) -> int:
        """Delete all expired memories. Returns count of deleted memories."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        now = time.time()

        # Get IDs of expired memories for cleanup
        cursor.execute(
            "SELECT id FROM memories WHERE expires_at IS NOT NULL AND expires_at < ?", (now,)
        )
        expired_ids = [r[0] for r in cursor.fetchall()]

        if not expired_ids:
            conn.close()
            return 0

        placeholders = ",".join("?" for _ in expired_ids)
        cursor.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", expired_ids)
        cursor.execute(f"DELETE FROM memory_versions WHERE memory_id IN ({placeholders})", expired_ids)

        conn.commit()
        conn.close()
        return len(expired_ids)

    def consolidate_memories(self, tags: List[str], summarizer_fn,
                             older_than_days: int = 30,
                             min_count: int = 5) -> List[str]:
        """Consolidate old memories matching tags into LLM-generated summaries.

        Args:
            tags: Filter to memories matching ALL of these tags.
            summarizer_fn: Callable(str) -> str that takes formatted memories text
                          and returns a summary string.
            older_than_days: Only consolidate memories older than this many days.
            min_count: Skip consolidation if fewer than this many memories match.

        Returns:
            List of new summary memory IDs, or empty list if skipped.
        """
        tags = _normalize_tags(tags)
        cutoff = time.time() - (older_than_days * 86400)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Find matching memories older than cutoff
        cursor.execute(
            "SELECT id, content, tags, metadata FROM memories WHERE created_at < ? ORDER BY created_at ASC",
            (cutoff,)
        )
        matching = []
        for row in cursor.fetchall():
            row_tags = json.loads(row["tags"])
            if all(t in row_tags for t in tags):
                matching.append({
                    "id": row["id"],
                    "content": row["content"],
                    "tags": row_tags,
                    "metadata": json.loads(row["metadata"]),
                })

        if len(matching) < min_count:
            conn.close()
            return []

        # Format memories for the summarizer
        memory_texts = [f"Memory {i+1} (ID: {m['id']}): {m['content']}"
                        for i, m in enumerate(matching)]
        formatted = "\n".join(memory_texts)

        # Call the summarizer
        summary_text = summarizer_fn(formatted)

        conn.close()

        # Store the summary as a new memory
        original_ids = [m["id"] for m in matching]
        summary_tags = list(set(tags + ["consolidated"]))
        summary_metadata = {
            "consolidated_from": original_ids,
            "consolidated_count": len(original_ids),
        }
        summary_id = self.store_memory(summary_text, summary_metadata, tags=summary_tags)

        # Delete the originals and their version history
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        placeholders = ",".join("?" for _ in original_ids)
        cursor.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", original_ids)
        cursor.execute(f"DELETE FROM memory_versions WHERE memory_id IN ({placeholders})", original_ids)
        conn.commit()
        conn.close()

        return [summary_id]

    def rollback_memory(self, memory_id: str) -> bool:
        """Rollback a memory to its previous version."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Check memory exists
        cursor.execute("SELECT 1 FROM memories WHERE id = ?", (memory_id,))
        if not cursor.fetchone():
            conn.close()
            return False

        # Get most recent version
        cursor.execute(
            "SELECT version_id, content, tags, metadata FROM memory_versions WHERE memory_id = ? ORDER BY version_id DESC LIMIT 1",
            (memory_id,)
        )
        version = cursor.fetchone()
        if not version:
            conn.close()
            return False

        version_id, old_content, old_tags_str, old_metadata_str = version
        old_metadata = json.loads(old_metadata_str)
        old_metadata["updated_at"] = time.time()

        # Restore the old version
        cursor.execute(
            "UPDATE memories SET content = ?, tags = ?, metadata = ?, updated_at = ? WHERE id = ?",
            (old_content, old_tags_str, json.dumps(old_metadata), old_metadata["updated_at"], memory_id)
        )

        # Remove the consumed version entry
        cursor.execute("DELETE FROM memory_versions WHERE version_id = ?", (version_id,))

        conn.commit()
        conn.close()

        # Update vector store if available
        if self.vector_store:
            try:
                self.vector_store.update_text(memory_id, old_content, old_metadata)
            except Exception as e:
                pass

        return True

    def export_memories(self) -> Dict[str, Any]:
        """Export all memories, versions, and checkpoints as a portable dict."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT id, content, tags, metadata, created_at, updated_at, expires_at FROM memories")
        memories = []
        for row in cursor.fetchall():
            memories.append({
                "id": row["id"],
                "content": row["content"],
                "tags": json.loads(row["tags"]),
                "metadata": json.loads(row["metadata"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "expires_at": row["expires_at"],
            })

        cursor.execute("SELECT version_id, memory_id, content, tags, metadata, created_at FROM memory_versions")
        versions = []
        for row in cursor.fetchall():
            versions.append({
                "version_id": row["version_id"],
                "memory_id": row["memory_id"],
                "content": row["content"],
                "tags": json.loads(row["tags"]),
                "metadata": json.loads(row["metadata"]),
                "created_at": row["created_at"],
            })

        cursor.execute("SELECT id, name, tags, created_at FROM checkpoints")
        checkpoints = []
        for row in cursor.fetchall():
            checkpoints.append({
                "id": row["id"],
                "name": row["name"],
                "tags": json.loads(row["tags"]),
                "created_at": row["created_at"],
            })

        conn.close()

        return {
            "version": 1,
            "exported_at": time.time(),
            "source_backend": "sqlite",
            "memories": memories,
            "memory_versions": versions,
            "checkpoints": checkpoints,
        }

    def import_memories(self, data: Dict[str, Any]) -> Dict[str, int]:
        """Import memories from an export dict. Skips duplicates by ID."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        counts = {"memories": 0, "memory_versions": 0, "checkpoints": 0}

        for mem in data.get("memories", []):
            cursor.execute("SELECT 1 FROM memories WHERE id = ?", (mem["id"],))
            if cursor.fetchone():
                continue
            tags = json.dumps(mem.get("tags", []))
            metadata = json.dumps(mem.get("metadata", {}))
            cursor.execute(
                "INSERT INTO memories (id, content, tags, metadata, created_at, updated_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (mem["id"], mem["content"], tags, metadata,
                 mem["created_at"], mem["updated_at"], mem.get("expires_at"))
            )
            counts["memories"] += 1

            # Regenerate embeddings if vector store available
            if self.vector_store:
                try:
                    self.vector_store.add_text(mem["id"], mem["content"], mem.get("metadata", {}))
                except Exception:
                    pass

        for ver in data.get("memory_versions", []):
            cursor.execute("SELECT 1 FROM memory_versions WHERE version_id = ?", (ver["version_id"],))
            if cursor.fetchone():
                continue
            tags = json.dumps(ver.get("tags", []))
            metadata = json.dumps(ver.get("metadata", {}))
            cursor.execute(
                "INSERT INTO memory_versions (version_id, memory_id, content, tags, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (ver["version_id"], ver["memory_id"], ver["content"], tags, metadata, ver["created_at"])
            )
            counts["memory_versions"] += 1

        for chk in data.get("checkpoints", []):
            cursor.execute("SELECT 1 FROM checkpoints WHERE id = ?", (chk["id"],))
            if cursor.fetchone():
                continue
            tags = json.dumps(chk.get("tags", []))
            cursor.execute(
                "INSERT INTO checkpoints (id, name, tags, created_at) VALUES (?, ?, ?, ?)",
                (chk["id"], chk["name"], tags, chk["created_at"])
            )
            counts["checkpoints"] += 1

        conn.commit()
        conn.close()
        return counts
