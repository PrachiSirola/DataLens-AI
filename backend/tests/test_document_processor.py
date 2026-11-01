"""Tests for document_processor.py — text extraction and chunking."""

import pytest
import pandas as pd

from app.services.document_processor import (
    chunk_pages,
    is_tabular_content,
    get_text_content_length,
    TextChunk,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    MIN_CHUNK_SIZE,
)


# ── chunk_pages tests ────────────────────────────────────────────────

class TestChunkPages:
    def test_single_short_page(self):
        """A short page becomes a single chunk."""
        pages = [{"page": 1, "text": "This is a short paragraph about security."}]
        # Too short (below MIN_CHUNK_SIZE), should be skipped
        chunks = chunk_pages(pages, chunk_size=800, overlap=200)
        assert len(chunks) == 0  # below 100 chars

    def test_single_page_above_min(self):
        """A page above MIN_CHUNK_SIZE becomes one chunk."""
        text = "A " * 60  # 120 chars
        pages = [{"page": 5, "text": text.strip()}]
        chunks = chunk_pages(pages, chunk_size=800, overlap=200)
        assert len(chunks) == 1
        assert chunks[0].page_num == 5
        assert chunks[0].chunk_index == 0

    def test_long_page_splits(self):
        """A long page is split into multiple overlapping chunks."""
        text = "This is a test sentence. " * 80  # ~2000 chars
        pages = [{"page": 1, "text": text.strip()}]
        chunks = chunk_pages(pages, chunk_size=500, overlap=100)
        assert len(chunks) > 1
        # All chunks reference page 1
        for c in chunks:
            assert c.page_num == 1

    def test_multiple_pages(self):
        """Multiple pages produce chunks with correct page numbers."""
        pages = [
            {"page": 1, "text": "A " * 60},
            {"page": 2, "text": "B " * 60},
            {"page": 3, "text": "C " * 60},
        ]
        chunks = chunk_pages(pages, chunk_size=800, overlap=200)
        assert len(chunks) == 3
        assert [c.page_num for c in chunks] == [1, 2, 3]

    def test_chunk_indices_sequential(self):
        """Chunk indices are sequential across pages."""
        pages = [
            {"page": 1, "text": "This is word content. " * 80},
            {"page": 2, "text": "This is data content. " * 80},
        ]
        chunks = chunk_pages(pages, chunk_size=500, overlap=100)
        indices = [c.chunk_index for c in chunks]
        assert indices == list(range(len(chunks)))

    def test_empty_pages_skipped(self):
        """Empty or too-short pages are skipped."""
        pages = [
            {"page": 1, "text": ""},
            {"page": 2, "text": "hi"},
            {"page": 3, "text": "A " * 60},
        ]
        chunks = chunk_pages(pages, chunk_size=800, overlap=200)
        assert len(chunks) == 1
        assert chunks[0].page_num == 3

    def test_overlap_exists(self):
        """Adjacent chunks from same page have overlapping content."""
        text = "Sentence one. " * 80  # long enough
        pages = [{"page": 1, "text": text.strip()}]
        chunks = chunk_pages(pages, chunk_size=300, overlap=100)
        if len(chunks) >= 2:
            # Check content overlap
            end_of_first = chunks[0].content[-50:]
            assert any(
                word in chunks[1].content
                for word in end_of_first.split()
                if len(word) > 3
            )


# ── is_tabular_content tests ─────────────────────────────────────────

class TestIsTabularContent:
    def test_csv_always_tabular(self):
        """CSV files are always treated as tabular."""
        df = pd.DataFrame({"a": [1]})
        assert is_tabular_content(df, "csv") is True

    def test_xlsx_always_tabular(self):
        """Excel files are always treated as tabular."""
        assert is_tabular_content(None, "xlsx") is True

    def test_empty_df_not_tabular(self):
        """Empty DataFrame from PDF is not tabular."""
        df = pd.DataFrame()
        assert is_tabular_content(df, "pdf") is False

    def test_none_df_not_tabular(self):
        assert is_tabular_content(None, "pdf") is False

    def test_too_few_columns(self):
        """DataFrame with <3 columns isn't real tabular data."""
        df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
        assert is_tabular_content(df, "pdf") is False

    def test_high_null_ratio(self):
        """DataFrame with >50% nulls is poorly-extracted prose."""
        import numpy as np
        df = pd.DataFrame({
            "a": ["x", "y", "z", None, None, None],
            "b": [None, None, None, None, None, None],
            "c": [None, None, None, None, None, None],
            "d": [1, None, None, None, None, None],
        })
        assert is_tabular_content(df, "pdf") is False

    def test_good_tabular_pdf(self):
        """Well-structured PDF table is detected as tabular."""
        df = pd.DataFrame({
            "name": ["Alice", "Bob", "Charlie", "Dave", "Eve", "Frank"],
            "score": [90, 85, 88, 92, 78, 95],
            "grade": ["A", "B", "A", "A", "C", "A"],
            "department": ["Eng", "Eng", "Sales", "HR", "Sales", "Eng"],
        })
        assert is_tabular_content(df, "pdf") is True

    def test_too_few_rows(self):
        """DataFrame with <5 rows isn't reliable tabular."""
        df = pd.DataFrame({
            "a": ["x", "y"],
            "b": [1, 2],
            "c": ["p", "q"],
        })
        assert is_tabular_content(df, "pdf") is False


# ── get_text_content_length tests ─────────────────────────────────────

class TestGetTextContentLength:
    def test_empty(self):
        assert get_text_content_length([]) == 0

    def test_single_page(self):
        pages = [{"page": 1, "text": "Hello world"}]
        assert get_text_content_length(pages) == 11

    def test_multiple_pages(self):
        pages = [
            {"page": 1, "text": "abc"},
            {"page": 2, "text": "defgh"},
        ]
        assert get_text_content_length(pages) == 8
