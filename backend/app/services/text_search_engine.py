"""
Text Search Engine — Phase 4 of DataLensAI.

Hybrid retrieval for knowledge documents (prose PDFs, DOCX, etc.).
Combines DuckDB FTS (BM25 keyword search) with Ollama embeddings
(semantic similarity), merged via Reciprocal Rank Fusion.

Storage:
    Each user gets a separate DuckDB file (``{user_id}_knowledge.duckdb``)
    that holds text chunks + precomputed embeddings.
"""

import json
import logging
from pathlib import Path

import duckdb
import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_DUCKDB_DIR = Path("data/duckdb")

# Reciprocal Rank Fusion constant
RRF_K = 60


# ── Storage helpers ──────────────────────────────────────────────────

def _knowledge_db_path(user_id: str) -> Path:
    _DUCKDB_DIR.mkdir(parents=True, exist_ok=True)
    return _DUCKDB_DIR / f"{user_id}_knowledge.duckdb"


def _ensure_tables(con: duckdb.DuckDBPyConnection):
    """Create the chunks table if it doesn't exist."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id          INTEGER PRIMARY KEY,
            dataset_id  VARCHAR NOT NULL,
            content     VARCHAR NOT NULL,
            page_num    INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            embedding   VARCHAR          -- JSON array of floats
        )
    """)


def store_chunks(
    user_id: str,
    dataset_id: str,
    chunks: list,  # list[TextChunk]
    embeddings: list[list[float]] | None = None,
):
    """
    Persist text chunks (with optional embeddings) into the knowledge DB.
    """
    db_path = _knowledge_db_path(user_id)
    con = duckdb.connect(str(db_path))
    try:
        _ensure_tables(con)

        # Get next id
        max_id = con.execute(
            "SELECT COALESCE(MAX(id), 0) FROM chunks"
        ).fetchone()[0]

        for i, chunk in enumerate(chunks):
            emb_json = None
            if embeddings and i < len(embeddings):
                emb_json = json.dumps(embeddings[i])

            max_id += 1
            con.execute(
                "INSERT INTO chunks (id, dataset_id, content, page_num, chunk_index, embedding) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [max_id, dataset_id, chunk.content, chunk.page_num, chunk.chunk_index, emb_json],
            )

        # (Re-)build FTS index after insert
        _rebuild_fts(con)
        logger.info(f"Stored {len(chunks)} chunks for dataset {dataset_id}")
    finally:
        con.close()


def delete_chunks(user_id: str, dataset_id: str):
    """Remove all chunks for a specific dataset."""
    db_path = _knowledge_db_path(user_id)
    if not db_path.exists():
        return
    con = duckdb.connect(str(db_path))
    try:
        _ensure_tables(con)
        con.execute("DELETE FROM chunks WHERE dataset_id = ?", [dataset_id])
        _rebuild_fts(con)
    finally:
        con.close()


def has_knowledge_chunks(user_id: str, dataset_ids: list[str] | None = None) -> bool:
    """Check whether the user has any text chunks indexed."""
    db_path = _knowledge_db_path(user_id)
    if not db_path.exists():
        return False
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        if dataset_ids:
            placeholders = ", ".join("?" for _ in dataset_ids)
            row = con.execute(
                f"SELECT COUNT(*) FROM chunks WHERE dataset_id IN ({placeholders})",
                dataset_ids,
            ).fetchone()
        else:
            row = con.execute("SELECT COUNT(*) FROM chunks").fetchone()
        return (row[0] or 0) > 0
    except duckdb.Error:
        return False
    finally:
        con.close()


def get_chunk_count(user_id: str, dataset_id: str) -> int:
    """Return the number of chunks for a specific dataset."""
    db_path = _knowledge_db_path(user_id)
    if not db_path.exists():
        return 0
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute(
            "SELECT COUNT(*) FROM chunks WHERE dataset_id = ?", [dataset_id]
        ).fetchone()
        return row[0] or 0
    except duckdb.Error:
        return 0
    finally:
        con.close()


# ── FTS helpers ──────────────────────────────────────────────────────

