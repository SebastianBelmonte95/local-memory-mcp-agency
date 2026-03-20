import os
import time
import json
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from psycopg2 import sql
import numpy as np
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class PostgresMemoryAPI:
    def __init__(self, ollama_embeddings=None):
        """Initialize PostgreSQL memory store with connection parameters."""
        self.connection_params = {
            'host': os.getenv('POSTGRES_HOST', 'localhost'),
            'port': os.getenv('POSTGRES_PORT', 5432),
            'database': os.getenv('POSTGRES_DB', 'postgres'),
            'user': os.getenv('POSTGRES_USER', 'postgres'),
            'password': os.getenv('POSTGRES_PASSWORD', 'postgres'),
        }
        self.default_domain = os.getenv('DEFAULT_MEMORY_DOMAIN', 'default')
        self.ollama_embeddings = ollama_embeddings

    def _get_connection(self):
        """Get a new database connection."""
        return psycopg2.connect(**self.connection_params)

    def _ensure_table_exists(self, domain: str):
        """Ensure the domain table exists."""
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT create_domain_memories_table(%s)", (domain,))
                conn.commit()

    def store_memory(self, content: str, metadata: Dict[str, Any] = None,
                     domain: str = None, tags: List[str] = None) -> str:
        """Store a new memory in the specified domain."""
        domain = domain or self.default_domain
        self._ensure_table_exists(domain)

        memory_id = f"mem_{int(time.time() * 1000)}"
        timestamp = time.time()
        tags = tags or []

        metadata = metadata or {}
        metadata.update({
            "created_at": timestamp,
            "updated_at": timestamp,
        })

        # Get embedding if available
        embedding = None
        if self.ollama_embeddings:
            try:
                embedding = self.ollama_embeddings.get_embedding(content)
            except Exception as e:
                print(f"Failed to generate embedding: {e}")

        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                table_name = sql.Identifier(f"{domain}_memories")

                if embedding:
                    query = sql.SQL("""
                        INSERT INTO {} (id, content, embedding, tags, metadata)
                        VALUES (%s, %s, %s::vector, %s, %s)
                    """).format(table_name)
                    cursor.execute(query, (memory_id, content, embedding, tags, Json(metadata)))
                else:
                    query = sql.SQL("""
                        INSERT INTO {} (id, content, tags, metadata)
                        VALUES (%s, %s, %s, %s)
                    """).format(table_name)
                    cursor.execute(query, (memory_id, content, tags, Json(metadata)))

                conn.commit()

        return memory_id

    def retrieve_memories(self, query: str, limit: int = 5, domain: str = None,
                          tags: List[str] = None) -> List[Dict[str, Any]]:
        """Retrieve memories using vector similarity search with text search fallback.
        When tags are provided, filter to memories containing ALL specified tags."""
        domain = domain or self.default_domain
        self._ensure_table_exists(domain)

        tag_clause = sql.SQL("")
        tag_params = []
        if tags:
            # tags @> ARRAY[...] means the row's tags contain all specified tags
            tag_clause = sql.SQL(" AND tags @> %s")
            tag_params = [tags]

        # Try vector search first if embeddings are available
        if self.ollama_embeddings:
            try:
                query_embedding = self.ollama_embeddings.get_embedding(query)

                with self._get_connection() as conn:
                    with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                        table_name = sql.Identifier(f"{domain}_memories")

                        search_query = sql.SQL("""
                            SELECT id, content, tags, metadata,
                                   1 - (embedding <=> %s::vector) AS score
                            FROM {}
                            WHERE embedding IS NOT NULL{}
                            ORDER BY embedding <=> %s::vector
                            LIMIT %s
                        """).format(table_name, tag_clause)

                        params = [query_embedding] + tag_params + [query_embedding, limit]
                        cursor.execute(search_query, params)
                        results = cursor.fetchall()

                        if results:
                            return [dict(row) for row in results]

            except Exception as e:
                print(f"Vector search failed: {e}")

        # Fallback to text search
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                table_name = sql.Identifier(f"{domain}_memories")

                # If tags-only recall (no meaningful query text), just filter by tags
                if tags and not query:
                    tags_query = sql.SQL("""
                        SELECT id, content, tags, metadata, 0.0 as score
                        FROM {}
                        WHERE tags @> %s
                        ORDER BY updated_at DESC
                        LIMIT %s
                    """).format(table_name)
                    cursor.execute(tags_query, (tags, limit))
                    results = cursor.fetchall()
                    if results:
                        return [dict(row) for row in results]

                # Full text search
                search_query = sql.SQL("""
                    SELECT id, content, tags, metadata, 0.0 as score
                    FROM {}
                    WHERE to_tsvector('english', content) @@ plainto_tsquery('english', %s){}
                    ORDER BY updated_at DESC
                    LIMIT %s
                """).format(table_name, tag_clause)

                cursor.execute(search_query, [query] + tag_params + [limit])
                results = cursor.fetchall()

                if results:
                    return [dict(row) for row in results]

                # If no results from full text search, try simple LIKE
                like_query = sql.SQL("""
                    SELECT id, content, tags, metadata, 0.0 as score
                    FROM {}
                    WHERE content ILIKE %s{}
                    ORDER BY updated_at DESC
                    LIMIT %s
                """).format(table_name, tag_clause)

                cursor.execute(like_query, [f"%{query}%"] + tag_params + [limit])
                results = cursor.fetchall()

                if results:
                    return [dict(row) for row in results]

                # Last resort: return most recent memories (with tag filter if specified)
                fallback_query = sql.SQL("""
                    SELECT id, content, tags, metadata, 0.0 as score
                    FROM {}
                    WHERE true{}
                    ORDER BY updated_at DESC
                    LIMIT %s
                """).format(table_name, tag_clause)

                cursor.execute(fallback_query, tag_params + [limit])
                results = cursor.fetchall()

                return [dict(row) for row in results]

    def update_memory(self, memory_id: str, content: str = None,
                      metadata: Dict[str, Any] = None, domain: str = None,
                      tags: List[str] = None) -> bool:
        """Update an existing memory. Snapshots the old version for rollback."""
        domain = domain or self.default_domain

        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                table_name = sql.Identifier(f"{domain}_memories")
                versions_table = sql.Identifier(f"{domain}_memory_versions")

                # First check if memory exists and get current state for version snapshot
                check_query = sql.SQL(
                    "SELECT content, tags, metadata, embedding FROM {} WHERE id = %s"
                ).format(table_name)
                cursor.execute(check_query, (memory_id,))
                current = cursor.fetchone()

                if not current:
                    return False

                current_content, current_tags, current_metadata_json, current_embedding = current

                # Snapshot current state to version history
                if current_embedding:
                    snapshot_query = sql.SQL("""
                        INSERT INTO {} (memory_id, content, tags, metadata, embedding)
                        VALUES (%s, %s, %s, %s, %s)
                    """).format(versions_table)
                    cursor.execute(snapshot_query, (
                        memory_id, current_content, current_tags,
                        current_metadata_json, current_embedding
                    ))
                else:
                    snapshot_query = sql.SQL("""
                        INSERT INTO {} (memory_id, content, tags, metadata)
                        VALUES (%s, %s, %s, %s)
                    """).format(versions_table)
                    cursor.execute(snapshot_query, (
                        memory_id, current_content, current_tags, current_metadata_json
                    ))

                # Update metadata timestamp
                if metadata is not None:
                    metadata["updated_at"] = time.time()

                # Build update based on what changed
                if content is not None and metadata is not None:
                    embedding = None
                    if self.ollama_embeddings:
                        try:
                            embedding = self.ollama_embeddings.get_embedding(content)
                        except Exception as e:
                            print(f"Failed to generate embedding: {e}")

                    if tags is not None:
                        if embedding:
                            update_query = sql.SQL("""
                                UPDATE {} SET content = %s, embedding = %s::vector,
                                tags = %s, metadata = %s, updated_at = NOW() WHERE id = %s
                            """).format(table_name)
                            cursor.execute(update_query, (content, embedding, tags, Json(metadata), memory_id))
                        else:
                            update_query = sql.SQL("""
                                UPDATE {} SET content = %s, tags = %s, metadata = %s,
                                updated_at = NOW() WHERE id = %s
                            """).format(table_name)
                            cursor.execute(update_query, (content, tags, Json(metadata), memory_id))
                    else:
                        if embedding:
                            update_query = sql.SQL("""
                                UPDATE {} SET content = %s, embedding = %s::vector,
                                metadata = %s, updated_at = NOW() WHERE id = %s
                            """).format(table_name)
                            cursor.execute(update_query, (content, embedding, Json(metadata), memory_id))
                        else:
                            update_query = sql.SQL("""
                                UPDATE {} SET content = %s, metadata = %s,
                                updated_at = NOW() WHERE id = %s
                            """).format(table_name)
                            cursor.execute(update_query, (content, Json(metadata), memory_id))

                elif content is not None:
                    embedding = None
                    if self.ollama_embeddings:
                        try:
                            embedding = self.ollama_embeddings.get_embedding(content)
                        except Exception as e:
                            print(f"Failed to generate embedding: {e}")

                    if embedding:
                        update_query = sql.SQL("""
                            UPDATE {} SET content = %s, embedding = %s::vector,
                            updated_at = NOW() WHERE id = %s
                        """).format(table_name)
                        cursor.execute(update_query, (content, embedding, memory_id))
                    else:
                        update_query = sql.SQL("""
                            UPDATE {} SET content = %s, updated_at = NOW() WHERE id = %s
                        """).format(table_name)
                        cursor.execute(update_query, (content, memory_id))

                elif metadata is not None:
                    if tags is not None:
                        update_query = sql.SQL("""
                            UPDATE {} SET metadata = metadata || %s, tags = %s,
                            updated_at = NOW() WHERE id = %s
                        """).format(table_name)
                        cursor.execute(update_query, (Json(metadata), tags, memory_id))
                    else:
                        update_query = sql.SQL("""
                            UPDATE {} SET metadata = metadata || %s,
                            updated_at = NOW() WHERE id = %s
                        """).format(table_name)
                        cursor.execute(update_query, (Json(metadata), memory_id))

                elif tags is not None:
                    update_query = sql.SQL("""
                        UPDATE {} SET tags = %s, updated_at = NOW() WHERE id = %s
                    """).format(table_name)
                    cursor.execute(update_query, (tags, memory_id))

                conn.commit()
                return True

    def rollback_memory(self, memory_id: str, domain: str = None) -> bool:
        """Rollback a memory to its previous version."""
        domain = domain or self.default_domain

        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                table_name = sql.Identifier(f"{domain}_memories")
                versions_table = sql.Identifier(f"{domain}_memory_versions")

                # Check memory exists
                check_query = sql.SQL("SELECT 1 FROM {} WHERE id = %s").format(table_name)
                cursor.execute(check_query, (memory_id,))
                if not cursor.fetchone():
                    return False

                # Get most recent version
                version_query = sql.SQL("""
                    SELECT version_id, content, tags, metadata, embedding
                    FROM {} WHERE memory_id = %s
                    ORDER BY version_id DESC LIMIT 1
                """).format(versions_table)
                cursor.execute(version_query, (memory_id,))
                version = cursor.fetchone()

                if not version:
                    return False

                version_id, old_content, old_tags, old_metadata, old_embedding = version

                # Restore the old version
                if old_embedding:
                    restore_query = sql.SQL("""
                        UPDATE {} SET content = %s, embedding = %s,
                        tags = %s, metadata = %s, updated_at = NOW() WHERE id = %s
                    """).format(table_name)
                    cursor.execute(restore_query, (old_content, old_embedding, old_tags, old_metadata, memory_id))
                else:
                    restore_query = sql.SQL("""
                        UPDATE {} SET content = %s, tags = %s, metadata = %s,
                        updated_at = NOW() WHERE id = %s
                    """).format(table_name)
                    cursor.execute(restore_query, (old_content, old_tags, old_metadata, memory_id))

                # Remove the consumed version entry
                delete_query = sql.SQL(
                    "DELETE FROM {} WHERE version_id = %s"
                ).format(versions_table)
                cursor.execute(delete_query, (version_id,))

                conn.commit()
                return True

    def list_domains(self) -> List[str]:
        """List all available memory domains."""
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_name LIKE '%_memories'
                    AND table_name NOT LIKE '%_memory_versions'
                    ORDER BY table_name
                """)

                domains = []
                for row in cursor.fetchall():
                    table_name = row[0]
                    if table_name.endswith('_memories'):
                        domain = table_name[:-9]
                        domains.append(domain)

                return domains
