"""Tests for SQLite MCP server tools, resources, and prompts."""
import pytest
import sys
import importlib
from unittest.mock import patch, MagicMock

# Prevent module-level Ollama check and FAISS initialization during import
with patch("requests.get", side_effect=ConnectionError("mocked")):
    import sqlite_memory_server
    from sqlite_memory_server import (
        remember, recall, rollback, search,
        store_memory, update_memory, search_memories,
        get_memories, summarize_memories,
    )


@pytest.fixture(autouse=True)
def mock_api():
    """Replace memory_api with a mock for every test."""
    mock = MagicMock()
    with patch.object(sqlite_memory_server, "memory_api", mock):
        yield mock


# ── Agency-Agents tools ─────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.sqlite
class TestRememberTool:
    def test_basic(self, mock_api):
        mock_api.store_memory.return_value = "mem_123"
        result = remember("test content")
        assert result == "mem_123"
        mock_api.store_memory.assert_called_once_with("test content", {}, tags=None)

    def test_with_tags(self, mock_api):
        mock_api.store_memory.return_value = "mem_456"
        remember("content", tags=["agent-a", "project-x"])
        mock_api.store_memory.assert_called_once_with(
            "content", {}, tags=["agent-a", "project-x"]
        )

    def test_with_all_params(self, mock_api):
        mock_api.store_memory.return_value = "mem_789"
        remember("content", tags=["t"], source="meeting", importance=0.9)
        mock_api.store_memory.assert_called_once_with(
            "content", {"source": "meeting", "importance": 0.9}, tags=["t"]
        )


@pytest.mark.unit
@pytest.mark.sqlite
class TestRecallTool:
    def test_by_tags(self, mock_api):
        mock_api.retrieve_memories.return_value = [
            {"id": "mem_1", "content": "result", "tags": ["a"], "metadata": {}}
        ]
        results = recall(tags=["a", "b"])
        mock_api.retrieve_memories.assert_called_once_with(
            query="", limit=5, use_vector=False, tags=["a", "b"]
        )
        assert results[0]["score"] == 0.0

    def test_by_query(self, mock_api):
        mock_api.retrieve_memories.return_value = []
        recall(query="test search")
        mock_api.retrieve_memories.assert_called_once_with(
            query="test search", limit=5, use_vector=True, tags=None
        )

    def test_tags_and_query(self, mock_api):
        mock_api.retrieve_memories.return_value = []
        recall(tags=["a"], query="test")
        mock_api.retrieve_memories.assert_called_once_with(
            query="test", limit=5, use_vector=True, tags=["a"]
        )


@pytest.mark.unit
@pytest.mark.sqlite
class TestRollbackTool:
    def test_success(self, mock_api):
        mock_api.rollback_memory.return_value = True
        assert rollback("mem_1") is True
        mock_api.rollback_memory.assert_called_once_with("mem_1")

    def test_not_found(self, mock_api):
        mock_api.rollback_memory.return_value = False
        assert rollback("mem_nonexistent") is False


@pytest.mark.unit
@pytest.mark.sqlite
class TestSearchTool:
    def test_basic(self, mock_api):
        mock_api.retrieve_memories.return_value = [
            {"id": "mem_1", "content": "result", "metadata": {}, "score": 0.9}
        ]
        results = search("test query")
        mock_api.retrieve_memories.assert_called_once_with("test query", 5, True)
        assert results[0]["query"] == "test query"

    def test_adds_default_score(self, mock_api):
        mock_api.retrieve_memories.return_value = [
            {"id": "mem_1", "content": "result", "metadata": {}}
        ]
        results = search("query")
        assert results[0]["score"] == 0.0

    def test_limit_and_use_vector(self, mock_api):
        mock_api.retrieve_memories.return_value = []
        search("query", limit=3, use_vector=False)
        mock_api.retrieve_memories.assert_called_once_with("query", 3, False)


# ── Legacy tools ────────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.sqlite
class TestStoreMemoryTool:
    def test_basic(self, mock_api):
        mock_api.store_memory.return_value = "mem_123"
        result = store_memory("test content")
        assert result == "mem_123"
        mock_api.store_memory.assert_called_once_with("test content", {})

    def test_with_source_and_importance(self, mock_api):
        mock_api.store_memory.return_value = "mem_456"
        store_memory("content", source="conversation", importance=0.8)
        mock_api.store_memory.assert_called_once_with(
            "content", {"source": "conversation", "importance": 0.8}
        )


