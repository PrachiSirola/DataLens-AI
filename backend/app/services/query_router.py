"""
Query Router — Phase 4 of DataLensAI.

Decides whether a user question should be answered via:
  - SQL analytics (tabular data in DuckDB)
  - Knowledge retrieval / RAG (prose chunks from PDFs, DOCX)
  - Hybrid (try both paths and combine)

Also builds the RAG prompt that injects retrieved passages into the
LLM context for grounded answer generation.
"""

import json
import logging
import re

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# ── Signal words for routing ─────────────────────────────────────────

# Domain-neutral SQL operation signals — no dataset-specific terms.
# Schema-aware dynamic matching is added at routing time via
# _schema_signal_boost().
DATA_SIGNALS = {
    "how many", "count", "total", "sum", "average", "avg", "mean",
    "minimum", "maximum", "highest", "lowest", "top", "bottom",
    "percentage", "percent", "ratio", "compare", "trend", "group by",
    "per", "each", "breakdown", "filter", "where", "between",
    "greater than", "less than", "sort", "rank", "distinct",
    "unique", "first", "last", "recent", "show me the data",
    "export", "list all", "show all", "rows", "columns",
    "today", "yesterday", "this week", "this month",
}

KNOWLEDGE_SIGNALS = {
    "what is", "what are", "explain", "describe", "define",
    "why", "how does", "how do", "how can", "how to",
    "tell me about", "summarize", "summary", "overview",
    "meaning of", "difference between", "pros and cons",
    "advantages", "disadvantages", "benefits", "drawbacks",
    "example of", "types of", "steps to", "causes of",
    "impact of", "effect of", "role of", "purpose of",
    "importance of", "features of", "characteristics",
    "concept", "theory", "principle", "according to",
    "vulnerability", "attack", "threat", "security",
    "mitigation", "prevention", "recommendation",
    "best practice", "guideline",
}


# ── Query type determination ─────────────────────────────────────────

def determine_query_type(
    question: str,
    has_tabular: bool,
    has_knowledge: bool,
    column_names: list[str] | None = None,
    table_names: list[str] | None = None,
) -> str:
    """
    Classify the question as 'sql', 'knowledge', or 'hybrid'.

    Parameters
    ----------
    question : str
        The user's natural-language question.
    has_tabular : bool
        Whether the user has any tabular datasets (DuckDB tables).
    has_knowledge : bool
        Whether the user has any knowledge chunks indexed.
    column_names : list[str] | None
        Column names from the user's schema (for dynamic boost).
    table_names : list[str] | None
        Table names from the user's DuckDB (for dynamic boost).

    Returns
    -------
    str
        One of 'sql', 'knowledge', 'hybrid'.
    """
    if not has_tabular and not has_knowledge:
        return "sql"  # will produce "no datasets" error naturally

    if has_tabular and not has_knowledge:
        return "sql"

    if has_knowledge and not has_tabular:
        return "knowledge"

    # Both available → score by signal words
    q = question.lower().strip()

    data_score = sum(1 for s in DATA_SIGNALS if s in q)
    knowledge_score = sum(1 for s in KNOWLEDGE_SIGNALS if s in q)

    # Dynamic schema-aware boost — if query words match actual column/table
    # names in the user's data, boost toward SQL regardless of domain
    if column_names or table_names:
        data_score += schema_signal_boost(
            question,
            column_names or [],
            table_names or [],
        )

    # Strong knowledge signals
    if knowledge_score >= 2 and data_score == 0:
        return "knowledge"

    # Strong data signals
    if data_score >= 2 and knowledge_score == 0:
        return "sql"

    # Mixed or ambiguous → hybrid
    if knowledge_score > 0 and data_score > 0:
        return "hybrid"

    # Default: if question starts with interrogative patterns → knowledge
    interrogative = re.match(
        r"^(what|why|how|explain|describe|define|tell me|summarize|overview)",
        q,
    )
    if interrogative and has_knowledge:
        return "knowledge"

    # Default to SQL if tabular data exists
    return "sql" if has_tabular else "knowledge"


# ── RAG prompt construction ──────────────────────────────────────────

KNOWLEDGE_SYSTEM_PROMPT = """You are a knowledgeable assistant answering questions based on the provided document excerpts.

RULES:
1. Answer ONLY based on the information in the PASSAGES below. Do NOT use your own knowledge.
2. If the passages don't contain enough information to answer, say so clearly.
3. Cite the page number(s) where you found the information using [Page X] format.
4. Be thorough but concise. Include specific details from the passages.
5. If multiple passages cover the topic, synthesize them into a coherent answer.
6. Do NOT make up information that isn't in the passages.

PASSAGES:
{passages}"""


