"""Dataset-related Pydantic models (request/response schemas)."""

from datetime import datetime
from pydantic import BaseModel, Field


class DatasetPublic(BaseModel):
    """Public representation of an uploaded dataset."""
    id: str
    original_filename: str
    file_type: str  # csv, pdf, docx
    file_size: int  # bytes
    row_count: int | None = None
    column_count: int | None = None
    columns: list[str] = []
    table_name: str  # DuckDB table name
    content_type: str = "tabular"  # tabular, knowledge, mixed
    chunk_count: int | None = None  # number of text chunks (knowledge/mixed)
    status: str  # processing, ready, error
    error_message: str | None = None
    uploaded_at: datetime


class DatasetListResponse(BaseModel):
    datasets: list[DatasetPublic]
    total: int


class QueryRequest(BaseModel):
    """Natural language query against uploaded datasets."""
    question: str = Field(min_length=1, max_length=2000)
    dataset_ids: list[str] | None = Field(
        None,
        description="Specific datasets to query. If None, queries all user datasets.",
    )


class QueryResponse(BaseModel):
    question: str
    sql_generated: str | None = None
    answer: str
    data: list[dict] | None = None
    chart_config: dict | None = None
    row_count: int | None = None
    preprocessed: str | None = None  # cleaned query (shown only if different from original)
    query_type: str | None = None  # sql, knowledge, hybrid
    sources: list[dict] | None = None  # page references for knowledge answers


class ConversationEntry(BaseModel):
    role: str  # user or assistant
    content: str
    sql: str | None = None
    data: list[dict] | None = None
    timestamp: datetime
