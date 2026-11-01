"""Tests for text_search_engine.py — chunk storage and search."""

import pytest
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
from dataclasses import dataclass

from app.services.text_search_engine import (
    store_chunks,
    delete_chunks,
    has_knowledge_chunks,
    get_chunk_count,
    _cosine_similarity,
    _knowledge_db_path,
)


@dataclass
class MockChunk:
    content: str
    page_num: int
    chunk_index: int
    start_char: int = 0
    end_char: int = 100


# ── Cosine similarity tests ──────────────────────────────────────────

class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        assert abs(_cosine_similarity(v, v) - 1.0) < 1e-6

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(_cosine_similarity(a, b)) < 1e-6

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert abs(_cosine_similarity(a, b) - (-1.0)) < 1e-6

    def test_empty_vectors(self):
        assert _cosine_similarity([], []) == 0.0

    def test_mismatched_lengths(self):
        assert _cosine_similarity([1.0], [1.0, 2.0]) == 0.0

    def test_zero_vector(self):
        assert _cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


# ── Storage tests ─────────────────────────────────────────────────────

class TestChunkStorage:
    """Tests that interact with DuckDB on disk."""

    @pytest.fixture(autouse=True)
    def setup_teardown(self, tmp_path, monkeypatch):
        """Point DuckDB dir to a temp directory."""
        monkeypatch.setattr(
            "app.services.text_search_engine._DUCKDB_DIR", tmp_path
        )
        self.user_id = "test_user_123"
        self.dataset_id = "ds_001"
        yield
        # Cleanup
        db_path = tmp_path / f"{self.user_id}_knowledge.duckdb"
        if db_path.exists():
            db_path.unlink()

    def test_store_and_count(self):
        chunks = [
            MockChunk("Denial of service is an attack", 1, 0),
            MockChunk("SQL injection exploits input validation", 2, 1),
            MockChunk("XSS allows script injection", 3, 2),
        ]
        store_chunks(self.user_id, self.dataset_id, chunks)
        assert get_chunk_count(self.user_id, self.dataset_id) == 3

    def test_has_knowledge_chunks(self):
        assert has_knowledge_chunks(self.user_id) is False
        chunks = [MockChunk("test content", 1, 0)]
        store_chunks(self.user_id, self.dataset_id, chunks)
        assert has_knowledge_chunks(self.user_id) is True

    def test_has_knowledge_filtered_by_dataset(self):
        chunks = [MockChunk("test content", 1, 0)]
        store_chunks(self.user_id, self.dataset_id, chunks)
        assert has_knowledge_chunks(self.user_id, [self.dataset_id]) is True
        assert has_knowledge_chunks(self.user_id, ["nonexistent"]) is False

    def test_delete_chunks(self):
        chunks = [MockChunk("content A", 1, 0), MockChunk("content B", 2, 1)]
        store_chunks(self.user_id, self.dataset_id, chunks)
        assert get_chunk_count(self.user_id, self.dataset_id) == 2

        delete_chunks(self.user_id, self.dataset_id)
        assert get_chunk_count(self.user_id, self.dataset_id) == 0

    def test_store_with_embeddings(self):
        chunks = [MockChunk("embedding test", 1, 0)]
        embeddings = [[0.1, 0.2, 0.3]]
        store_chunks(self.user_id, self.dataset_id, chunks, embeddings)
        assert get_chunk_count(self.user_id, self.dataset_id) == 1

    def test_multiple_datasets(self):
        """Chunks from different datasets are stored separately."""
        ds1 = "dataset_1"
        ds2 = "dataset_2"
        store_chunks(self.user_id, ds1, [MockChunk("A", 1, 0)])
        store_chunks(self.user_id, ds2, [MockChunk("B", 1, 0), MockChunk("C", 2, 1)])

        assert get_chunk_count(self.user_id, ds1) == 1
        assert get_chunk_count(self.user_id, ds2) == 2

        delete_chunks(self.user_id, ds1)
        assert get_chunk_count(self.user_id, ds1) == 0
        assert get_chunk_count(self.user_id, ds2) == 2

    def test_nonexistent_user(self):
        """No DB file → 0 chunks, no error."""
        assert has_knowledge_chunks("ghost_user") is False
        assert get_chunk_count("ghost_user", "any_ds") == 0
