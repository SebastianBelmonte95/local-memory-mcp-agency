"""Tests for SimpleChunker text chunking utility."""
import pytest
from sqlite_vector_api import SimpleChunker


@pytest.mark.unit
class TestChunkByParagraph:
    def test_two_paragraphs(self):
        text = ("A" * 60) + "\n\n" + ("B" * 60)
        chunks = SimpleChunker.chunk_by_paragraph(text, min_size=50)
        assert len(chunks) == 2

    def test_filters_below_min_size(self):
        text = "Short.\n\n" + ("B" * 60)
        chunks = SimpleChunker.chunk_by_paragraph(text, min_size=50)
        assert len(chunks) == 1
        assert "B" in chunks[0]

    def test_splits_long_paragraph_by_sentence(self):
        # One paragraph with multiple sentences exceeding max_size
        sentences = "This is a test sentence. " * 50  # ~1250 chars
        chunks = SimpleChunker.chunk_by_paragraph(sentences, min_size=50, max_size=200)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 200 + 50  # some tolerance for sentence boundaries

    def test_empty_text(self):
        chunks = SimpleChunker.chunk_by_paragraph("")
        assert chunks == []

    def test_single_paragraph(self):
        text = "A" * 100
        chunks = SimpleChunker.chunk_by_paragraph(text, min_size=50)
        assert len(chunks) == 1


@pytest.mark.unit
class TestChunkBySentence:
    def test_basic_sentences(self):
        text = "First sentence here. Second sentence here. Third sentence here."
        chunks = SimpleChunker.chunk_by_sentence(text, min_size=10, max_size=100)
        assert len(chunks) >= 1
        # All content should be preserved
        combined = " ".join(chunks)
        assert "First" in combined
        assert "Third" in combined

    def test_accumulates_short_sentences(self):
        text = "Hi. There. How. Are. You. Doing. Today. Fine. Thanks."
        chunks = SimpleChunker.chunk_by_sentence(text, min_size=20, max_size=100)
        # Short sentences should be grouped together
        assert len(chunks) < 9  # fewer chunks than sentences

    def test_discards_final_chunk_below_min_size(self):
        text = "A long enough first sentence that meets minimum. X."
        chunks = SimpleChunker.chunk_by_sentence(text, min_size=30, max_size=100)
        # "X." alone is below min_size=30, should be discarded
        for chunk in chunks:
            assert len(chunk) >= 30


@pytest.mark.unit
class TestChunkByFixedSize:
    def test_basic(self):
        text = "A" * 1500
        chunks = SimpleChunker.chunk_by_fixed_size(text, chunk_size=500, overlap=100)
        assert len(chunks) >= 3

    def test_short_text_single_chunk(self):
        text = "Short text"
        chunks = SimpleChunker.chunk_by_fixed_size(text, chunk_size=500)
        assert len(chunks) == 1
        assert chunks[0] == "Short text"

    def test_empty_text(self):
        chunks = SimpleChunker.chunk_by_fixed_size("")
        assert chunks == []

    def test_breaks_at_word_boundary(self):
        text = "word " * 200  # 1000 chars
        chunks = SimpleChunker.chunk_by_fixed_size(text, chunk_size=100, overlap=10)
        for chunk in chunks:
            # Should not break mid-word
            assert not chunk.startswith(" ")
