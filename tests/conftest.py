import pytest
import numpy as np
import hashlib
from unittest.mock import MagicMock, patch


def deterministic_embedding(text: str, dim: int = 768) -> list:
    """Generate a deterministic 768-dim embedding from text using a hash seed."""
    seed = int(hashlib.md5(text.encode()).hexdigest()[:8], 16)
    rng = np.random.RandomState(seed)
    vec = rng.randn(dim).astype(np.float32)
    vec = vec / np.linalg.norm(vec)  # normalize
    return vec.tolist()


@pytest.fixture
def mock_ollama_embeddings():
    """Mock OllamaEmbeddings that returns deterministic vectors."""
    mock = MagicMock()
    mock.get_embedding.side_effect = lambda text: deterministic_embedding(text)
    mock.get_embeddings.side_effect = lambda texts: [deterministic_embedding(t) for t in texts]
    return mock


@pytest.fixture
def sqlite_memory_api(tmp_path):
    """SQLiteMemoryAPI with no vector store (text search only)."""
    from sqlite_memory_api import SQLiteMemoryAPI
    db_path = str(tmp_path / "test_memory.db")
    return SQLiteMemoryAPI(db_path=db_path, vector_store=None)


@pytest.fixture
def sqlite_memory_api_with_vectors(tmp_path, mock_ollama_embeddings):
    """SQLiteMemoryAPI with a real FAISS vector store using mocked embeddings."""
    from sqlite_vector_api import FAISSVectorAPI
    from sqlite_memory_api import SQLiteMemoryAPI

    data_dir = str(tmp_path / "vector_data")

    with patch("sqlite_vector_api.OllamaEmbeddings", return_value=mock_ollama_embeddings):
        vector_store = FAISSVectorAPI(data_dir=data_dir)

    db_path = str(tmp_path / "test_memory.db")
    return SQLiteMemoryAPI(db_path=db_path, vector_store=vector_store)


@pytest.fixture
def mock_pg_cursor():
    """A mock psycopg2 cursor supporting context manager."""
    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    cursor.fetchone.return_value = None
    cursor.fetchall.return_value = []
    return cursor


@pytest.fixture
def mock_pg_connection(mock_pg_cursor):
    """A mock psycopg2 connection supporting context manager."""
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = mock_pg_cursor
    return conn