@pytest.mark.unit
@pytest.mark.sqlite
class TestUpdateMemoryTool:
    def test_update_content(self, mock_api):
        mock_api.update_memory.return_value = True
        result = update_memory("mem_123", content="new content")
        assert result is True
        mock_api.update_memory.assert_called_once_with("mem_123", "new content", {})

    def test_update_importance(self, mock_api):
        mock_api.update_memory.return_value = True
        update_memory("mem_123", importance=0.9)
        mock_api.update_memory.assert_called_once_with("mem_123", None, {"importance": 0.9})

    def test_not_found(self, mock_api):
        mock_api.update_memory.return_value = False
        result = update_memory("mem_nonexistent", content="x")
        assert result is False


@pytest.mark.unit
@pytest.mark.sqlite
class TestSearchMemoriesTool:
    def test_basic_search(self, mock_api):
        mock_api.retrieve_memories.return_value = [
            {"id": "mem_1", "content": "result", "metadata": {}, "score": 0.9}
        ]
        results = search_memories("test query")
        mock_api.retrieve_memories.assert_called_once_with("test query", 5, True)
        assert results[0]["query"] == "test query"

    def test_adds_default_score(self, mock_api):
        mock_api.retrieve_memories.return_value = [
            {"id": "mem_1", "content": "result", "metadata": {}}
        ]
        results = search_memories("query")
        assert results[0]["score"] == 0.0

    def test_limit_and_use_vector(self, mock_api):
        mock_api.retrieve_memories.return_value = []
        search_memories("query", limit=3, use_vector=False)
        mock_api.retrieve_memories.assert_called_once_with("query", 3, False)


@pytest.mark.unit
@pytest.mark.sqlite
class TestGetMemoriesResource:
    def test_basic(self, mock_api):
        mock_api.retrieve_memories.return_value = [{"id": "mem_1", "content": "r", "metadata": {}}]
        results = get_memories("test query")
        mock_api.retrieve_memories.assert_called_once_with("test query", 5)

    def test_with_limit(self, mock_api):
        mock_api.retrieve_memories.return_value = []
        get_memories("query", limit=10)
        mock_api.retrieve_memories.assert_called_once_with("query", 10)


@pytest.mark.unit
@pytest.mark.sqlite
class TestSummarizeMemoriesPrompt:
    def test_formats_memories(self, mock_api):
        memories = [
            {"content": "first memory"},
            {"content": "second memory"},
        ]
        result = summarize_memories(memories)
        assert "Memory 1: first memory" in result
        assert "Memory 2: second memory" in result
        assert "Summary:" in result

    def test_empty_memories(self, mock_api):
        result = summarize_memories([])
        assert "Summary:" in result


@pytest.mark.unit
@pytest.mark.sqlite
class TestModuleInit:
    def test_ollama_available_with_model(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MCP_DATA_DIR", str(tmp_path))
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": [{"name": "nomic-embed-text:v1.5"}]}

        mock_faiss_api = MagicMock()

        with patch("requests.get", return_value=mock_response), \
             patch("sqlite_memory_server.FAISSVectorAPI", return_value=mock_faiss_api):
            importlib.reload(sqlite_memory_server)

        assert sqlite_memory_server.ollama_available is True
        with patch("requests.get", side_effect=ConnectionError("mocked")):
            importlib.reload(sqlite_memory_server)

    def test_ollama_available_model_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MCP_DATA_DIR", str(tmp_path))
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": [{"name": "other-model"}]}

        with patch("requests.get", return_value=mock_response):
            importlib.reload(sqlite_memory_server)

        assert sqlite_memory_server.ollama_available is False
        with patch("requests.get", side_effect=ConnectionError("mocked")):
            importlib.reload(sqlite_memory_server)

    def test_ollama_api_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MCP_DATA_DIR", str(tmp_path))
        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch("requests.get", return_value=mock_response):
            importlib.reload(sqlite_memory_server)

        assert sqlite_memory_server.ollama_available is False
        with patch("requests.get", side_effect=ConnectionError("mocked")):
            importlib.reload(sqlite_memory_server)

    def test_vector_store_init_failure(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MCP_DATA_DIR", str(tmp_path))
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": [{"name": "nomic-embed-text:v1.5"}]}

        with patch("requests.get", return_value=mock_response), \
             patch("sqlite_vector_api.FAISSVectorAPI", side_effect=RuntimeError("init fail")):
            importlib.reload(sqlite_memory_server)

        assert sqlite_memory_server.vector_store is None
        with patch("requests.get", side_effect=ConnectionError("mocked")):
            importlib.reload(sqlite_memory_server)
