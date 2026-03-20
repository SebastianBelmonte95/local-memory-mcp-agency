"""Integration tests: SQLiteMemoryAPI + FAISSVectorAPI with mocked Ollama."""
import pytest
import time


@pytest.mark.integration
@pytest.mark.sqlite
class TestStoreAndRetrieve:
    def test_vector_search_round_trip(self, sqlite_memory_api_with_vectors):
        api = sqlite_memory_api_with_vectors
        mem_id = api.store_memory("Python is a versatile programming language")
        results = api.retrieve_memories("Python programming", use_vector=True)
        assert len(results) >= 1
        assert any(r["id"] == mem_id for r in results)

    def test_text_fallback(self, sqlite_memory_api_with_vectors):
        api = sqlite_memory_api_with_vectors
        api.store_memory("unique_keyword_xyz in this memory")
        results = api.retrieve_memories("unique_keyword_xyz", use_vector=False)
        assert len(results) >= 1
        assert "unique_keyword_xyz" in results[0]["content"]

    def test_multiple_memories_returned(self, sqlite_memory_api_with_vectors):
        api = sqlite_memory_api_with_vectors
        ids = []
        for text in ["Alpha topic one", "Beta topic two", "Gamma topic three"]:
            ids.append(api.store_memory(text))
            time.sleep(0.002)
        results = api.retrieve_memories("topic", limit=10)
        assert len(results) >= 1


@pytest.mark.integration
@pytest.mark.sqlite
class TestUpdateAndRetrieve:
    def test_updated_content_returned(self, sqlite_memory_api_with_vectors):
        api = sqlite_memory_api_with_vectors
        mem_id = api.store_memory("old information here")
        api.update_memory(mem_id, content="new information replaced")
        results = api.retrieve_memories("new information", use_vector=False)
        assert any("new information" in r["content"] for r in results)


@pytest.mark.integration
@pytest.mark.sqlite
class TestTagsIntegration:
    def test_store_and_recall_by_tags(self, sqlite_memory_api_with_vectors):
        api = sqlite_memory_api_with_vectors
        api.store_memory("API spec for frontend", tags=["backend-architect", "retroboard", "api-spec"])
        api.store_memory("Sprint plan", tags=["sprint-prioritizer", "retroboard"])
        time.sleep(0.002)
        results = api.retrieve_memories("", tags=["backend-architect"])
        assert len(results) == 1
        assert "API spec" in results[0]["content"]

    def test_multi_agent_handoff(self, sqlite_memory_api_with_vectors):
        """Simulate: backend-architect stores for frontend-developer to recall."""
        api = sqlite_memory_api_with_vectors
        api.store_memory(
            "REST API: GET /api/products returns Product[]",
            tags=["backend-architect", "retroboard", "frontend-developer"]
        )
        time.sleep(0.002)
        # Frontend developer recalls
        results = api.retrieve_memories("", tags=["frontend-developer", "retroboard"])
        assert len(results) == 1
        assert "Product[]" in results[0]["content"]


@pytest.mark.integration
@pytest.mark.sqlite
class TestRollbackIntegration:
    def test_rollback_after_bad_update(self, sqlite_memory_api_with_vectors):
        api = sqlite_memory_api_with_vectors
        mem_id = api.store_memory("good schema v1", tags=["backend-architect"])
        api.update_memory(mem_id, content="bad schema v2")
        api.rollback_memory(mem_id)
        results = api.retrieve_memories("schema", use_vector=False)
        assert any("good schema v1" in r["content"] for r in results)


@pytest.mark.integration
@pytest.mark.sqlite
class TestCheckpointIntegration:
    def test_full_agency_agents_scenario(self, sqlite_memory_api_with_vectors):
        """Create checkpoint → remember 3 → update 2 existing → rollback → verify."""
        api = sqlite_memory_api_with_vectors
        # Pre-existing memories
        existing_1 = api.store_memory("existing api spec v1", tags=["backend-architect"])
        existing_2 = api.store_memory("existing db schema v1", tags=["backend-architect"])
        time.sleep(0.01)

        # Create checkpoint
        chk_id = api.create_checkpoint("before-redesign", tags=["backend-architect"])
        time.sleep(0.01)

        # Agent creates 3 new memories
        new_1 = api.store_memory("new api spec v2", tags=["backend-architect"])
        time.sleep(0.002)
        new_2 = api.store_memory("new auth strategy", tags=["backend-architect"])
        time.sleep(0.002)
        new_3 = api.store_memory("new caching layer", tags=["backend-architect"])
        time.sleep(0.002)

        # Agent updates 2 existing memories
        api.update_memory(existing_1, content="existing api spec v2 BROKEN")
        api.update_memory(existing_2, content="existing db schema v2 BROKEN")

        # QA fails — rollback to checkpoint
        result = api.rollback_to_checkpoint(chk_id)
        assert result is True

        # Verify: 3 new memories gone
        all_results = api.retrieve_memories("", tags=["backend-architect"], limit=100)
        all_ids = [r["id"] for r in all_results]
        assert new_1 not in all_ids
        assert new_2 not in all_ids
        assert new_3 not in all_ids

        # Verify: 2 existing memories restored
        assert existing_1 in all_ids
        assert existing_2 in all_ids
        for r in all_results:
            if r["id"] == existing_1:
                assert r["content"] == "existing api spec v1"
            if r["id"] == existing_2:
                assert r["content"] == "existing db schema v1"


@pytest.mark.integration
@pytest.mark.sqlite
class TestGracefulDegradation:
    def test_broken_vector_store_falls_back(self, tmp_path, mock_ollama_embeddings):
        from unittest.mock import patch
        from sqlite_vector_api import FAISSVectorAPI
        from sqlite_memory_api import SQLiteMemoryAPI

        data_dir = str(tmp_path / "degrade_test")
        with patch("sqlite_vector_api.OllamaEmbeddings", return_value=mock_ollama_embeddings):
            vs = FAISSVectorAPI(data_dir=data_dir)

        db_path = str(tmp_path / "degrade.db")
        api = SQLiteMemoryAPI(db_path=db_path, vector_store=vs)

        api.store_memory("keyword_resilience_test content")

        vs.search = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("broken"))

        results = api.retrieve_memories("keyword_resilience_test", use_vector=True)
        assert len(results) >= 1
        assert "keyword_resilience_test" in results[0]["content"]
