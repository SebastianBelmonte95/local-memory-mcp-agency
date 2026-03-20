"""Tests for PostgreSQL MCP server tools, resources, and prompts."""
import pytest
import importlib
from unittest.mock import patch, MagicMock

# Prevent module-level Ollama check and psycopg2 usage during import
with patch("requests.get", side_effect=ConnectionError("mocked")), \
     patch("postgres_memory_api.psycopg2"):
    import postgres_memory_server
    from postgres_memory_server import (
        remember, recall, rollback, search,
        store_memory, update_memory, search_memories,
        get_memories, list_memory_domains, summarize_memories,
    )


@pytest.fixture(autouse=True)
def mock_api():
    """Replace memory_api with a mock for every test."""
    mock = MagicMock()
    with patch.object(postgres_memory_server, "memory_api", mock):
        yield mock


# ── Agency-Agents tools ─────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.postgres
class TestRememberTool:
    def test_basic(self, mock_api):
        mock_api.store_memory.return_value = "mem_123"
        result = remember("test content")
        assert result == "mem_123"
        mock_api.store_memory.assert_called_once_with("test content", {}, None, tags=None)

    def test_with_tags(self, mock_api):
        mock_api.store_memory.return_value = "mem_456"
        remember("content", tags=["agent-a", "project-x"])
        mock_api.store_memory.assert_called_once_with(
            "content", {}, None, tags=["agent-a", "project-x"]
        )

    def test_with_all_params(self, mock_api):
        mock_api.store_memory.return_value = "mem_789"
        remember("content", tags=["t"], domain="startup", source="meeting", importance=0.9)
        mock_api.store_memory.assert_called_once_with(
            "content", {"source": "meeting", "importance": 0.9}, "startup", tags=["t"]
        )


@pytest.mark.unit
@pytest.mark.postgres
class TestRecallTool:
    def test_by_tags(self, mock_api):
        mock_api.retrieve_memories.return_value = [
            {"id": "mem_1", "content": "result", "tags": ["a"], "metadata": {}}
        ]
        results = recall(tags=["a", "b"])
        mock_api.retrieve_memories.assert_called_once_with(
            query="", limit=5, domain=None, tags=["a", "b"]
        )
        assert results[0]["score"] == 0.0

    def test_by_query(self, mock_api):
        mock_api.retrieve_memories.return_value = []
        recall(query="test search")
        mock_api.retrieve_memories.assert_called_once_with(
            query="test search", limit=5, domain=None, tags=None
        )

    def test_with_domain(self, mock_api):
        mock_api.retrieve_memories.return_value = []
        recall(tags=["a"], domain="health", limit=3)
        mock_api.retrieve_memories.assert_called_once_with(
            query="", limit=3, domain="health", tags=["a"]
        )


@pytest.mark.unit
@pytest.mark.postgres
class TestRollbackTool:
    def test_success(self, mock_api):
        mock_api.rollback_memory.return_value = True
        assert rollback("mem_1") is True
        mock_api.rollback_memory.assert_called_once_with("mem_1", None)

    def test_with_domain(self, mock_api):
        mock_api.rollback_memory.return_value = True
        rollback("mem_1", domain="health")
        mock_api.rollback_memory.assert_called_once_with("mem_1", "health")

    def test_not_found(self, mock_api):
        mock_api.rollback_memory.return_value = False
        assert rollback("mem_nonexistent") is False


@pytest.mark.unit
@pytest.mark.postgres
class TestSearchTool:
    def test_basic(self, mock_api):
        mock_api.retrieve_memories.return_value = [
            {"id": "mem_1", "content": "result", "tags": [], "metadata": {}, "score": 0.8}
        ]
        results = search("test query")
        mock_api.retrieve_memories.assert_called_once_with("test query", 5, None)
        assert results[0]["query"] == "test query"

    def test_with_domain(self, mock_api):
        mock_api.retrieve_memories.return_value = []
        search("query", domain="health", limit=3)
        mock_api.retrieve_memories.assert_called_once_with("query", 3, "health")

    def test_adds_default_score(self, mock_api):
        mock_api.retrieve_memories.return_value = [
            {"id": "mem_1", "content": "result", "metadata": {}}
        ]
        results = search("query")
        assert results[0]["score"] == 0.0


# ── Legacy tools ────────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.postgres
class TestStoreMemoryTool:
    def test_basic(self, mock_api):
        mock_api.store_memory.return_value = "mem_123"
        result = store_memory("test content")
        assert result == "mem_123"
        mock_api.store_memory.assert_called_once_with("test content", {}, None)

    def test_with_domain(self, mock_api):
        mock_api.store_memory.return_value = "mem_456"
        store_memory("content", domain="health")
        mock_api.store_memory.assert_called_once_with("content", {}, "health")

    def test_with_all_params(self, mock_api):
        mock_api.store_memory.return_value = "mem_789"
        store_memory("content", domain="startup", source="meeting", importance=0.9)
        mock_api.store_memory.assert_called_once_with(
            "content", {"source": "meeting", "importance": 0.9}, "startup"
        )


