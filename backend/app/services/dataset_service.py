"""
Dataset management service — orchestrates upload, processing, and storage.

Phase 4: Added dual-indexing for PDFs/DOCX:
  - Tabular path (DuckDB tables) for structured data
  - Knowledge path (text chunks + embeddings) for prose documents
  - Auto-detection of content type
"""

import logging
from datetime import datetime, timezone

from bson import ObjectId

from app.config import settings
from app.database import get_db
from app.services.file_processor import process_file, FileProcessingError
from app.services.duckdb_engine import register_dataframe, drop_table
from app.services.document_processor import (
    extract_text_from_pdf,
    extract_text_from_docx,
    chunk_pages,
    is_tabular_content,
)
from app.services.text_search_engine import (
    store_chunks,
    delete_chunks,
    get_chunk_count,
)

logger = logging.getLogger(__name__)


ALLOWED_TYPES = {"csv", "xlsx", "xls"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


def _get_file_type(filename: str) -> str:
    """Extract and validate file type from filename."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "doc":
        ext = "docx"  # Treat .doc as .docx (best effort)
    if ext not in ALLOWED_TYPES:
        raise ValueError(
            f"Unsupported file type '.{ext}'. Allowed: {', '.join(sorted(ALLOWED_TYPES))}"
        )
    return ext


async def upload_dataset(
    user_id: str,
    filename: str,
    file_bytes: bytes,
) -> dict:
    """
    Process an uploaded file and register it in DuckDB and/or knowledge store.

    Flow:
      CSV/Excel → always tabular (DuckDB table)
      PDF/DOCX  → auto-detect:
        - If DataFrame has good structure → tabular + knowledge (mixed)
        - If DataFrame is poor/empty     → knowledge only
    """
    db = get_db()

    # Validate
    file_type = _get_file_type(filename)
    if len(file_bytes) > MAX_FILE_SIZE:
        raise ValueError(f"File too large. Maximum size: {MAX_FILE_SIZE // (1024*1024)} MB.")
    if len(file_bytes) == 0:
        raise ValueError("File is empty.")

    # Create dataset record (status: processing)
    now = datetime.now(timezone.utc)
    dataset_doc = {
        "user_id": ObjectId(user_id),
        "original_filename": filename,
        "file_type": file_type,
        "file_size": len(file_bytes),
        "row_count": None,
        "column_count": None,
        "columns": [],
        "table_name": "",
        "content_type": "tabular",
        "chunk_count": None,
        "status": "processing",
        "error_message": None,
        "uploaded_at": now,
    }
    result = await db.datasets.insert_one(dataset_doc)
    dataset_id = str(result.inserted_id)

    try:
        # Save raw file early (needed for re-processing)
        user_upload_dir = settings.uploads_dir / user_id
        user_upload_dir.mkdir(parents=True, exist_ok=True)
        raw_path = user_upload_dir / f"{dataset_id}.{file_type}"
        raw_path.write_bytes(file_bytes)

        # ── Route by file type ───────────────────────────────────
        if file_type in ("csv", "xlsx", "xls"):
            # Always tabular
            await _process_tabular(user_id, dataset_id, filename, file_bytes, file_type, dataset_doc)
        else:
            # PDF or DOCX → auto-detect
            await _process_document(user_id, dataset_id, filename, file_bytes, file_type, dataset_doc)

        # Update dataset record → ready
        await db.datasets.update_one(
            {"_id": result.inserted_id},
            {"$set": {
                "table_name": dataset_doc.get("table_name", ""),
                "row_count": dataset_doc.get("row_count"),
                "column_count": dataset_doc.get("column_count"),
                "columns": dataset_doc.get("columns", []),
                "content_type": dataset_doc.get("content_type", "tabular"),
                "chunk_count": dataset_doc.get("chunk_count"),
                "status": "ready",
            }},
        )
        dataset_doc["_id"] = result.inserted_id
        dataset_doc["status"] = "ready"

        logger.info(
            f"Dataset uploaded: {filename} → type={dataset_doc['content_type']} "
            f"(table={dataset_doc.get('table_name', 'none')}, "
            f"chunks={dataset_doc.get('chunk_count', 0)})"
        )

    except (FileProcessingError, Exception) as e:
        error_msg = str(e)
        await db.datasets.update_one(
            {"_id": result.inserted_id},
            {"$set": {"status": "error", "error_message": error_msg}},
        )
        dataset_doc.update({
            "_id": result.inserted_id,
            "status": "error",
            "error_message": error_msg,
        })
        logger.error(f"Dataset processing failed: {filename} — {error_msg}")

    return _serialize(dataset_doc)


async def _process_tabular(
    user_id: str,
    dataset_id: str,
    filename: str,
    file_bytes: bytes,
    file_type: str,
    doc: dict,
):
    """Process a CSV/Excel file as pure tabular data."""
    df = process_file(file_bytes, filename, file_type)
    table_name, row_count, col_count, columns = register_dataframe(
        user_id=user_id,
        dataset_id=dataset_id,
        filename=filename,
        df=df,
    )
    doc.update({
        "table_name": table_name,
        "row_count": row_count,
        "column_count": col_count,
        "columns": columns,
        "content_type": "tabular",
    })


async def _process_document(
    user_id: str,
    dataset_id: str,
    filename: str,
    file_bytes: bytes,
    file_type: str,
    doc: dict,
):
    """
    Process a PDF or DOCX with auto-detection:
      - Try tabular extraction first
      - Always extract text for knowledge chunks
      - Set content_type to 'mixed' (both) or 'knowledge' (text only)
    """
    # ── Step 1: Try tabular extraction ────────────────────────
    df = None
    tabular_ok = False
    try:
        df = process_file(file_bytes, filename, file_type)
        tabular_ok = is_tabular_content(df, file_type)
    except (FileProcessingError, Exception) as e:
        logger.info(f"Tabular extraction failed for {filename}: {e}")

    if tabular_ok and df is not None and not df.empty:
        table_name, row_count, col_count, columns = register_dataframe(
            user_id=user_id,
            dataset_id=dataset_id,
            filename=filename,
            df=df,
        )
        doc.update({
            "table_name": table_name,
            "row_count": row_count,
            "column_count": col_count,
            "columns": columns,
        })

    # ── Step 2: Extract text for knowledge / RAG ──────────────
    if file_type == "pdf":
        pages = extract_text_from_pdf(file_bytes)
    elif file_type == "docx":
        pages = extract_text_from_docx(file_bytes)
    else:
        pages = []

    if pages:
        chunks = chunk_pages(pages)
        if chunks:

            store_chunks(user_id, dataset_id, chunks, embeddings=None)
            doc["chunk_count"] = len(chunks)

    # ── Step 3: Set content type ──────────────────────────────
    has_table = tabular_ok and doc.get("table_name")
    has_chunks = doc.get("chunk_count", 0) > 0

    if has_table and has_chunks:
        doc["content_type"] = "mixed"
    elif has_chunks:
        doc["content_type"] = "knowledge"
    else:
        doc["content_type"] = "tabular"  # fallback even if empty


async def list_user_datasets(user_id: str) -> list[dict]:
    """List all datasets for a user."""
    db = get_db()
    cursor = db.datasets.find(
        {"user_id": ObjectId(user_id)}
    ).sort("uploaded_at", -1)
    docs = await cursor.to_list(length=200)
    return [_serialize(d) for d in docs]


async def get_dataset(user_id: str, dataset_id: str) -> dict | None:
    """Get a single dataset by ID, only if it belongs to the user."""
    db = get_db()
    doc = await db.datasets.find_one({
        "_id": ObjectId(dataset_id),
        "user_id": ObjectId(user_id),
    })
    return _serialize(doc) if doc else None


async def delete_dataset(user_id: str, dataset_id: str) -> bool:
    """Delete a dataset, its DuckDB table, knowledge chunks, and raw file."""
    db = get_db()
    doc = await db.datasets.find_one({
        "_id": ObjectId(dataset_id),
        "user_id": ObjectId(user_id),
    })
    if not doc:
        return False

    # Remove DuckDB table
    if doc.get("table_name"):
        drop_table(user_id, doc["table_name"])

    # Remove knowledge chunks
    delete_chunks(user_id, dataset_id)

    # Remove raw file
    raw_path = settings.uploads_dir / user_id / f"{dataset_id}.{doc['file_type']}"
    if raw_path.exists():
        raw_path.unlink()

    # Remove MongoDB record
    await db.datasets.delete_one({"_id": doc["_id"]})

    logger.info(f"Dataset deleted: {doc['original_filename']} (user {user_id})")
    return True


async def get_user_table_names(user_id: str, dataset_ids: list[str] | None = None) -> list[str]:
    """Get DuckDB table names for a user's datasets."""
    db = get_db()
    query = {"user_id": ObjectId(user_id), "status": "ready"}
    if dataset_ids:
        query["_id"] = {"$in": [ObjectId(d) for d in dataset_ids]}
    cursor = db.datasets.find(query, {"table_name": 1})
    docs = await cursor.to_list(length=200)
    return [d["table_name"] for d in docs if d.get("table_name")]


async def get_user_dataset_ids(user_id: str, dataset_ids: list[str] | None = None) -> list[str]:
    """Get dataset IDs for a user's ready datasets."""
    db = get_db()
    query = {"user_id": ObjectId(user_id), "status": "ready"}
    if dataset_ids:
        query["_id"] = {"$in": [ObjectId(d) for d in dataset_ids]}
    cursor = db.datasets.find(query, {"_id": 1})
    docs = await cursor.to_list(length=200)
    return [str(d["_id"]) for d in docs]


async def has_knowledge_datasets(user_id: str, dataset_ids: list[str] | None = None) -> bool:
    """Check if user has any knowledge or mixed content datasets."""
    db = get_db()
    query = {
        "user_id": ObjectId(user_id),
        "status": "ready",
        "content_type": {"$in": ["knowledge", "mixed"]},
    }
    if dataset_ids:
        query["_id"] = {"$in": [ObjectId(d) for d in dataset_ids]}
    count = await db.datasets.count_documents(query)
    return count > 0


def _serialize(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "original_filename": doc["original_filename"],
        "file_type": doc["file_type"],
        "file_size": doc["file_size"],
        "row_count": doc.get("row_count"),
        "column_count": doc.get("column_count"),
        "columns": doc.get("columns", []),
        "table_name": doc.get("table_name", ""),
        "content_type": doc.get("content_type", "tabular"),
        "chunk_count": doc.get("chunk_count"),
        "status": doc.get("status", "processing"),
        "error_message": doc.get("error_message"),
        "uploaded_at": doc["uploaded_at"],
    }
