"""Tests for OllamaEmbeddings client."""
import pytest
from unittest.mock import patch, MagicMock
from ollama_embeddings import OllamaEmbeddings


@pytest.fixture
def embeddings():
    return OllamaEmbeddings(model_name="test-model", base_url="http://fake:11434")


@pytest.fixture
def fake_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"embedding": [0.1] * 768}
    resp.raise_for_status.return_value = None
    return resp


@pytest.mark.unit
class TestGetEmbedding:
    def test_success(self, embeddings, fake_response):
        with patch.object(embeddings.session, "post", return_value=fake_response):
            result = embeddings.get_embedding("hello")
        assert len(result) == 768
        assert result[0] == 0.1

    def test_sends_correct_payload(self, embeddings, fake_response):
        with patch.object(embeddings.session, "post", return_value=fake_response) as mock_post:
            embeddings.get_embedding("test text")
        call_kwargs = mock_post.call_args
        assert call_kwargs[1]["json"]["model"] == "test-model"
        assert call_kwargs[1]["json"]["prompt"] == "test text"
        assert call_kwargs[1]["json"]["keep_alive"] == "10m"

    def test_custom_url(self):
        emb = OllamaEmbeddings(base_url="http://custom:9999")
        assert emb.api_url == "http://custom:9999/api/embeddings"

    def test_caching(self, embeddings, fake_response):
        with patch.object(embeddings.session, "post", return_value=fake_response) as mock_post:
            result1 = embeddings.get_embedding("same text")
            result2 = embeddings.get_embedding("same text")
        assert mock_post.call_count == 1
        assert result1 == result2

    def test_cache_eviction(self, embeddings, fake_response):
        embeddings._cache_max_size = 2
        with patch.object(embeddings.session, "post", return_value=fake_response):
            embeddings.get_embedding("text_a")
            embeddings.get_embedding("text_b")
            embeddings.get_embedding("text_c")
        assert len(embeddings._embedding_cache) == 2
        # First entry should have been evicted
        assert hash("text_a") not in embeddings._embedding_cache

    def test_http_error_returns_zero_vector(self, embeddings):
        error_resp = MagicMock()
        error_resp.raise_for_status.side_effect = Exception("500 Server Error")
        with patch.object(embeddings.session, "post", return_value=error_resp):
            result = embeddings.get_embedding("fail")
        assert result == [0.0] * 768

    def test_connection_error_returns_zero_vector(self, embeddings):
        import requests
        with patch.object(embeddings.session, "post", side_effect=requests.ConnectionError("refused")):
            result = embeddings.get_embedding("fail")
        assert result == [0.0] * 768

    def test_timeout_returns_zero_vector(self, embeddings):
        import requests
        with patch.object(embeddings.session, "post", side_effect=requests.Timeout("timeout")):
            result = embeddings.get_embedding("fail")
        assert result == [0.0] * 768


@pytest.mark.unit
class TestGetEmbeddings:
    def test_multiple_texts(self, embeddings, fake_response):
        with patch.object(embeddings.session, "post", return_value=fake_response) as mock_post:
            results = embeddings.get_embeddings(["a", "b", "c"])
        assert len(results) == 3
        assert mock_post.call_count == 3
        for r in results:
            assert len(r) == 768


@pytest.mark.unit
class TestSessionCleanup:
    def test_del_closes_session(self, embeddings):
        session_mock = MagicMock()
        embeddings.session = session_mock
        embeddings.__del__()
        session_mock.close.assert_called_once()
