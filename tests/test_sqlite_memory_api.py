"""Tests for SQLiteMemoryAPI."""
import pytest
import json
import sqlite3
import time
from unittest.mock import MagicMock
from sqlite_memory_api import SQLiteMemoryAPI


@pytest.mark.unit
@pytest.mark.sqlite
class TestInitialization:
    def test_creates_db_and_tables(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        api = SQLiteMemoryAPI(db_path=db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memories'")
        assert cursor.fetchone() is not None
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memory_versions'")
        assert cursor.fetchone() is not None
        conn.close()

    def test_default_path_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MCP_DATA_DIR", str(tmp_path))
        api = SQLiteMemoryAPI()
        assert "memory.db" in api.db_path

    def test_migration_adds_tags_column(self, tmp_path):
        """If an old DB exists without tags column, migration adds it."""
        db_path = str(tmp_path / "old.db")
        conn = sqlite3.connect(db_path)
        conn.execute("""
        CREATE TABLE memories (
            id TEXT PRIMARY KEY, content TEXT NOT NULL,
            metadata TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
        )""")
        conn.commit()
        conn.close()
        # Should not raise — migration adds the tags column
        api = SQLiteMemoryAPI(db_path=db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(memories)")
        columns = [row[1] for row in cursor.fetchall()]
        assert "tags" in columns
        conn.close()


@pytest.mark.unit
@pytest.mark.sqlite
class TestStoreMemory:
    def test_returns_id(self, sqlite_memory_api):
        mem_id = sqlite_memory_api.store_memory("test content")
        assert mem_id.startswith("mem_")

    def test_stores_content_in_db(self, sqlite_memory_api):
        mem_id = sqlite_memory_api.store_memory("hello world")
        conn = sqlite3.connect(sqlite_memory_api.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT content FROM memories WHERE id = ?", (mem_id,))
        assert cursor.fetchone()[0] == "hello world"
        conn.close()

    def test_stores_metadata(self, sqlite_memory_api):
        mem_id = sqlite_memory_api.store_memory("content", {"source": "test", "importance": 0.8})
        conn = sqlite3.connect(sqlite_memory_api.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT metadata FROM memories WHERE id = ?", (mem_id,))
        meta = json.loads(cursor.fetchone()[0])
        assert meta["source"] == "test"
        assert meta["importance"] == 0.8
        assert "created_at" in meta
        conn.close()

    def test_stores_tags(self, sqlite_memory_api):
        mem_id = sqlite_memory_api.store_memory("content", tags=["agent-a", "project-x"])
        conn = sqlite3.connect(sqlite_memory_api.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT tags FROM memories WHERE id = ?", (mem_id,))
        tags = json.loads(cursor.fetchone()[0])
        assert tags == ["agent-a", "project-x"]
        conn.close()

    def test_tags_normalized_on_store(self, sqlite_memory_api):
        mem_id = sqlite_memory_api.store_memory("content", tags=["  Backend-Architect ", "RETROBOARD"])
        conn = sqlite3.connect(sqlite_memory_api.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT tags FROM memories WHERE id = ?", (mem_id,))
        tags = json.loads(cursor.fetchone()[0])
        assert tags == ["backend-architect", "retroboard"]
        conn.close()

    def test_default_empty_tags(self, sqlite_memory_api):
        mem_id = sqlite_memory_api.store_memory("content")
        conn = sqlite3.connect(sqlite_memory_api.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT tags FROM memories WHERE id = ?", (mem_id,))
        tags = json.loads(cursor.fetchone()[0])
        assert tags == []
        conn.close()

    def test_calls_vector_store(self, tmp_path):
        mock_vs = MagicMock()
        api = SQLiteMemoryAPI(db_path=str(tmp_path / "t.db"), vector_store=mock_vs)
        api.store_memory("content", {"source": "test"})
        mock_vs.add_text.assert_called_once()

    def test_vector_store_error_silent(self, tmp_path):
        mock_vs = MagicMock()
        mock_vs.add_text.side_effect = RuntimeError("boom")
        api = SQLiteMemoryAPI(db_path=str(tmp_path / "t.db"), vector_store=mock_vs)
        mem_id = api.store_memory("content")
        assert mem_id.startswith("mem_")


@pytest.mark.unit
@pytest.mark.sqlite
class TestRetrieveMemories:
    def test_text_search(self, sqlite_memory_api):
        sqlite_memory_api.store_memory("Python is great")
        sqlite_memory_api.store_memory("JavaScript is okay")
        results = sqlite_memory_api.retrieve_memories("Python")
        assert len(results) >= 1
        assert any("Python" in r["content"] for r in results)

    def test_text_search_no_match(self, sqlite_memory_api):
        sqlite_memory_api.store_memory("Python is great")
        results = sqlite_memory_api.retrieve_memories("nonexistent_xyz")
        assert results == []

    def test_vector_search_used_when_available(self, tmp_path):
        mock_vs = MagicMock()
        mock_vs.search.return_value = [
            {"id": "mem_1", "content": "result", "metadata": {}, "score": 0.9}
        ]
        api = SQLiteMemoryAPI(db_path=str(tmp_path / "t.db"), vector_store=mock_vs)
        results = api.retrieve_memories("query", use_vector=True)
        mock_vs.search.assert_called_once_with("query", 5)
        assert len(results) == 1

    def test_vector_empty_falls_back_to_text(self, tmp_path):
        mock_vs = MagicMock()
        mock_vs.search.return_value = []
        api = SQLiteMemoryAPI(db_path=str(tmp_path / "t.db"), vector_store=mock_vs)
        api.store_memory("fallback content keyword_xyz")
        results = api.retrieve_memories("keyword_xyz", use_vector=True)
        assert any("keyword_xyz" in r["content"] for r in results)

    def test_vector_error_falls_back_to_text(self, tmp_path):
        mock_vs = MagicMock()
        mock_vs.search.side_effect = RuntimeError("broken")
        api = SQLiteMemoryAPI(db_path=str(tmp_path / "t.db"), vector_store=mock_vs)
        api.store_memory("error fallback keyword_abc")
        results = api.retrieve_memories("keyword_abc", use_vector=True)
        assert any("keyword_abc" in r["content"] for r in results)

    def test_use_vector_false_skips_vector(self, tmp_path):
        mock_vs = MagicMock()
        api = SQLiteMemoryAPI(db_path=str(tmp_path / "t.db"), vector_store=mock_vs)
        api.store_memory("content")
        api.retrieve_memories("content", use_vector=False)
        mock_vs.search.assert_not_called()

    def test_limit(self, sqlite_memory_api):
        for i in range(10):
            sqlite_memory_api.store_memory(f"memory number {i}")
        results = sqlite_memory_api.retrieve_memories("memory", limit=3)
        assert len(results) <= 3

    def test_filter_by_tags(self, sqlite_memory_api):
        sqlite_memory_api.store_memory("tagged content", tags=["agent-a", "project-x"])
        sqlite_memory_api.store_memory("other content", tags=["agent-b"])
        time.sleep(0.002)
        results = sqlite_memory_api.retrieve_memories("", tags=["agent-a"])
        assert len(results) == 1
        assert results[0]["tags"] == ["agent-a", "project-x"]

    def test_filter_by_multiple_tags_and(self, sqlite_memory_api):
        sqlite_memory_api.store_memory("both tags", tags=["a", "b"])
        sqlite_memory_api.store_memory("one tag", tags=["a"])
        time.sleep(0.002)
        results = sqlite_memory_api.retrieve_memories("", tags=["a", "b"])
        assert len(results) == 1
        assert "both tags" in results[0]["content"]

    def test_tags_normalized_on_recall(self, sqlite_memory_api):
        """Querying with mixed-case tags still matches lowercase-stored tags."""
        sqlite_memory_api.store_memory("tagged content", tags=["agent-a"])
        time.sleep(0.002)
        results = sqlite_memory_api.retrieve_memories("", tags=["  Agent-A  "])
        assert len(results) == 1

    def test_tags_with_query(self, sqlite_memory_api):
        sqlite_memory_api.store_memory("keyword_xyz tagged", tags=["a"])
        sqlite_memory_api.store_memory("keyword_xyz untagged", tags=["b"])
        time.sleep(0.002)
        results = sqlite_memory_api.retrieve_memories("keyword_xyz", tags=["a"])
        assert len(results) == 1
        assert "tagged" in results[0]["content"]

    def test_tags_with_query_no_match(self, sqlite_memory_api):
        """Tags match but query doesn't — should skip the row."""
        sqlite_memory_api.store_memory("irrelevant content", tags=["a"])
        time.sleep(0.002)
        results = sqlite_memory_api.retrieve_memories("nonexistent_xyz", tags=["a"])
        assert results == []

    def test_tags_filter_limit(self, sqlite_memory_api):
        """Tag filter should respect limit."""
        for i in range(5):
            sqlite_memory_api.store_memory(f"item {i}", tags=["bulk"])
            time.sleep(0.002)
        results = sqlite_memory_api.retrieve_memories("", tags=["bulk"], limit=2)
        assert len(results) == 2

    def test_filter_by_tags_empty_results(self, tmp_path):
        """_filter_by_tags with empty input returns early."""
        api = SQLiteMemoryAPI(db_path=str(tmp_path / "t.db"))
        result = api._filter_by_tags([], ["a"], 5)
        assert result == []

    def test_filter_by_tags_limit(self, tmp_path):
        """_filter_by_tags should cap at limit."""
        api = SQLiteMemoryAPI(db_path=str(tmp_path / "t.db"))
        for i in range(5):
            api.store_memory(f"item {i}", tags=["x"])
            time.sleep(0.002)
        # Simulate vector results
        fake_results = [{"id": f"mem_{i}", "content": f"item {i}", "metadata": {}, "score": 0.9}
                        for i in range(5)]
        # Need matching IDs in DB — re-read actual IDs
        import sqlite3
        conn = sqlite3.connect(api.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM memories")
        real_ids = [r[0] for r in cursor.fetchall()]
        conn.close()
        fake_results = [{"id": rid, "content": "c", "metadata": {}, "score": 0.9} for rid in real_ids]
        filtered = api._filter_by_tags(fake_results, ["x"], 2)
        assert len(filtered) == 2

    def test_vector_search_with_tag_filter(self, tmp_path):
        """When vector search returns results, tag filter is applied post-hoc."""
        mock_vs = MagicMock()
        mock_vs.search.return_value = [
            {"id": "mem_1", "content": "r1", "metadata": {}, "score": 0.9},
            {"id": "mem_2", "content": "r2", "metadata": {}, "score": 0.8},
        ]
        api = SQLiteMemoryAPI(db_path=str(tmp_path / "t.db"), vector_store=mock_vs)
        # Store with tags so the filter can look them up
        conn = sqlite3.connect(api.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO memories (id, content, tags, metadata, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("mem_1", "r1", '["a"]', '{}', 1.0, 1.0)
        )
        cursor.execute(
            "INSERT INTO memories (id, content, tags, metadata, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("mem_2", "r2", '["b"]', '{}', 1.0, 1.0)
        )
        conn.commit()
        conn.close()

        results = api.retrieve_memories("query", use_vector=True, tags=["a"])
        assert len(results) == 1
        assert results[0]["id"] == "mem_1"


@pytest.mark.unit
@pytest.mark.sqlite
class TestUpdateMemory:
    def test_update_content(self, sqlite_memory_api):
        mem_id = sqlite_memory_api.store_memory("old content")
        result = sqlite_memory_api.update_memory(mem_id, content="new content")
        assert result is True
        conn = sqlite3.connect(sqlite_memory_api.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT content FROM memories WHERE id = ?", (mem_id,))
        assert cursor.fetchone()[0] == "new content"
        conn.close()

    def test_update_metadata(self, sqlite_memory_api):
        mem_id = sqlite_memory_api.store_memory("content", {"importance": 0.5})
        result = sqlite_memory_api.update_memory(mem_id, metadata={"importance": 0.9})
        assert result is True
        conn = sqlite3.connect(sqlite_memory_api.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT metadata FROM memories WHERE id = ?", (mem_id,))
        meta = json.loads(cursor.fetchone()[0])
        assert meta["importance"] == 0.9
        assert "updated_at" in meta
        conn.close()

    def test_update_tags(self, sqlite_memory_api):
        mem_id = sqlite_memory_api.store_memory("content", tags=["old-tag"])
        sqlite_memory_api.update_memory(mem_id, tags=["new-tag"])
        conn = sqlite3.connect(sqlite_memory_api.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT tags FROM memories WHERE id = ?", (mem_id,))
        tags = json.loads(cursor.fetchone()[0])
        assert tags == ["new-tag"]
        conn.close()

    def test_update_nonexistent_returns_false(self, sqlite_memory_api):
        assert sqlite_memory_api.update_memory("mem_nonexistent", content="x") is False

    def test_creates_version_snapshot(self, sqlite_memory_api):
        mem_id = sqlite_memory_api.store_memory("original")
        sqlite_memory_api.update_memory(mem_id, content="updated")
        conn = sqlite3.connect(sqlite_memory_api.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT content FROM memory_versions WHERE memory_id = ?", (mem_id,))
        version = cursor.fetchone()
        assert version is not None
        assert version[0] == "original"
        conn.close()

    def test_calls_vector_store_on_content_update(self, tmp_path):
        mock_vs = MagicMock()
        api = SQLiteMemoryAPI(db_path=str(tmp_path / "t.db"), vector_store=mock_vs)
        mem_id = api.store_memory("original")
        api.update_memory(mem_id, content="updated")
        mock_vs.update_text.assert_called()

    def test_metadata_only_update_calls_vector_store(self, tmp_path):
        mock_vs = MagicMock()
        api = SQLiteMemoryAPI(db_path=str(tmp_path / "t.db"), vector_store=mock_vs)
        mem_id = api.store_memory("content")
        api.update_memory(mem_id, metadata={"importance": 0.9})
        mock_vs.update_text.assert_called()
        call_args = mock_vs.update_text.call_args
        assert call_args[0][0] == mem_id
        assert call_args[0][1] is None

    def test_vector_store_error_silent_on_update(self, tmp_path):
        mock_vs = MagicMock()
        mock_vs.update_text.side_effect = RuntimeError("boom")
        api = SQLiteMemoryAPI(db_path=str(tmp_path / "t.db"), vector_store=mock_vs)
        mem_id = api.store_memory("original")
        result = api.update_memory(mem_id, content="updated")
        assert result is True


@pytest.mark.unit
@pytest.mark.sqlite
class TestRollbackMemory:
    def test_rollback_restores_previous(self, sqlite_memory_api):
        mem_id = sqlite_memory_api.store_memory("v1 content", tags=["tag1"])
        sqlite_memory_api.update_memory(mem_id, content="v2 content", tags=["tag2"])
        result = sqlite_memory_api.rollback_memory(mem_id)
        assert result is True
        conn = sqlite3.connect(sqlite_memory_api.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT content, tags FROM memories WHERE id = ?", (mem_id,))
        row = cursor.fetchone()
        assert row[0] == "v1 content"
        assert json.loads(row[1]) == ["tag1"]
        conn.close()

    def test_rollback_consumes_version(self, sqlite_memory_api):
        mem_id = sqlite_memory_api.store_memory("v1")
        sqlite_memory_api.update_memory(mem_id, content="v2")
        sqlite_memory_api.rollback_memory(mem_id)
        # Version history should be empty now
        conn = sqlite3.connect(sqlite_memory_api.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM memory_versions WHERE memory_id = ?", (mem_id,))
        assert cursor.fetchone()[0] == 0
        conn.close()

    def test_rollback_memory_not_found(self, sqlite_memory_api):
        assert sqlite_memory_api.rollback_memory("mem_nonexistent") is False

    def test_rollback_no_versions(self, sqlite_memory_api):
        mem_id = sqlite_memory_api.store_memory("original")
        # No updates = no version history
        assert sqlite_memory_api.rollback_memory(mem_id) is False

    def test_multiple_rollbacks(self, sqlite_memory_api):
        mem_id = sqlite_memory_api.store_memory("v1")
        sqlite_memory_api.update_memory(mem_id, content="v2")
        sqlite_memory_api.update_memory(mem_id, content="v3")
        # Rollback to v2
        sqlite_memory_api.rollback_memory(mem_id)
        conn = sqlite3.connect(sqlite_memory_api.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT content FROM memories WHERE id = ?", (mem_id,))
        assert cursor.fetchone()[0] == "v2"
        # Rollback again to v1
        sqlite_memory_api.rollback_memory(mem_id)
        cursor.execute("SELECT content FROM memories WHERE id = ?", (mem_id,))
        assert cursor.fetchone()[0] == "v1"
        conn.close()

    def test_rollback_updates_vector_store(self, tmp_path):
        mock_vs = MagicMock()
        api = SQLiteMemoryAPI(db_path=str(tmp_path / "t.db"), vector_store=mock_vs)
        mem_id = api.store_memory("original")
        api.update_memory(mem_id, content="updated")
        api.rollback_memory(mem_id)
        # Should have called update_text to restore old content
        last_call = mock_vs.update_text.call_args
        assert last_call[0][1] == "original"

    def test_rollback_vector_store_error_silent(self, tmp_path):
        mock_vs = MagicMock()
        api = SQLiteMemoryAPI(db_path=str(tmp_path / "t.db"), vector_store=mock_vs)
        mem_id = api.store_memory("original")
        api.update_memory(mem_id, content="updated")
        mock_vs.update_text.side_effect = RuntimeError("broken")
        # Should not raise
        result = api.rollback_memory(mem_id)
        assert result is True


@pytest.mark.unit
@pytest.mark.sqlite
class TestCreateCheckpoint:
    def test_returns_id(self, sqlite_memory_api):
        chk_id = sqlite_memory_api.create_checkpoint("save point")
        assert chk_id.startswith("chk_")

    def test_stores_in_db(self, sqlite_memory_api):
        chk_id = sqlite_memory_api.create_checkpoint("my save", tags=["a", "b"])
        conn = sqlite3.connect(sqlite_memory_api.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name, tags FROM checkpoints WHERE id = ?", (chk_id,))
        row = cursor.fetchone()
        assert row[0] == "my save"
        assert json.loads(row[1]) == ["a", "b"]
        conn.close()


@pytest.mark.unit
@pytest.mark.sqlite
class TestRollbackToCheckpoint:
    def test_deletes_new_memories(self, sqlite_memory_api):
        api = sqlite_memory_api
        api.store_memory("before checkpoint")
        time.sleep(0.01)
        chk_id = api.create_checkpoint("save")
        time.sleep(0.01)
        api.store_memory("after checkpoint")
        api.rollback_to_checkpoint(chk_id)
        # Only the pre-checkpoint memory should remain
        conn = sqlite3.connect(api.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM memories")
        assert cursor.fetchone()[0] == 1
        cursor.execute("SELECT content FROM memories")
        assert cursor.fetchone()[0] == "before checkpoint"
        conn.close()

    def test_restores_updated_memories(self, sqlite_memory_api):
        api = sqlite_memory_api
        mem_id = api.store_memory("original content", tags=["old-tag"])
        time.sleep(0.01)
        chk_id = api.create_checkpoint("save")
        time.sleep(0.01)
        api.update_memory(mem_id, content="modified content", tags=["new-tag"])
        api.rollback_to_checkpoint(chk_id)
        conn = sqlite3.connect(api.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT content, tags FROM memories WHERE id = ?", (mem_id,))
        row = cursor.fetchone()
        assert row[0] == "original content"
        assert json.loads(row[1]) == ["old-tag"]
        conn.close()

    def test_mixed_scenario(self, sqlite_memory_api):
        """Some memories created, some updated, some untouched after checkpoint."""
        api = sqlite_memory_api
        untouched_id = api.store_memory("untouched")
        updated_id = api.store_memory("will be updated")
        time.sleep(0.01)
        chk_id = api.create_checkpoint("save")
        time.sleep(0.01)
        new_id = api.store_memory("new after checkpoint")
        api.update_memory(updated_id, content="modified")
        api.rollback_to_checkpoint(chk_id)
        conn = sqlite3.connect(api.db_path)
        cursor = conn.cursor()
        # New memory should be deleted
        cursor.execute("SELECT 1 FROM memories WHERE id = ?", (new_id,))
        assert cursor.fetchone() is None
        # Updated memory should be restored
        cursor.execute("SELECT content FROM memories WHERE id = ?", (updated_id,))
        assert cursor.fetchone()[0] == "will be updated"
        # Untouched memory should still exist
        cursor.execute("SELECT content FROM memories WHERE id = ?", (untouched_id,))
        assert cursor.fetchone()[0] == "untouched"
        conn.close()

    def test_checkpoint_not_found(self, sqlite_memory_api):
        assert sqlite_memory_api.rollback_to_checkpoint("chk_nonexistent") is False

    def test_cleans_up_versions_and_checkpoint(self, sqlite_memory_api):
        api = sqlite_memory_api
        mem_id = api.store_memory("content")
        time.sleep(0.01)
        chk_id = api.create_checkpoint("save")
        time.sleep(0.01)
        api.update_memory(mem_id, content="changed")
        api.rollback_to_checkpoint(chk_id)
        conn = sqlite3.connect(api.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM memory_versions")
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT COUNT(*) FROM checkpoints")
        assert cursor.fetchone()[0] == 0
        conn.close()

    def test_deletes_later_checkpoints(self, sqlite_memory_api):
        api = sqlite_memory_api
        time.sleep(0.01)
        chk1 = api.create_checkpoint("first")
        time.sleep(0.01)
        chk2 = api.create_checkpoint("second")
        api.rollback_to_checkpoint(chk1)
        conn = sqlite3.connect(api.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM checkpoints")
        assert cursor.fetchone()[0] == 0  # both deleted (>= checkpoint time)
        conn.close()

    def test_vector_store_error_on_restore_silent(self, tmp_path, mock_ollama_embeddings):
        from unittest.mock import patch, MagicMock
        from sqlite_vector_api import FAISSVectorAPI

        data_dir = str(tmp_path / "vs_err")
        with patch("sqlite_vector_api.OllamaEmbeddings", return_value=mock_ollama_embeddings):
            vs = FAISSVectorAPI(data_dir=data_dir)

        db_path = str(tmp_path / "chk_err.db")
        api = SQLiteMemoryAPI(db_path=db_path, vector_store=vs)
        mem_id = api.store_memory("original")
        time.sleep(0.01)
        chk_id = api.create_checkpoint("save")
        time.sleep(0.01)
        api.update_memory(mem_id, content="changed")
        # Break the vector store
        vs.update_text = MagicMock(side_effect=RuntimeError("broken"))
        # Should not raise
        result = api.rollback_to_checkpoint(chk_id)
        assert result is True

    def test_vector_store_updated_on_restore(self, tmp_path, mock_ollama_embeddings):
        from unittest.mock import patch
        from sqlite_vector_api import FAISSVectorAPI

        data_dir = str(tmp_path / "vs_data")
        with patch("sqlite_vector_api.OllamaEmbeddings", return_value=mock_ollama_embeddings):
            vs = FAISSVectorAPI(data_dir=data_dir)

        db_path = str(tmp_path / "chk.db")
        api = SQLiteMemoryAPI(db_path=db_path, vector_store=vs)

        mem_id = api.store_memory("original")
        time.sleep(0.01)
        chk_id = api.create_checkpoint("save")
        time.sleep(0.01)
        api.update_memory(mem_id, content="changed")
        api.rollback_to_checkpoint(chk_id)
        # Verify the memory was restored in DB
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT content FROM memories WHERE id = ?", (mem_id,))
        assert cursor.fetchone()[0] == "original"
        conn.close()


@pytest.mark.unit
@pytest.mark.sqlite
class TestListCheckpoints:
    def test_returns_checkpoints(self, sqlite_memory_api):
        sqlite_memory_api.create_checkpoint("first")
        time.sleep(0.01)
        sqlite_memory_api.create_checkpoint("second")
        result = sqlite_memory_api.list_checkpoints()
        assert len(result) == 2
        assert result[0]["name"] == "second"  # newest first
        assert result[1]["name"] == "first"

    def test_empty(self, sqlite_memory_api):
        assert sqlite_memory_api.list_checkpoints() == []

    def test_includes_tags(self, sqlite_memory_api):
        sqlite_memory_api.create_checkpoint("tagged", tags=["a", "b"])
        result = sqlite_memory_api.list_checkpoints()
        assert result[0]["tags"] == ["a", "b"]


@pytest.mark.unit
@pytest.mark.sqlite
class TestCheckpointSchema:
    def test_checkpoints_table_exists(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        SQLiteMemoryAPI(db_path=db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='checkpoints'")
        assert cursor.fetchone() is not None
        conn.close()


@pytest.mark.unit
@pytest.mark.sqlite
class TestVersionRetention:
    def test_prunes_oldest_when_exceeding_limit(self, tmp_path):
        api = SQLiteMemoryAPI(db_path=str(tmp_path / "t.db"))
        api.max_versions = 3
        mem_id = api.store_memory("v0")
        for i in range(5):
            api.update_memory(mem_id, content=f"v{i+1}")
        conn = sqlite3.connect(api.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM memory_versions WHERE memory_id = ?", (mem_id,))
        assert cursor.fetchone()[0] == 3
        conn.close()

    def test_limit_one_keeps_only_most_recent(self, tmp_path):
        api = SQLiteMemoryAPI(db_path=str(tmp_path / "t.db"))
        api.max_versions = 1
        mem_id = api.store_memory("v0")
        api.update_memory(mem_id, content="v1")
        api.update_memory(mem_id, content="v2")
        conn = sqlite3.connect(api.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM memory_versions WHERE memory_id = ?", (mem_id,))
        assert cursor.fetchone()[0] == 1
        # The kept version should be the most recent snapshot (v1 content)
        cursor.execute("SELECT content FROM memory_versions WHERE memory_id = ?", (mem_id,))
        assert cursor.fetchone()[0] == "v1"
        conn.close()

    def test_default_limit_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MAX_VERSIONS_PER_MEMORY", "5")
        api = SQLiteMemoryAPI(db_path=str(tmp_path / "t.db"))
        assert api.max_versions == 5


@pytest.mark.unit
@pytest.mark.sqlite
class TestTTLExpiration:
    def test_store_with_ttl(self, sqlite_memory_api):
        mem_id = sqlite_memory_api.store_memory("ephemeral", ttl_seconds=3600)
        conn = sqlite3.connect(sqlite_memory_api.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT expires_at FROM memories WHERE id = ?", (mem_id,))
        expires_at = cursor.fetchone()[0]
        assert expires_at is not None
        assert expires_at > time.time()
        conn.close()

    def test_store_without_ttl_has_null_expires(self, sqlite_memory_api):
        mem_id = sqlite_memory_api.store_memory("permanent")
        conn = sqlite3.connect(sqlite_memory_api.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT expires_at FROM memories WHERE id = ?", (mem_id,))
        assert cursor.fetchone()[0] is None
        conn.close()

    def test_expired_memories_not_in_search(self, sqlite_memory_api):
        # Store, then manually set expires_at to the past
        mem_id = sqlite_memory_api.store_memory("expired_keyword_xyz", ttl_seconds=3600)
        conn = sqlite3.connect(sqlite_memory_api.db_path)
        conn.execute("UPDATE memories SET expires_at = ? WHERE id = ?", (time.time() - 10, mem_id))
        conn.commit()
        conn.close()
        results = sqlite_memory_api.retrieve_memories("expired_keyword_xyz")
        assert not any(r["id"] == mem_id for r in results)

    def test_unexpired_memories_in_search(self, sqlite_memory_api):
        mem_id = sqlite_memory_api.store_memory("fresh_keyword_xyz", ttl_seconds=99999)
        results = sqlite_memory_api.retrieve_memories("fresh_keyword_xyz")
        assert any(r["id"] == mem_id for r in results)

    def test_purge_expired(self, sqlite_memory_api):
        mem_id = sqlite_memory_api.store_memory("will expire", ttl_seconds=3600)
        # Manually expire it
        conn = sqlite3.connect(sqlite_memory_api.db_path)
        conn.execute("UPDATE memories SET expires_at = ? WHERE id = ?", (time.time() - 10, mem_id))
        conn.commit()
        conn.close()
        sqlite_memory_api.store_memory("stays forever")
        count = sqlite_memory_api.purge_expired()
        assert count == 1
        conn = sqlite3.connect(sqlite_memory_api.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM memories")
        assert cursor.fetchone()[0] == 1
        conn.close()

    def test_purge_nothing(self, sqlite_memory_api):
        sqlite_memory_api.store_memory("permanent")
        assert sqlite_memory_api.purge_expired() == 0

    def test_expires_at_migration(self, tmp_path):
        """Existing DB without expires_at column gets it via migration."""
        db_path = str(tmp_path / "old.db")
        conn = sqlite3.connect(db_path)
        conn.execute("""
        CREATE TABLE memories (
            id TEXT PRIMARY KEY, content TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '[]',
            metadata TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
        )""")
        conn.commit()
        conn.close()
        api = SQLiteMemoryAPI(db_path=db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(memories)")
        columns = [row[1] for row in cursor.fetchall()]
        assert "expires_at" in columns
        conn.close()


@pytest.mark.unit
@pytest.mark.sqlite
class TestConsolidateMemories:
    def test_consolidation(self, sqlite_memory_api):
        api = sqlite_memory_api
        # Store old memories (fake old timestamps)
        conn = sqlite3.connect(api.db_path)
        cursor = conn.cursor()
        old_time = time.time() - (60 * 86400)  # 60 days ago
        for i in range(6):
            cursor.execute(
                "INSERT INTO memories (id, content, tags, metadata, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (f"mem_old_{i}", f"decision {i}", '["project-x"]', '{}', old_time + i, old_time + i)
            )
        conn.commit()
        conn.close()

        def fake_summarizer(text):
            return f"Summary of {text.count('Memory')} memories"

        result = api.consolidate_memories(["project-x"], fake_summarizer, older_than_days=30)
        assert len(result) == 1

        # Originals should be deleted
        conn = sqlite3.connect(api.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM memories WHERE id LIKE 'mem_old_%'")
        assert cursor.fetchone()[0] == 0

        # Summary should exist with consolidated tag and metadata
        cursor.execute("SELECT content, tags, metadata FROM memories WHERE id = ?", (result[0],))
        row = cursor.fetchone()
        assert "Summary" in row[0]
        tags = json.loads(row[1])
        assert "consolidated" in tags
        assert "project-x" in tags
        meta = json.loads(row[2])
        assert "consolidated_from" in meta
        assert len(meta["consolidated_from"]) == 6
        conn.close()

    def test_skips_when_below_min_count(self, sqlite_memory_api):
        api = sqlite_memory_api
        api.store_memory("one memory", tags=["project-x"])
        result = api.consolidate_memories(["project-x"], lambda t: "summary", older_than_days=0)
        assert result == []

    def test_skips_recent_memories(self, sqlite_memory_api):
        api = sqlite_memory_api
        for i in range(10):
            api.store_memory(f"recent {i}", tags=["project-x"])
            time.sleep(0.002)
        result = api.consolidate_memories(["project-x"], lambda t: "summary", older_than_days=30)
        assert result == []  # all too recent


@pytest.mark.unit
@pytest.mark.sqlite
class TestCheckpointAutoCleanup:
    def test_old_checkpoints_pruned(self, tmp_path):
        api = SQLiteMemoryAPI(db_path=str(tmp_path / "t.db"))
        api.checkpoint_retention_days = 0  # everything is "old"
        # Create an old checkpoint manually
        conn = sqlite3.connect(api.db_path)
        cursor = conn.cursor()
        old_time = time.time() - 86400
        cursor.execute("INSERT INTO checkpoints (id, name, tags, created_at) VALUES (?, ?, ?, ?)",
                       ("chk_old", "old save", "[]", old_time))
        conn.commit()
        conn.close()

        # Creating a new checkpoint should prune the old one
        api.create_checkpoint("new save")

        conn = sqlite3.connect(api.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM checkpoints")
        # Only the new one should remain (old was pruned, but most recent is kept)
        assert cursor.fetchone()[0] == 1
        cursor.execute("SELECT name FROM checkpoints")
        assert cursor.fetchone()[0] == "new save"
        conn.close()

    def test_recent_checkpoints_kept(self, sqlite_memory_api):
        api = sqlite_memory_api
        api.checkpoint_retention_days = 30
        api.create_checkpoint("first")
        time.sleep(0.01)
        api.create_checkpoint("second")
        conn = sqlite3.connect(api.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM checkpoints")
        assert cursor.fetchone()[0] == 2
        conn.close()
