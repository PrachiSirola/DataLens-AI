"""Query routes — natural language questions against uploaded datasets.

Phase 4: Added query routing for SQL analytics vs. knowledge retrieval (RAG).
"""

import csv
import io
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.middleware.auth import get_current_user
from app.models.dataset import QueryRequest, QueryResponse
from app.services.dataset_service import (
    get_user_table_names,
    get_user_dataset_ids,
    has_knowledge_datasets,
)
from app.services.duckdb_engine import execute_query, get_all_column_names
from app.services.ai_query_engine import ask_question
from app.services.query_router import determine_query_type, generate_knowledge_answer
from app.services.text_search_engine import hybrid_search, has_knowledge_chunks

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/query", tags=["query"])


@router.post("/", response_model=QueryResponse)
async def query_datasets(
    body: QueryRequest,
    user: dict = Depends(get_current_user),
):
    """Ask a natural language question — routed to SQL or knowledge retrieval."""
    user_id = user["id"]

    # Resolve which tables / datasets to query
    table_names = await get_user_table_names(user_id, body.dataset_ids)
    dataset_ids = await get_user_dataset_ids(user_id, body.dataset_ids)

    has_tabular = bool(table_names)
    has_knowledge = has_knowledge_chunks(user_id, dataset_ids)

    if not has_tabular and not has_knowledge:
        raise HTTPException(
            status_code=400,
            detail="No datasets available. Upload a CSV, PDF, or DOCX file first.",
        )

    # Get column names for schema-aware routing (lightweight call)
    column_names = get_all_column_names(user_id, table_names) if has_tabular else []

    # Route the question
    query_type = determine_query_type(
        body.question, has_tabular, has_knowledge,
        column_names=column_names, table_names=table_names,
    )
    logger.info(f"Query routed as '{query_type}': {body.question[:80]}")

    if query_type == "sql":
        return await _handle_sql(user_id, body.question, table_names)

    elif query_type == "knowledge":
        return await _handle_knowledge(user_id, body.question, dataset_ids)

    else:  # hybrid
        return await _handle_hybrid(user_id, body.question, table_names, dataset_ids)


async def _handle_sql(user_id: str, question: str, table_names: list[str]) -> QueryResponse:
    """SQL analytics path — existing NL→SQL pipeline."""
    result = await ask_question(
        user_id=user_id,
        question=question,
        table_names=table_names,
        conversation_history=None,
    )
    return QueryResponse(**result)


async def _handle_knowledge(user_id: str, question: str, dataset_ids: list[str]) -> QueryResponse:
    """Knowledge retrieval path — RAG with hybrid FTS + embeddings."""
    chunks = await hybrid_search(user_id, question, dataset_ids, top_k=5)
    result = await generate_knowledge_answer(question, chunks)

    return QueryResponse(
        question=question,
        sql_generated=None,
        answer=result["answer"],
        data=None,
        chart_config=None,
        row_count=None,
        query_type="knowledge",
        sources=result.get("sources"),
    )


async def _handle_hybrid(
    user_id: str,
    question: str,
    table_names: list[str],
    dataset_ids: list[str],
) -> QueryResponse:
    """
    Hybrid path — try SQL first; if it returns 0 results or errors,
    fall back to knowledge retrieval.
    """
    # Try SQL first
    sql_result = await ask_question(
        user_id=user_id,
        question=question,
        table_names=table_names,
        conversation_history=None,
    )

    sql_has_data = (
        sql_result.get("data")
        and len(sql_result["data"]) > 0
        and sql_result.get("row_count", 0) > 0
    )

    if sql_has_data:
        # SQL worked — return SQL result
        return QueryResponse(**sql_result)

    # SQL empty or failed — try knowledge retrieval
    chunks = await hybrid_search(user_id, question, dataset_ids, top_k=5)
    if chunks:
        knowledge_result = await generate_knowledge_answer(question, chunks)
        return QueryResponse(
            question=question,
            sql_generated=sql_result.get("sql_generated"),
            answer=knowledge_result["answer"],
            data=None,
            chart_config=None,
            row_count=None,
            query_type="hybrid",
            sources=knowledge_result.get("sources"),
        )

    # Neither worked — return whatever SQL gave us
    return QueryResponse(**sql_result)


@router.get("/tables")
async def list_tables(user: dict = Depends(get_current_user)):
    """List DuckDB tables available to the user."""
    from app.services.duckdb_engine import list_tables as _list_tables

    tables = _list_tables(user["id"])
    return {"tables": tables}


@router.get("/export")
async def export_query_csv(
    sql: str = Query(..., min_length=1, description="SQL query to export"),
    user: dict = Depends(get_current_user),
):
    """Execute a SQL query and return the results as a downloadable CSV."""
    user_id = user["id"]
    try:
        rows, total = execute_query(user_id, sql)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not rows:
        raise HTTPException(status_code=404, detail="Query returned no results.")

    # Build CSV in memory
    buf = io.StringIO()
    cols = list(rows[0].keys())
    writer = csv.DictWriter(buf, fieldnames=cols)
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=export.csv"},
    )
