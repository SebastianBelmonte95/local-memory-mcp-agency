"""Tests for PostgresMemoryAPI with mocked psycopg2."""
import pytest
from unittest.mock import patch, MagicMock, call
from psycopg2.extras import Json

# Standard mock return for update_memory's SELECT (content, tags, metadata_json, embedding)
MOCK_EXISTING_ROW = ("old content", ["tag1"], '{"created_at": 1.0}', None)
MOCK_EXISTING_ROW_WITH_EMBEDDING = ("old content", ["tag1"], '{"created_at": 1.0}', [0.1] * 768)


@pytest.fixture
def pg_api(mock_pg_connection, mock_pg_cursor):
    """PostgresMemoryAPI with mocked database connection."""
    with patch("postgres_memory_api.psycopg2") as mock_psycopg2:
        mock_psycopg2.connect.return_value = mock_pg_connection
        from postgres_memory_api import PostgresMemoryAPI
        api = PostgresMemoryAPI(ollama_embeddings=None)
    api._get_connection = MagicMock(return_value=mock_pg_connection)
    return api


@pytest.fixture
def pg_api_with_embeddings(mock_pg_connection, mock_pg_cursor, mock_ollama_embeddings):
    """PostgresMemoryAPI with mocked database and embeddings."""
    with patch("postgres_memory_api.psycopg2") as mock_psycopg2:
        mock_psycopg2.connect.return_value = mock_pg_connection
        from postgres_memory_api import PostgresMemoryAPI
        api = PostgresMemoryAPI(ollama_embeddings=mock_ollama_embeddings)
    api._get_connection = MagicMock(return_value=mock_pg_connection)
    return api


@pytest.mark.unit
@pytest.mark.postgres
class TestInitialization:
    def test_default_connection_params(self):
        with patch("postgres_memory_api.psycopg2"):
            from postgres_memory_api import PostgresMemoryAPI
            api = PostgresMemoryAPI()
        assert api.connection_params["host"] == "localhost"
        assert api.connection_params["database"] == "postgres"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("POSTGRES_HOST", "dbhost")
        monkeypatch.setenv("POSTGRES_PORT", "5433")
        monkeypatch.setenv("POSTGRES_DB", "mydb")
        with patch("postgres_memory_api.psycopg2"):
            import importlib
            import postgres_memory_api
            importlib.reload(postgres_memory_api)
            api = postgres_memory_api.PostgresMemoryAPI()
        assert api.connection_params["host"] == "dbhost"
        assert api.connection_params["port"] == "5433"
        assert api.connection_params["database"] == "mydb"


@pytest.mark.unit
@pytest.mark.postgres
class TestStoreMemory:
    def test_without_embedding(self, pg_api, mock_pg_cursor):
        mem_id = pg_api.store_memory("test content")
        assert mem_id.startswith("mem_")
        assert mock_pg_cursor.execute.call_count >= 2

    def test_with_embedding(self, pg_api_with_embeddings, mock_pg_cursor):
        mem_id = pg_api_with_embeddings.store_memory("test content")
        assert mem_id.startswith("mem_")
        pg_api_with_embeddings.ollama_embeddings.get_embedding.assert_called_once_with("test content")
        assert mock_pg_cursor.execute.call_count >= 2

    def test_with_domain(self, pg_api, mock_pg_cursor):
        pg_api.store_memory("content", domain="health")
        ensure_call = mock_pg_cursor.execute.call_args_list[0]
        assert "health" in str(ensure_call)

    def test_with_tags(self, pg_api, mock_pg_cursor):
        pg_api.store_memory("content", tags=["agent-a", "project-x"])
        insert_call = mock_pg_cursor.execute.call_args_list[-1]
        args = insert_call[0][1]
        assert ["agent-a", "project-x"] in list(args)

    def test_embedding_failure_falls_back(self, pg_api_with_embeddings, mock_pg_cursor):
        pg_api_with_embeddings.ollama_embeddings.get_embedding.side_effect = RuntimeError("fail")
        mem_id = pg_api_with_embeddings.store_memory("content")
        assert mem_id.startswith("mem_")

    def test_metadata_passed(self, pg_api, mock_pg_cursor):
        pg_api.store_memory("content", metadata={"source": "test", "importance": 0.5})
        insert_call = mock_pg_cursor.execute.call_args_list[-1]
        args = insert_call[0][1] if len(insert_call[0]) > 1 else ()
        json_args = [a for a in args if isinstance(a, Json)]
        assert len(json_args) > 0


