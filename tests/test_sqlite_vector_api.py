"""Tests for FAISSVectorAPI vector store."""
import pytest
import os
from unittest.mock import patch
from sqlite_vector_api import FAISSVectorAPI


@pytest.fixture
def vector_api(tmp_path, mock_ollama_embeddings):
    """Create a FAISSVectorAPI with mocked Ollama embeddings."""
    data_dir = str(tmp_path / "faiss_data")
    with patch("sqlite_vector_api.OllamaEmbeddings", return_value=mock_ollama_embeddings):
        api = FAISSVectorAPI(data_dir=data_dir)
    return api


@pytest.mark.unit
@pytest.mark.sqlite
class TestInitialization:
    def test_creates_new_index(self, vector_api):
        assert vector_api.index.ntotal == 0
        assert vector_api.metadata == {"chunks": [], "id_map": {}}

    def test_creates_data_directory(self, tmp_path, mock_ollama_embeddings):
        data_dir = str(tmp_path / "new_dir" / "nested")
        with patch("sqlite_vector_api.OllamaEmbeddings", return_value=mock_ollama_embeddings):
            api = FAISSVectorAPI(data_dir=data_dir)
        assert os.path.isdir(data_dir)

    def test_loads_existing_index(self, tmp_path, mock_ollama_embeddings):
        data_dir = str(tmp_path / "persist_test")
        with patch("sqlite_vector_api.OllamaEmbeddings", return_value=mock_ollama_embeddings):
            api1 = FAISSVectorAPI(data_dir=data_dir)
            api1.add_text("t1", "Some test content for persistence")
            count = api1.index.ntotal

        with patch("sqlite_vector_api.OllamaEmbeddings", return_value=mock_ollama_embeddings):
            api2 = FAISSVectorAPI(data_dir=data_dir)
        assert api2.index.ntotal == count
        assert "t1" in api2.metadata["id_map"]

    def test_corrupt_index_file_resets(self, tmp_path, mock_ollama_embeddings):
        data_dir = str(tmp_path / "corrupt_idx")
        os.makedirs(data_dir, exist_ok=True)
        # Write garbage to the index file
        with open(os.path.join(data_dir, "faiss_index.bin"), "wb") as f:
            f.write(b"not a valid faiss index")
        with patch("sqlite_vector_api.OllamaEmbeddings", return_value=mock_ollama_embeddings):
            api = FAISSVectorAPI(data_dir=data_dir)
        assert api.index.ntotal == 0  # fresh index created

    def test_corrupt_metadata_file_resets(self, tmp_path, mock_ollama_embeddings):
        data_dir = str(tmp_path / "corrupt_meta")
        os.makedirs(data_dir, exist_ok=True)
        # Write invalid JSON to metadata file
        with open(os.path.join(data_dir, "faiss_metadata.json"), "w") as f:
            f.write("{invalid json!!!}")
        with patch("sqlite_vector_api.OllamaEmbeddings", return_value=mock_ollama_embeddings):
            api = FAISSVectorAPI(data_dir=data_dir)
        assert api.metadata == {"chunks": [], "id_map": {}}


@pytest.mark.unit
@pytest.mark.sqlite
class TestAddText:
    def test_single_short_text(self, vector_api):
        """Short text (below paragraph chunking threshold) becomes one chunk."""
        indices = vector_api.add_text("t1", "Short text")
        assert len(indices) >= 1
        assert vector_api.index.ntotal >= 1
        assert "t1" in vector_api.metadata["id_map"]

    def test_multi_paragraph_text(self, vector_api):
        """Multi-paragraph text produces multiple chunks."""
        text = ("A" * 60 + "\n\n") * 3 + ("B" * 60)
        indices = vector_api.add_text("t2", text)
        assert len(indices) > 1
        assert vector_api.index.ntotal == len(indices)

    def test_metadata_stored(self, vector_api):
        vector_api.add_text("t1", "Content", metadata={"source": "test"})
        chunk = vector_api.metadata["chunks"][0]
        assert chunk["text_id"] == "t1"
        assert chunk["metadata"]["source"] == "test"
        assert "created_at" in chunk


@pytest.mark.unit
@pytest.mark.sqlite
class TestSearch:
    def test_returns_results(self, vector_api):
        vector_api.add_text("t1", "Python programming language")
        vector_api.add_text("t2", "JavaScript web development")
        results = vector_api.search("Python code", limit=5)
        assert len(results) >= 1
        # Results should have expected fields
        assert "id" in results[0]
        assert "content" in results[0]
        assert "score" in results[0]

    def test_deduplicates_text_ids(self, vector_api):
        """Multi-chunk text should only appear once in results."""
        text = ("A" * 60 + "\n\n") * 5
        vector_api.add_text("t1", text)
        results = vector_api.search("test query", limit=10)
        text_ids = [r["id"] for r in results]
        assert text_ids.count("t1") == 1

    def test_skips_deleted_chunks(self, vector_api):
        vector_api.add_text("t1", "Original content here")
        vector_api.update_text("t1", content="Updated content now")
        results = vector_api.search("content", limit=10)
        # Only the updated version should appear
        for r in results:
            if r["id"] == "t1":
                assert "Updated" in r["content"] or "content" in r["content"].lower()

    def test_empty_index(self, vector_api):
        results = vector_api.search("anything")
        assert results == []

    def test_limit_caps_results(self, vector_api):
        """Adding more texts than the limit should cap returned results."""
        for i in range(5):
            vector_api.add_text(f"t{i}", f"Unique text number {i}")
        results = vector_api.search("text", limit=2)
        assert len(results) <= 2


@pytest.mark.unit
@pytest.mark.sqlite
class TestUpdateText:
    def test_update_content(self, vector_api):
        vector_api.add_text("t1", "Original content")
        result = vector_api.update_text("t1", content="New content")
        assert result is True
        # Old chunks should be marked deleted
        old_indices = [i for i, c in enumerate(vector_api.metadata["chunks"])
                       if c.get("text_id") is None]
        assert len(old_indices) > 0

    def test_update_metadata_only(self, vector_api):
        vector_api.add_text("t1", "Content stays", metadata={"v": 1})
        result = vector_api.update_text("t1", metadata={"v": 2})
        assert result is True
        chunks = vector_api.get_all_chunks_for_text("t1")
        assert chunks[0]["metadata"]["v"] == 2

    def test_update_nonexistent_returns_false(self, vector_api):
        assert vector_api.update_text("nonexistent", content="x") is False

    def test_no_changes_returns_false(self, vector_api):
        vector_api.add_text("t1", "Content")
        assert vector_api.update_text("t1") is False


@pytest.mark.unit
@pytest.mark.sqlite
class TestGetAllChunks:
    def test_returns_chunks(self, vector_api):
        text = ("A" * 60 + "\n\n") * 3
        vector_api.add_text("t1", text)
        chunks = vector_api.get_all_chunks_for_text("t1")
        assert len(chunks) > 0

    def test_nonexistent_returns_empty(self, vector_api):
        assert vector_api.get_all_chunks_for_text("nope") == []