@pytest.mark.unit
@pytest.mark.postgres
class TestUpdateMemoryTool:
    def test_update_content(self, mock_api):
        mock_api.update_memory.return_value = True
        result = update_memory("mem_123", content="new content")
        assert result is True
        mock_api.update_memory.assert_called_once_with("mem_123", "new content", {}, None)

    def test_with_domain(self, mock_api):
        mock_api.update_memory.return_value = True
        update_memory("mem_123", content="x", domain="health")
        mock_api.update_memory.assert_called_once_with("mem_123", "x", {}, "health")

    def test_with_importance(self, mock_api):
        mock_api.update_memory.return_value = True
        update_memory("mem_123", importance=0.9)
        mock_api.update_memory.assert_called_once_with("mem_123", None, {"importance": 0.9}, None)

    def test_not_found(self, mock_api):
        mock_api.update_memory.return_value = False
        assert update_memory("mem_nonexistent") is False


@pytest.mark.unit
@pytest.mark.postgres
class TestSearchMemoriesTool:
    def test_basic(self, mock_api):
        mock_api.retrieve_memories.return_value = [
            {"id": "mem_1", "content": "result", "metadata": {}, "score": 0.8}
        ]
        results = search_memories("test query")
        mock_api.retrieve_memories.assert_called_once_with("test query", 5, None)
        assert results[0]["query"] == "test query"

    def test_adds_default_score(self, mock_api):
        mock_api.retrieve_memories.return_value = [
            {"id": "mem_1", "content": "result", "metadata": {}}
        ]
        results = search_memories("query")
        assert results[0]["score"] == 0.0

    def test_with_domain(self, mock_api):
        mock_api.retrieve_memories.return_value = []
        search_memories("query", domain="health", limit=3)
        mock_api.retrieve_memories.assert_called_once_with("query", 3, "health")


@pytest.mark.unit
@pytest.mark.postgres
class TestGetMemoriesResource:
    def test_basic(self, mock_api):
        mock_api.retrieve_memories.return_value = []
        get_memories("health", "blood pressure", limit=3)
        mock_api.retrieve_memories.assert_called_once_with("blood pressure", 3, "health")


@pytest.mark.unit
@pytest.mark.postgres
class TestListMemoryDomains:
    def test_returns_domains(self, mock_api):
        mock_api.list_domains.return_value = ["default", "health"]
        result = list_memory_domains()
        assert result == ["default", "health"]
        mock_api.list_domains.assert_called_once()


@pytest.mark.unit
@pytest.mark.postgres
class TestSummarizeMemoriesPrompt:
    def test_formats_memories(self, mock_api):
        memories = [{"content": "alpha"}, {"content": "beta"}]
        result = summarize_memories(memories)
        assert "Memory 1: alpha" in result
        assert "Memory 2: beta" in result
        assert "Summary:" in result


@pytest.mark.unit
@pytest.mark.postgres
class TestModuleInit:
    def test_ollama_available_with_model(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": [{"name": "nomic-embed-text:v1.5"}]}

        with patch("requests.get", return_value=mock_response), \
             patch("postgres_memory_api.psycopg2"):
            importlib.reload(postgres_memory_server)

        assert postgres_memory_server.ollama_available is True
        assert postgres_memory_server.ollama_embeddings is not None
        with patch("requests.get", side_effect=ConnectionError("mocked")), \
             patch("postgres_memory_api.psycopg2"):
            importlib.reload(postgres_memory_server)

    def test_ollama_model_missing(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": [{"name": "other-model"}]}

        with patch("requests.get", return_value=mock_response), \
             patch("postgres_memory_api.psycopg2"):
            importlib.reload(postgres_memory_server)

        assert postgres_memory_server.ollama_available is False
        with patch("requests.get", side_effect=ConnectionError("mocked")), \
             patch("postgres_memory_api.psycopg2"):
            importlib.reload(postgres_memory_server)

    def test_ollama_api_error(self):
        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch("requests.get", return_value=mock_response), \
             patch("postgres_memory_api.psycopg2"):
            importlib.reload(postgres_memory_server)

        assert postgres_memory_server.ollama_available is False
        with patch("requests.get", side_effect=ConnectionError("mocked")), \
             patch("postgres_memory_api.psycopg2"):
            importlib.reload(postgres_memory_server)