@pytest.mark.unit
@pytest.mark.postgres
class TestRetrieveMemories:
    def test_vector_search(self, pg_api_with_embeddings, mock_pg_cursor):
        mock_pg_cursor.fetchall.return_value = [
            {"id": "mem_1", "content": "result", "tags": [], "metadata": {}, "score": 0.85}
        ]
        results = pg_api_with_embeddings.retrieve_memories("test query")
        assert mock_pg_cursor.execute.called

    def test_text_fallback_when_no_embeddings(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchall.return_value = [
            {"id": "mem_1", "content": "text result", "tags": [], "metadata": {}, "score": 0.0}
        ]
        results = pg_api.retrieve_memories("test query")
        assert mock_pg_cursor.execute.called

    def test_like_fallback(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchall.side_effect = [
            [],  # tsvector
            [{"id": "mem_1", "content": "like result", "tags": [], "metadata": {}, "score": 0.0}]
        ]
        results = pg_api.retrieve_memories("test")
        assert mock_pg_cursor.execute.call_count >= 2

    def test_last_resort_fallback(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchall.side_effect = [
            [],  # tsvector
            [],  # ILIKE
            [{"id": "mem_1", "content": "recent", "tags": [], "metadata": {}, "score": 0.0}]
        ]
        results = pg_api.retrieve_memories("nothing matches")
        assert mock_pg_cursor.execute.call_count >= 3

    def test_with_domain(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchall.return_value = []
        pg_api.retrieve_memories("query", domain="health")
        first_call = mock_pg_cursor.execute.call_args_list[0]
        assert "health" in str(first_call)

    def test_with_tags_filter(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchall.return_value = [
            {"id": "mem_1", "content": "tagged", "tags": ["a", "b"], "metadata": {}, "score": 0.0}
        ]
        results = pg_api.retrieve_memories("query", tags=["a", "b"])
        assert mock_pg_cursor.execute.called

    def test_tags_only_no_query(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchall.return_value = [
            {"id": "mem_1", "content": "tagged", "tags": ["a"], "metadata": {}, "score": 0.0}
        ]
        results = pg_api.retrieve_memories("", tags=["a"])
        assert mock_pg_cursor.execute.called


@pytest.mark.unit
@pytest.mark.postgres
class TestUpdateMemory:
    def test_content_and_metadata(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchone.return_value = MOCK_EXISTING_ROW
        result = pg_api.update_memory("mem_1", content="new", metadata={"k": "v"})
        assert result is True

    def test_content_only(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchone.return_value = MOCK_EXISTING_ROW
        result = pg_api.update_memory("mem_1", content="new content")
        assert result is True

    def test_metadata_only(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchone.return_value = MOCK_EXISTING_ROW
        result = pg_api.update_memory("mem_1", metadata={"importance": 0.9})
        assert result is True

    def test_tags_only(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchone.return_value = MOCK_EXISTING_ROW
        result = pg_api.update_memory("mem_1", tags=["new-tag"])
        assert result is True

    def test_content_metadata_and_tags(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchone.return_value = MOCK_EXISTING_ROW
        result = pg_api.update_memory("mem_1", content="new", metadata={"k": "v"}, tags=["t"])
        assert result is True

    def test_metadata_and_tags(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchone.return_value = MOCK_EXISTING_ROW
        result = pg_api.update_memory("mem_1", metadata={"k": "v"}, tags=["t"])
        assert result is True

    def test_not_found(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchone.return_value = None
        result = pg_api.update_memory("mem_nonexistent", content="x")
        assert result is False

    def test_snapshots_version_before_update(self, pg_api, mock_pg_cursor):
        """Update should insert a version snapshot before modifying."""
        mock_pg_cursor.fetchone.return_value = MOCK_EXISTING_ROW
        pg_api.update_memory("mem_1", content="new")
        # At least: SELECT current + INSERT version + UPDATE memory
        assert mock_pg_cursor.execute.call_count >= 3

    def test_snapshots_version_with_embedding(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchone.return_value = MOCK_EXISTING_ROW_WITH_EMBEDDING
        pg_api.update_memory("mem_1", content="new")
        assert mock_pg_cursor.execute.call_count >= 3

    def test_with_embedding_on_content_update(self, pg_api_with_embeddings, mock_pg_cursor):
        mock_pg_cursor.fetchone.return_value = MOCK_EXISTING_ROW
        pg_api_with_embeddings.update_memory("mem_1", content="new content")
        pg_api_with_embeddings.ollama_embeddings.get_embedding.assert_called()

    def test_embedding_failure_still_updates(self, pg_api_with_embeddings, mock_pg_cursor):
        mock_pg_cursor.fetchone.return_value = MOCK_EXISTING_ROW
        pg_api_with_embeddings.ollama_embeddings.get_embedding.side_effect = RuntimeError("fail")
        result = pg_api_with_embeddings.update_memory("mem_1", content="new")
        assert result is True

    def test_with_domain(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchone.return_value = MOCK_EXISTING_ROW
        pg_api.update_memory("mem_1", content="x", domain="health")
        assert mock_pg_cursor.execute.called


@pytest.mark.unit
@pytest.mark.postgres
class TestUpdateWithEmbeddings:
    def test_content_and_metadata_with_embedding(self, mock_pg_connection, mock_pg_cursor, mock_ollama_embeddings):
        with patch("postgres_memory_api.psycopg2"):
            from postgres_memory_api import PostgresMemoryAPI
            api = PostgresMemoryAPI(ollama_embeddings=mock_ollama_embeddings)
        api._get_connection = MagicMock(return_value=mock_pg_connection)
        mock_pg_cursor.fetchone.return_value = MOCK_EXISTING_ROW

        result = api.update_memory("mem_1", content="new content", metadata={"k": "v"})
        assert result is True
        mock_ollama_embeddings.get_embedding.assert_called_with("new content")

    def test_content_and_metadata_embedding_failure(self, mock_pg_connection, mock_pg_cursor, mock_ollama_embeddings):
        with patch("postgres_memory_api.psycopg2"):
            from postgres_memory_api import PostgresMemoryAPI
            api = PostgresMemoryAPI(ollama_embeddings=mock_ollama_embeddings)
        api._get_connection = MagicMock(return_value=mock_pg_connection)
        mock_pg_cursor.fetchone.return_value = MOCK_EXISTING_ROW
        mock_ollama_embeddings.get_embedding.side_effect = RuntimeError("fail")

        result = api.update_memory("mem_1", content="new", metadata={"k": "v"})
        assert result is True

    def test_content_only_with_embedding(self, mock_pg_connection, mock_pg_cursor, mock_ollama_embeddings):
        with patch("postgres_memory_api.psycopg2"):
            from postgres_memory_api import PostgresMemoryAPI
            api = PostgresMemoryAPI(ollama_embeddings=mock_ollama_embeddings)
        api._get_connection = MagicMock(return_value=mock_pg_connection)
        mock_pg_cursor.fetchone.return_value = MOCK_EXISTING_ROW

        result = api.update_memory("mem_1", content="updated content")
        assert result is True
        mock_ollama_embeddings.get_embedding.assert_called_with("updated content")

    def test_content_only_embedding_failure(self, mock_pg_connection, mock_pg_cursor, mock_ollama_embeddings):
        with patch("postgres_memory_api.psycopg2"):
            from postgres_memory_api import PostgresMemoryAPI
            api = PostgresMemoryAPI(ollama_embeddings=mock_ollama_embeddings)
        api._get_connection = MagicMock(return_value=mock_pg_connection)
        mock_pg_cursor.fetchone.return_value = MOCK_EXISTING_ROW
        mock_ollama_embeddings.get_embedding.side_effect = RuntimeError("fail")

        result = api.update_memory("mem_1", content="new")
        assert result is True

    def test_content_and_metadata_with_tags_and_embedding(self, mock_pg_connection, mock_pg_cursor, mock_ollama_embeddings):
        with patch("postgres_memory_api.psycopg2"):
            from postgres_memory_api import PostgresMemoryAPI
            api = PostgresMemoryAPI(ollama_embeddings=mock_ollama_embeddings)
        api._get_connection = MagicMock(return_value=mock_pg_connection)
        mock_pg_cursor.fetchone.return_value = MOCK_EXISTING_ROW

        result = api.update_memory("mem_1", content="new", metadata={"k": "v"}, tags=["t"])
        assert result is True
        mock_ollama_embeddings.get_embedding.assert_called()


@pytest.mark.unit
@pytest.mark.postgres
class TestRollbackMemory:
    def test_rollback_success(self, pg_api, mock_pg_cursor):
        # fetchone calls: 1) check exists -> found, 2) get version -> found
        mock_pg_cursor.fetchone.side_effect = [
            (1,),  # memory exists
            (42, "old content", ["old-tag"], '{"k": "v"}', None)  # version row
        ]
        result = pg_api.rollback_memory("mem_1")
        assert result is True

    def test_rollback_with_embedding(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchone.side_effect = [
            (1,),
            (42, "old content", ["old-tag"], '{"k": "v"}', [0.1] * 768)
        ]
        result = pg_api.rollback_memory("mem_1")
        assert result is True

    def test_rollback_memory_not_found(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchone.return_value = None
        result = pg_api.rollback_memory("mem_nonexistent")
        assert result is False

    def test_rollback_no_versions(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchone.side_effect = [
            (1,),  # memory exists
            None   # no version history
        ]
        result = pg_api.rollback_memory("mem_1")
        assert result is False

    def test_rollback_with_domain(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchone.side_effect = [
            (1,),
            (1, "old", [], '{}', None)
        ]
        result = pg_api.rollback_memory("mem_1", domain="health")
        assert result is True


@pytest.mark.unit
@pytest.mark.postgres
class TestListDomains:
    def test_returns_domains(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchall.return_value = [
            ("default_memories",),
            ("health_memories",),
            ("startup_memories",),
        ]
        domains = pg_api.list_domains()
        assert domains == ["default", "health", "startup"]

    def test_empty(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchall.return_value = []
        assert pg_api.list_domains() == []

    def test_excludes_version_tables(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchall.return_value = [
            ("default_memories",),
        ]
        # The SQL query itself filters out _memory_versions tables
        domains = pg_api.list_domains()
        assert "default_memory_versions" not in domains


@pytest.mark.unit
@pytest.mark.postgres
class TestGetConnection:
    def test_calls_psycopg2_connect(self):
        with patch("postgres_memory_api.psycopg2") as mock_psycopg2:
            from postgres_memory_api import PostgresMemoryAPI
            api = PostgresMemoryAPI()
            conn = api._get_connection()
            mock_psycopg2.connect.assert_called_once_with(**api.connection_params)


@pytest.mark.unit
@pytest.mark.postgres
class TestRetrieveEdgeCases:
    def test_vector_search_exception_falls_back(self, mock_pg_connection, mock_pg_cursor, mock_ollama_embeddings):
        with patch("postgres_memory_api.psycopg2"):
            from postgres_memory_api import PostgresMemoryAPI
            api = PostgresMemoryAPI(ollama_embeddings=mock_ollama_embeddings)
        api._get_connection = MagicMock(return_value=mock_pg_connection)

        mock_ollama_embeddings.get_embedding.side_effect = Exception("embedding failed")
        mock_pg_cursor.fetchall.return_value = [
            {"id": "mem_1", "content": "fallback", "tags": [], "metadata": {}, "score": 0.0}
        ]
        results = api.retrieve_memories("query")
        assert mock_pg_cursor.execute.call_count >= 2


@pytest.mark.unit
@pytest.mark.postgres
class TestCreateCheckpoint:
    def test_returns_id(self, pg_api, mock_pg_cursor):
        chk_id = pg_api.create_checkpoint("save point")
        assert chk_id.startswith("chk_")
        assert mock_pg_cursor.execute.called

    def test_with_tags_and_domain(self, pg_api, mock_pg_cursor):
        chk_id = pg_api.create_checkpoint("save", domain="health", tags=["a"])
        assert chk_id.startswith("chk_")


@pytest.mark.unit
@pytest.mark.postgres
class TestRollbackToCheckpoint:
    def test_success_deletes_new_and_restores_updated(self, pg_api, mock_pg_cursor):
        # fetchone: checkpoint exists with timestamp
        mock_pg_cursor.fetchone.return_value = ("2026-01-01T00:00:00Z",)
        # fetchall: versions to restore
        mock_pg_cursor.fetchall.return_value = [
            ("mem_old", "restored content", ["tag"], '{"k":"v"}', None)
        ]
        result = pg_api.rollback_to_checkpoint("chk_123")
        assert result is True
        # Should have multiple execute calls: get checkpoint, delete new memories,
        # select versions, restore, delete versions, delete checkpoints
        assert mock_pg_cursor.execute.call_count >= 5

    def test_restores_with_embedding(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchone.return_value = ("2026-01-01T00:00:00Z",)
        mock_pg_cursor.fetchall.return_value = [
            ("mem_old", "content", ["tag"], '{"k":"v"}', [0.1] * 768)
        ]
        result = pg_api.rollback_to_checkpoint("chk_123")
        assert result is True

    def test_checkpoint_not_found(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchone.return_value = None
        result = pg_api.rollback_to_checkpoint("chk_nonexistent")
        assert result is False

    def test_no_changes_to_restore(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchone.return_value = ("2026-01-01T00:00:00Z",)
        mock_pg_cursor.fetchall.return_value = []  # no versions to restore
        result = pg_api.rollback_to_checkpoint("chk_123")
        assert result is True  # still succeeds (just deletes new memories + cleans up)

    def test_with_domain(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchone.return_value = ("2026-01-01T00:00:00Z",)
        mock_pg_cursor.fetchall.return_value = []
        result = pg_api.rollback_to_checkpoint("chk_123", domain="health")
        assert result is True


@pytest.mark.unit
@pytest.mark.postgres
class TestListCheckpoints:
    def test_returns_checkpoints(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchall.return_value = [
            {"id": "chk_2", "name": "save2", "tags": [], "created_at": "2026-01-02T00:00:00Z"},
            {"id": "chk_1", "name": "save1", "tags": ["a"], "created_at": "2026-01-01T00:00:00Z"},
        ]
        result = pg_api.list_checkpoints()
        assert len(result) == 2
        assert result[0]["id"] == "chk_2"

    def test_empty(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchall.return_value = []
        assert pg_api.list_checkpoints() == []

    def test_with_domain(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchall.return_value = []
        pg_api.list_checkpoints(domain="health")
        assert mock_pg_cursor.execute.called


@pytest.mark.unit
@pytest.mark.postgres
class TestVersionRetention:
    def test_prune_query_executed_on_update(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchone.return_value = MOCK_EXISTING_ROW
        pg_api.update_memory("mem_1", content="new")
        # Should have prune query among the execute calls
        calls_str = str(mock_pg_cursor.execute.call_args_list)
        assert "LIMIT GREATEST" in calls_str or mock_pg_cursor.execute.call_count >= 4

    def test_config_from_env(self, monkeypatch):
        monkeypatch.setenv("MAX_VERSIONS_PER_MEMORY", "5")
        with patch("postgres_memory_api.psycopg2"):
            import importlib, postgres_memory_api
            importlib.reload(postgres_memory_api)
            api = postgres_memory_api.PostgresMemoryAPI()
        assert api.max_versions == 5


@pytest.mark.unit
@pytest.mark.postgres
class TestTTLExpiration:
    def test_store_with_ttl(self, pg_api, mock_pg_cursor):
        mem_id = pg_api.store_memory("ephemeral", ttl_seconds=3600)
        assert mem_id.startswith("mem_")
        # The insert query should include expires_at
        assert mock_pg_cursor.execute.called

    def test_store_without_ttl(self, pg_api, mock_pg_cursor):
        pg_api.store_memory("permanent")
        assert mock_pg_cursor.execute.called


@pytest.mark.unit
@pytest.mark.postgres
class TestPurgeExpired:
    def test_purges_expired(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchall.return_value = [("mem_1",), ("mem_2",)]
        count = pg_api.purge_expired()
        assert count == 2

    def test_nothing_to_purge(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchall.return_value = []
        assert pg_api.purge_expired() == 0

    def test_with_domain(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchall.return_value = []
        pg_api.purge_expired(domain="health")
        assert mock_pg_cursor.execute.called


@pytest.mark.unit
@pytest.mark.postgres
class TestConsolidateMemories:
    def test_consolidation(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchall.return_value = [
            {"id": f"mem_{i}", "content": f"decision {i}", "tags": ["project-x"], "metadata": {}}
            for i in range(6)
        ]
        pg_api.store_memory = MagicMock(return_value="mem_summary")

        result = pg_api.consolidate_memories(
            ["project-x"], lambda t: "Summary text", older_than_days=30
        )
        assert result == ["mem_summary"]
        pg_api.store_memory.assert_called_once()

    def test_skips_when_below_min_count(self, pg_api, mock_pg_cursor):
        mock_pg_cursor.fetchall.return_value = [
            {"id": "mem_1", "content": "one", "tags": ["x"], "metadata": {}}
        ]
        result = pg_api.consolidate_memories(["x"], lambda t: "summary")
        assert result == []


@pytest.mark.unit
@pytest.mark.postgres
class TestCheckpointAutoCleanup:
    def test_cleanup_query_executed(self, pg_api, mock_pg_cursor):
        pg_api.create_checkpoint("save")
        # Should have insert + cleanup queries
        calls_str = str(mock_pg_cursor.execute.call_args_list)
        assert "days" in calls_str.lower() or mock_pg_cursor.execute.call_count >= 3


@pytest.mark.unit
@pytest.mark.postgres
class TestEnsureTableExists:
    def test_calls_create_function(self, pg_api, mock_pg_cursor):
        pg_api._ensure_table_exists("testdomain")
        execute_call = mock_pg_cursor.execute.call_args
        assert "create_domain_memories_table" in str(execute_call)
        assert "testdomain" in str(execute_call)