def format_passages_for_llm(chunks: list[dict]) -> str:
    """
    Format retrieved chunks into a structured passage block
    for the LLM prompt.
    """
    if not chunks:
        return "(No relevant passages found)"

    parts = []
    for i, chunk in enumerate(chunks, 1):
        page = chunk.get("page_num", "?")
        content = chunk.get("content", "").strip()
        parts.append(f"[Passage {i} — Page {page}]\n{content}")

    return "\n\n---\n\n".join(parts)


def build_knowledge_prompt(question: str, chunks: list[dict]) -> list[dict]:
    """
    Build the full chat messages for a knowledge / RAG query.
    Injects retrieved passages into the system prompt.
    """
    passages = format_passages_for_llm(chunks)
    system = KNOWLEDGE_SYSTEM_PROMPT.format(passages=passages)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]


# ── RAG answer generation ────────────────────────────────────────────

async def generate_knowledge_answer(
    question: str,
    chunks: list[dict],
) -> dict:
    """
    Generate a grounded answer from retrieved text chunks using the LLM.
    This is the Generation step of RAG.

    Returns
    -------
    dict
        {"answer": str, "sources": list[dict], "query_type": "knowledge"}
    """
    if not chunks:
        return {
            "answer": "I couldn't find relevant information in the uploaded documents to answer this question.",
            "sources": [],
            "query_type": "knowledge",
        }

    messages = build_knowledge_prompt(question, chunks)

    # Call Ollama
    answer_text = await _call_ollama_for_answer(messages)

    # Build source references
    sources = _build_sources(chunks)

    return {
        "answer": answer_text,
        "sources": sources,
        "query_type": "knowledge",
    }


def _build_sources(chunks: list[dict]) -> list[dict]:
    """Build source citation list from retrieved chunks."""
    seen_pages = set()
    sources = []
    for chunk in chunks:
        page = chunk.get("page_num", 0)
        if page in seen_pages:
            continue
        seen_pages.add(page)
        # Truncate content for preview
        preview = chunk.get("content", "")[:200].strip()
        if len(chunk.get("content", "")) > 200:
            preview += "..."
        sources.append({
            "page": page,
            "excerpt": preview,
        })
    return sources


async def _call_ollama_for_answer(messages: list[dict]) -> str:
    """Call Ollama for RAG answer generation."""
    url = f"{settings.ollama_base_url}/api/chat"
    payload = {
        "model": settings.ollama_model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.3},
    }
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data.get("message", {}).get("content", "I was unable to generate an answer.")
    except Exception as e:
        logger.error(f"Ollama RAG call failed: {e}")
        return "I encountered an error while generating the answer. Please try again."


# ── Status semantic enrichment for SQL path ──────────────────────────

# ── Data-driven categorical enrichment ──────────────────────────────
# Instead of hardcoded attendance status semantics, we detect ANY
# low-cardinality VARCHAR column and present its actual values to the
# LLM, letting it infer the meaning for whatever domain the data covers.

MAX_CATEGORICAL_CARDINALITY = 25  # columns with ≤ this many unique values


def build_status_enrichment(column_values: dict[str, list[str]]) -> str:
    """
    Build categorical-value hints for the SQL prompt.

    Scans VARCHAR columns for low-cardinality values (≤25 unique) and
    lists them so the LLM can understand categorical data in ANY domain
    (attendance statuses, order types, grade levels, product categories, etc.).
    """
    categorical_cols = _find_categorical_columns(column_values)
    if not categorical_cols:
        return ""

    hints = []
    for col_name, values in categorical_cols.items():
        val_list = ", ".join(values[:MAX_CATEGORICAL_CARDINALITY])
        hints.append(
            f'\nCATEGORICAL VALUES for column "{col_name}":\n'
            f"  Distinct values: {val_list}\n"
            f"  Use LOWER() for case-insensitive matching on this column."
        )

    return "\n".join(hints)


def _find_categorical_columns(column_values: dict[str, list[str]]) -> dict[str, list[str]]:
    """Identify columns with low cardinality (likely categorical/status data)."""
    result = {}
    for col, values in column_values.items():
        if not values:
            continue
        # Low-cardinality VARCHAR columns are likely categorical
        if len(values) <= MAX_CATEGORICAL_CARDINALITY:
            result[col] = values
    return result


def schema_signal_boost(question: str, column_names: list[str], table_names: list[str]) -> int:
    """
    Dynamic schema-aware boost for SQL routing.

    Returns a positive score if query words overlap with actual column
    or table names in the user's data, regardless of domain.
    """
    q_lower = question.lower()
    q_words = set(re.findall(r"[a-z_]+", q_lower))
    boost = 0

    for col in column_names:
        col_lower = col.lower()
        # Direct word match
        col_words = set(col_lower.replace("_", " ").split())
        if col_words & q_words:
            boost += 1
        # Substring match for compound column names
        elif col_lower in q_lower:
            boost += 1

    for table in table_names:
        table_lower = table.lower()
        table_words = set(table_lower.replace("_", " ").split())
        if table_words & q_words:
            boost += 1

    return boost