def _rebuild_fts(con: duckdb.DuckDBPyConnection):
    """Drop and rebuild the FTS index on the chunks table."""
    try:
        con.execute("INSTALL fts; LOAD fts;")
    except duckdb.Error:
        try:
            con.execute("LOAD fts;")
        except duckdb.Error:
            logger.warning("FTS extension not available — keyword search will use LIKE fallback")
            return

    try:
        con.execute("PRAGMA drop_fts_index('chunks')")
    except duckdb.Error:
        pass  # no existing index

    try:
        con.execute(
            "PRAGMA create_fts_index('chunks', 'id', 'content', overwrite=1)"
        )
    except duckdb.Error as e:
        logger.warning(f"FTS index creation failed: {e}")


# ── Embedding via Ollama ─────────────────────────────────────────────

async def _get_embedding(text: str) -> list[float] | None:
    """Get embedding vector from Ollama for a single text.

    Uses a short timeout (5 s) so the query path fails fast when the
    embedding model isn't loaded instead of blocking the laptop.
    """
    url = f"{settings.ollama_base_url}/api/embed"
    payload = {
        "model": settings.ollama_embed_model,
        "input": text,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            embeddings = data.get("embeddings")
            if embeddings and len(embeddings) > 0:
                return embeddings[0]
            return None
    except Exception as e:
        logger.warning(f"Embedding call failed: {e}")
        return None


async def get_embeddings_batch(texts: list[str]) -> list[list[float] | None]:
    """
    Get embeddings for a batch of texts in a SINGLE Ollama call.
    The /api/embed endpoint accepts a list of strings as 'input'.
    Falls back gracefully if the model is unavailable.
    """
    if not texts:
        return []

    url = f"{settings.ollama_base_url}/api/embed"
    payload = {
        "model": settings.ollama_embed_model,
        "input": texts,  # Ollama accepts list of strings
    }
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            embeddings = data.get("embeddings", [])
            # Pad with None if fewer embeddings returned than texts
            while len(embeddings) < len(texts):
                embeddings.append(None)
            return embeddings
    except Exception as e:
        logger.warning(f"Batch embedding failed, skipping: {e}")
        return [None] * len(texts)


# ── Search functions ─────────────────────────────────────────────────

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _fts_search(
    con: duckdb.DuckDBPyConnection,
    query: str,
    dataset_ids: list[str] | None,
    top_k: int = 20,
) -> list[dict]:
    """Full-text search using DuckDB FTS (BM25 scoring)."""
    # Clean query for FTS
    clean_q = query.replace("'", "''").strip()
    if not clean_q:
        return []

    try:
        # Try FTS first
        sql = f"""
            SELECT id, dataset_id, content, page_num, chunk_index,
                   fts_main_chunks.match_bm25(id, '{clean_q}') AS score
            FROM chunks
            WHERE score IS NOT NULL
        """
        if dataset_ids:
            placeholders = ", ".join(f"'{d}'" for d in dataset_ids)
            sql += f" AND dataset_id IN ({placeholders})"
        sql += f" ORDER BY score DESC LIMIT {top_k}"

        rows = con.execute(sql).fetchall()
        return [
            {
                "id": r[0], "dataset_id": r[1], "content": r[2],
                "page_num": r[3], "chunk_index": r[4], "score": r[5],
            }
            for r in rows
        ]
    except duckdb.Error:
        # Fallback: LIKE-based search
        return _like_search(con, query, dataset_ids, top_k)


def _like_search(
    con: duckdb.DuckDBPyConnection,
    query: str,
    dataset_ids: list[str] | None,
    top_k: int = 20,
) -> list[dict]:
    """Fallback keyword search using LIKE."""
    words = [w.strip() for w in query.lower().split() if len(w.strip()) > 2]
    if not words:
        return []

    conditions = " OR ".join(f"LOWER(content) LIKE '%{w}%'" for w in words[:8])
    sql = f"SELECT id, dataset_id, content, page_num, chunk_index FROM chunks WHERE ({conditions})"
    if dataset_ids:
        placeholders = ", ".join(f"'{d}'" for d in dataset_ids)
        sql += f" AND dataset_id IN ({placeholders})"
    sql += f" LIMIT {top_k}"

    try:
        rows = con.execute(sql).fetchall()
        return [
            {
                "id": r[0], "dataset_id": r[1], "content": r[2],
                "page_num": r[3], "chunk_index": r[4], "score": 1.0,
            }
            for r in rows
        ]
    except duckdb.Error:
        return []


def _has_stored_embeddings(
    con: duckdb.DuckDBPyConnection,
    dataset_ids: list[str] | None,
) -> bool:
    """Quick check: do any chunks have precomputed embeddings?"""
    sql = "SELECT 1 FROM chunks WHERE embedding IS NOT NULL"
    if dataset_ids:
        placeholders = ", ".join(f"'{d}'" for d in dataset_ids)
        sql += f" AND dataset_id IN ({placeholders})"
    sql += " LIMIT 1"
    try:
        return len(con.execute(sql).fetchall()) > 0
    except duckdb.Error:
        return False


async def _embedding_search(
    con: duckdb.DuckDBPyConnection,
    query: str,
    dataset_ids: list[str] | None,
    top_k: int = 20,
) -> list[dict]:
    """Semantic search using embedding cosine similarity.

    Skips the Ollama call entirely when no stored embeddings exist,
    which avoids loading the embedding model and freezing on low-RAM
    machines.
    """
    # ── Fast exit: nothing to compare against ────────────────────
    if not _has_stored_embeddings(con, dataset_ids):
        return []

    query_emb = await _get_embedding(query)
    if not query_emb:
        return []

    # Fetch all chunks with embeddings
    sql = "SELECT id, dataset_id, content, page_num, chunk_index, embedding FROM chunks WHERE embedding IS NOT NULL"
    if dataset_ids:
        placeholders = ", ".join(f"'{d}'" for d in dataset_ids)
        sql += f" AND dataset_id IN ({placeholders})"

    try:
        rows = con.execute(sql).fetchall()
    except duckdb.Error:
        return []

    # Compute similarities
    scored = []
    for r in rows:
        try:
            stored_emb = json.loads(r[5])
            sim = _cosine_similarity(query_emb, stored_emb)
            scored.append({
                "id": r[0], "dataset_id": r[1], "content": r[2],
                "page_num": r[3], "chunk_index": r[4], "score": sim,
            })
        except (json.JSONDecodeError, TypeError):
            continue

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


# ── Hybrid search with RRF ───────────────────────────────────────────

async def hybrid_search(
    user_id: str,
    query: str,
    dataset_ids: list[str] | None = None,
    top_k: int = 5,
) -> list[dict]:
    """
    Combine FTS (keyword / BM25) and embedding (semantic) results
    using Reciprocal Rank Fusion.

    Returns top-k chunks ordered by combined relevance.
    """
    db_path = _knowledge_db_path(user_id)
    if not db_path.exists():
        return []

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        # Load FTS extension
        try:
            con.execute("LOAD fts;")
        except duckdb.Error:
            pass

        # Run both searches
        fts_results = _fts_search(con, query, dataset_ids)
        emb_results = await _embedding_search(con, query, dataset_ids)

        if not fts_results and not emb_results:
            return []

        # RRF fusion
        rrf_scores: dict[int, float] = {}  # chunk id → score
        chunk_data: dict[int, dict] = {}

        for rank, item in enumerate(fts_results):
            cid = item["id"]
            rrf_scores[cid] = rrf_scores.get(cid, 0) + 1.0 / (RRF_K + rank + 1)
            chunk_data[cid] = item

        for rank, item in enumerate(emb_results):
            cid = item["id"]
            rrf_scores[cid] = rrf_scores.get(cid, 0) + 1.0 / (RRF_K + rank + 1)
            chunk_data[cid] = item

        # Sort by combined RRF score
        sorted_ids = sorted(rrf_scores, key=rrf_scores.get, reverse=True)

        results = []
        for cid in sorted_ids[:top_k]:
            entry = chunk_data[cid].copy()
            entry["rrf_score"] = rrf_scores[cid]
            results.append(entry)

        return results
    finally:
        con.close()
