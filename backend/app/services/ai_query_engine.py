"""
AI Query Engine — translates natural language questions into SQL via a
local Ollama LLM, executes deterministically against DuckDB, and formats
results. No paid API or cloud dependency.

Pipeline:
  1. Build schema context from user's DuckDB tables
  2. Ask local LLM (Ollama) to generate DuckDB-compatible SQL
  3. Validate & sanitise the SQL (block DDL, limit rows)
  4. Execute SQL in DuckDB — all numbers come from DuckDB, never the LLM
  5. Build a natural-language answer + optional chart config via LLM
  6. If LLM is unreachable, a deterministic fallback generates basic SQL
"""

import json
import logging
import re
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.services.duckdb_engine import get_table_schema, execute_query, get_column_values, detect_shared_columns, select_relevant_tables
from app.services.query_preprocessor import preprocess_query
from app.services.query_router import build_status_enrichment

logger = logging.getLogger(__name__)

# ── Prompt templates ────────────────────────────────────────────────

SQL_SYSTEM_PROMPT = """You are a DuckDB SQL expert. Given a user question and a database schema, output ONLY a single valid DuckDB SQL SELECT query. No explanations, no markdown fences, no commentary — just the SQL.

CRITICAL — EXACT TABLE NAMES (copy-paste these exactly, do NOT change any character):
{table_list}

RULES:
1. Use ONLY the exact table and column names listed below — copy them character-for-character.
2. Always double-quote table and column names: SELECT "column" FROM "table_name".
3. For date/time operations use DuckDB functions: strptime, date_part, date_trunc, etc.
4. Always end with LIMIT 500 unless the query already has a LIMIT or is an aggregation.
   For "top N" or "best N" or "highest N" questions, ALWAYS use LIMIT N (e.g., top 5 → LIMIT 5).
5. For aggregations, include meaningful column aliases.
6. Use COALESCE for nullable columns where appropriate.
7. If the question cannot be answered from the available tables, respond with exactly:
   -- CANNOT_ANSWER: <short reason>
8. CROSS-TABLE QUERIES: When multiple tables exist and the question asks about a person or entity, search ALL relevant tables. Use UNION ALL to combine rows from tables with the same columns, or JOIN tables on shared identifier columns. Never limit the search to just one table when the entity might appear in others.
9. When combining data from multiple tables, include a source column to show which table/file each row came from: SELECT *, 'table_name' AS source FROM ...
10. For name/text matching, use case-insensitive comparison: WHERE LOWER("column") LIKE LOWER('%value%').
11. For categorical/status filtering, use LOWER() for case-insensitive matching: WHERE LOWER("<col>") = 'value'. Always include the filter condition.
12. For date filtering, use DuckDB's CAST or direct date literals: WHERE "date_col" BETWEEN '2024-01-01' AND '2024-01-31'. When the user says "today", "yesterday", "this week", "last month", etc., use the resolved dates provided in DATE CONTEXT below.
13. Study the SCHEMA section carefully — column names and sample data tell you what the dataset is about. Map the user's question to the actual columns and values, even if the domain is unfamiliar.
14. DATE-LIKE TEXT COLUMNS: Some VARCHAR columns store date strings (marked ⚠ DATE-LIKE in the schema). When comparing these with CURRENT_DATE(), date literals, or date functions, ALWAYS wrap them with TRY_CAST("column" AS DATE). TRY_CAST safely returns NULL for empty or invalid values instead of throwing an error. Add a NOT NULL check to exclude unparseable rows: WHERE TRY_CAST("col" AS DATE) < '2024-12-01' AND TRY_CAST("col" AS DATE) IS NOT NULL.

FEW-SHOT EXAMPLES — these show SQL PATTERNS only. NEVER copy table names from examples.
ALWAYS replace table/column names with the EXACT names from the SCHEMA section below.

Q: How many rows are in the table?
A: SELECT COUNT(*) AS total_count FROM "<table>"

Q: How many items per category?
A: SELECT "<group_col>", COUNT(*) AS count FROM "<table>" GROUP BY "<group_col>" ORDER BY count DESC

Q: What is the average of a numeric column?
A: SELECT ROUND(AVG("<num_col>"), 2) AS average FROM "<table>"

Q: What is the total amount by group?
A: SELECT "<group_col>", ROUND(SUM("<num_col>"), 2) AS total FROM "<table>" GROUP BY "<group_col>" ORDER BY total DESC

Q: Show top 5 records by highest value
A: SELECT * FROM "<table>" ORDER BY "<num_col>" DESC LIMIT 5

Q: What is the maximum value?
A: SELECT MAX("<num_col>") AS max_value FROM "<table>"

Q: What is the minimum value?
A: SELECT MIN("<num_col>") AS min_value FROM "<table>"

Q: Filter records by a categorical value on a specific date
A: SELECT * FROM "<table>" WHERE LOWER("<cat_col>") = '<value>' AND CAST("<date_col>" AS DATE) = '2024-01-15' LIMIT 500

Q: Count records matching a condition in a date range
A: SELECT COUNT(*) AS match_count FROM "<table>" WHERE LOWER("<cat_col>") = '<value>' AND CAST("<date_col>" AS DATE) BETWEEN '2024-01-01' AND '2024-01-31'

Q: What are the unique values in a column?
A: SELECT DISTINCT "<col>" FROM "<table>" ORDER BY "<col>" LIMIT 500

Q: Search for a specific name or text value
A: SELECT * FROM "<table>" WHERE LOWER("<text_col>") LIKE '%search_term%' LIMIT 500

Q: Combine records from multiple tables with the same columns
A: SELECT *, '<table1>' AS source FROM "<table1>" UNION ALL SELECT *, '<table2>' AS source FROM "<table2>" LIMIT 500

Q: Join two tables on a shared column
A: SELECT a."<col1>", a."<col2>", b."<col3>" FROM "<table1>" a JOIN "<table2>" b ON LOWER(a."<shared_col>") = LOWER(b."<shared_col>") LIMIT 500

Q: What percentage of records match a condition?
A: SELECT ROUND(COUNT(CASE WHEN LOWER("<cat_col>") = '<value>' THEN 1 END) * 100.0 / COUNT(*), 2) AS percentage FROM "<table>"

Q: Show breakdown of counts by category in a date range
A: SELECT "<cat_col>", COUNT(*) AS count FROM "<table>" WHERE CAST("<date_col>" AS DATE) BETWEEN '2024-01-01' AND '2024-01-07' GROUP BY "<cat_col>" ORDER BY count DESC

Q: Which group has the highest average value?
A: SELECT "<group_col>", ROUND(AVG("<num_col>"), 2) AS avg_val FROM "<table>" GROUP BY "<group_col>" ORDER BY avg_val DESC LIMIT 1

Q: Count records matching a compound text condition
A: SELECT COUNT(*) AS count FROM "<table>" WHERE "<text_col>" = 'Specific Compound Value'

Q: Show the top N entities by total of a numeric column, filtered by category
A: SELECT "<name_col>", ROUND(SUM("<num_col>"), 2) AS total FROM "<table>" WHERE LOWER("<cat_col>") = '<filter_value>' GROUP BY "<name_col>" ORDER BY total DESC LIMIT 5

Q: Count records exceeding a threshold within a category
A: SELECT COUNT(*) AS count FROM "<table>" WHERE LOWER("<cat_col>") = '<value>' AND "<num_col>" > 100000

Q: Compare totals across categories
A: SELECT "<cat_col>", ROUND(SUM("<num_col>"), 2) AS total, COUNT(*) AS count FROM "<table>" GROUP BY "<cat_col>" ORDER BY total DESC

Q: Filter rows where a text-based date column is before a given date
A: SELECT * FROM "<table>" WHERE TRY_CAST("<date_text_col>" AS DATE) < '2024-12-01' AND TRY_CAST("<date_text_col>" AS DATE) IS NOT NULL LIMIT 500

Q: Find rows with missing or invalid dates in a text column
A: SELECT * FROM "<table>" WHERE "<date_text_col>" IS NULL OR TRY_CAST("<date_text_col>" AS DATE) IS NULL LIMIT 500

{date_context}
{status_semantics}
SCHEMA:
{schema}
{cross_table_hints}"""

ANSWER_SYSTEM_PROMPT = """You are a data analyst summarising query results for a non-technical user. Reply in JSON only:
{{"answer": "plain-english summary", "chart_config": {{"type": "bar|line|pie|scatter|none", "x_key": "col", "y_keys": ["col"], "title": "Chart Title"}} }}
Set chart_config to null when a chart would not help. Keep the answer concise — cite specific numbers from the data."""


# ── Public entry point ──────────────────────────────────────────────

async def ask_question(
    user_id: str,
    question: str,
    table_names: list[str] | None = None,
    conversation_history: list[dict] | None = None,
) -> dict:
    """
    Full NL → SQL → result → answer pipeline.
    All numerical computation is done by DuckDB; the LLM only translates
    natural language to SQL and summarises the deterministic output.
    """
    # 1. Smart table selection — avoid overwhelming the 7B model
    #    When many tables exist, pick only the ones relevant to the question.
    relevant_tables = table_names
    if table_names and len(table_names) > 3:
        relevant_tables = select_relevant_tables(user_id, question, table_names, max_tables=3)
        if relevant_tables != table_names:
            logger.info(f"Table selection: {len(table_names)} tables → {len(relevant_tables)}: {relevant_tables}")

    # 1a. Schema context (only relevant tables)
    schema = get_table_schema(user_id, relevant_tables)
    if schema == "No tables available.":
        return _empty("No datasets have been uploaded yet. Please upload a CSV or Excel file first.", question)

    # 1b. Detect cross-table relationships for multi-file queries
    cross_hints = detect_shared_columns(user_id, relevant_tables)

    # 1c. Preprocess the query — fix typos, expand abbreviations, detect values
    col_names = _extract_column_names(schema)
    col_values = get_column_values(user_id, relevant_tables)
    pp_result = preprocess_query(question, column_names=col_names, column_values=col_values)
    cleaned_question = pp_result["cleaned"]
    value_hints = pp_result["value_hints"]

    # Build a hint string for the LLM if values were detected
    hint = ""
    if value_hints:
        parts = []
        for vh in value_hints:
            parts.append(f'"{vh["value"]}" is a value in column "{vh["column"]}"')
        hint = "\nHINTS: " + "; ".join(parts) + "."

    # 1d. Date context from preprocessor
    date_ctx = pp_result.get("date_context")
    date_hint = _build_date_hints(date_ctx)

    # 1e. Status semantic enrichment
    status_hint = build_status_enrichment(col_values)

    # Use cleaned question for SQL generation
    effective_question = cleaned_question + hint + date_hint

    logger.info(f"Query preprocessed: '{question}' → '{effective_question}'")

    # 2. Generate SQL (LLM or fallback)
    sql = await _generate_sql(effective_question, schema, conversation_history, cross_hints, date_ctx, status_hint)
    if sql.startswith("-- CANNOT_ANSWER"):
        reason = sql.replace("-- CANNOT_ANSWER:", "").strip()
        return _empty(f"I couldn't answer that with the available data. {reason}", question, sql)

    # 3. Validate
    sql = _sanitise_sql(sql)

    # 4. Execute — retry once on error with self-correction
    rows, total, sql = await _execute_with_retry(user_id, sql, schema, effective_question, cross_hints, date_ctx, status_hint, table_names=relevant_tables)
    if rows is None:
        return _empty(f"I generated a query but it couldn't be executed: {total}", question, sql)

    # 5. Summarise (use original question for natural answer)
    answer_data = await _format_answer(question, sql, rows, total)

    # 5a. Validate: check LLM answer against actual DuckDB results
    answer_text = answer_data.get("answer", "Here are the results.")
    answer_text = _validate_answer(answer_text, rows, total)

    return {
        "question": question,
        "sql_generated": sql,
        "answer": answer_text,
        "data": rows,
        "chart_config": answer_data.get("chart_config"),
        "row_count": total,
        "preprocessed": cleaned_question if cleaned_question != question.strip().lower() else None,
        "query_type": "sql",
        "sources": None,
    }


def _extract_column_names(schema: str) -> list[str]:
    """Pull column names from the schema string.

    Handles multi-word type names like TIMESTAMP WITH TIME ZONE
    and column names with underscores/numbers.
    """
    # Match: column_name (ANY TYPE DESCRIPTION)
    return re.findall(r"(\w+)\s+\([^)]+\)", schema)


def _build_date_context(date_ctx: dict | None) -> str:
    """
    Build the DATE CONTEXT block that gets injected into the SQL system prompt.
    Returns an empty string when there's nothing to inject.
    """
    if not date_ctx:
        return ""

    lines = [
        f"\nDATE CONTEXT (use these exact dates in your SQL):",
        f"  Today's date: {date_ctx['current_date']} ({date_ctx['current_day']})",
    ]
    for r in date_ctx.get("resolved", []):
        if r["type"] == "single_date":
            lines.append(f'  "{r["original"]}" = {r["date"]}')
        else:
            lines.append(f'  "{r["original"]}" = {r["start_date"]} to {r["end_date"]}')
    return "\n".join(lines) + "\n"


def _build_date_hints(date_ctx: dict | None) -> str:
    """
    Build a hint string to append to the user question, similar to value hints.
    Keeps date info visible to the LLM even outside the system prompt.
    """
    if not date_ctx or not date_ctx.get("resolved"):
        return ""
    parts = []
    for r in date_ctx["resolved"]:
        if r["type"] == "single_date":
            parts.append(f'"{r["original"]}" means date {r["date"]}')
        else:
            parts.append(f'"{r["original"]}" means date range {r["start_date"]} to {r["end_date"]}')
    return "\nDATE HINTS: " + "; ".join(parts) + "."


# ── SQL generation ──────────────────────────────────────────────────

def _extract_table_list(schema: str) -> str:
    """Build a short explicit table list from the schema string."""
    tables = re.findall(r'Table "([^"]+)"', schema)
    if not tables:
        return "(none)"
    return "\n".join(f'  - "{t}"' for t in tables)


async def _generate_sql(
    question: str,
    schema: str,
    conversation_history: list[dict] | None = None,
    cross_table_hints: str = "",
    date_context: dict | None = None,
    status_semantics: str = "",
) -> str:
    """Ask Ollama to produce a SQL query. Falls back to pattern matching."""
    prompt = SQL_SYSTEM_PROMPT.format(
        table_list=_extract_table_list(schema),
        schema=schema,
        cross_table_hints=("\nCROSS-TABLE RELATIONSHIPS:\n" + cross_table_hints) if cross_table_hints else "",
        date_context=_build_date_context(date_context),
        status_semantics=status_semantics,
    )
    messages = [
        {"role": "system", "content": prompt},
    ]
    if conversation_history:
        for entry in conversation_history[-6:]:
            messages.append({"role": entry["role"], "content": entry["content"]})
    messages.append({"role": "user", "content": question})

    response = await _call_ollama(messages, temperature=0.0)
    sql = _extract_sql(response)
    return sql if sql else _fallback_sql(question, schema, date_context=date_context)


async def _fix_sql(original: str, error: str, schema: str, question: str, cross_table_hints: str = "", date_context: dict | None = None, status_semantics: str = "") -> str | None:
    """One self-correction attempt after a SQL execution error."""
    prompt = SQL_SYSTEM_PROMPT.format(
        table_list=_extract_table_list(schema),
        schema=schema,
        cross_table_hints=("\nCROSS-TABLE RELATIONSHIPS:\n" + cross_table_hints) if cross_table_hints else "",
        date_context=_build_date_context(date_context),
        status_semantics=status_semantics,
    )
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": question},
        {"role": "assistant", "content": original},
        {"role": "user", "content": f"That query failed with: {error}\nFix it. Output ONLY the corrected SQL."},
    ]
    response = await _call_ollama(messages, temperature=0.0)
    sql = _extract_sql(response)
    return sql if sql and sql != original else None


async def _format_answer(
    question: str, sql: str, rows: list[dict], row_count: int
) -> dict:
    """Summarise results in natural language + suggest a chart."""
    display = json.dumps(rows[:25], indent=2, default=str)
    user_msg = (
        f"Question: {question}\nSQL: {sql}\n"
        f"Results ({row_count} rows, showing first {min(len(rows), 25)}):\n{display}"
    )
    messages = [
        {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    response = await _call_ollama(messages, temperature=0.3)
    try:
        m = re.search(r"\{[\s\S]*\}", response)
        if m:
            return json.loads(m.group())
    except json.JSONDecodeError:
        pass
    # If LLM output isn't valid JSON, build a basic answer
    return _build_basic_answer(rows, row_count)


# ── Ollama client ───────────────────────────────────────────────────

async def _call_ollama(messages: list[dict], temperature: float = 0.0) -> str:
    """
    POST to the local Ollama chat endpoint.
    Returns raw text; on any failure falls back gracefully.
    """
    url = f"{settings.ollama_base_url}/api/chat"
    payload = {
        "model": settings.ollama_model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature},
    }
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data.get("message", {}).get("content", "")
    except Exception as e:
        logger.warning(f"Ollama call failed ({e}); using fallback")
        return _fallback_from_messages(messages)


# ── Validation & helpers ────────────────────────────────────────────

def _sanitise_sql(sql: str) -> str:
    """Strip markdown/prose, enforce read-only, add LIMIT if missing."""
    sql = _extract_sql(sql) if sql else sql
    if not sql:
        return "SELECT 'No valid SQL could be generated' AS error"
    upper = sql.strip().upper()
    blocked = ("DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE", "TRUNCATE", "GRANT", "REVOKE", "COPY", "EXPORT", "IMPORT", "ATTACH", "DETACH", "LOAD", "INSTALL", "PRAGMA", "SET")
    if any(upper.startswith(kw) for kw in blocked):
        raise ValueError("Only SELECT queries are allowed.")
    # Append LIMIT if absent and not an aggregation-only query
    if "LIMIT" not in upper:
        agg_only = bool(re.match(r"^\s*SELECT\s+(COUNT|SUM|AVG|MIN|MAX|APPROX_)\b", upper))
        if not agg_only:
            sql = sql.rstrip().rstrip(";") + " LIMIT 500"
    else:
        # Cap any LIMIT > 500 to 500 to prevent runaway results
        m = re.search(r"LIMIT\s+(\d+)", upper)
        if m and int(m.group(1)) > 500:
            sql = re.sub(r"LIMIT\s+\d+", "LIMIT 500", sql, flags=re.IGNORECASE)
    return sql


async def _execute_with_retry(user_id, sql, schema, question, cross_table_hints="", date_context=None, status_semantics="", table_names=None):
    """Execute SQL; on failure, try one LLM-driven self-correction.

    If the first correction also fails and multiple tables are in the schema,
    retry with a focused single-table schema (the most relevant table).
    """
    try:
        rows, total = execute_query(user_id, sql)
        return rows, total, sql
    except ValueError as e:
        error_str = str(e)
        logger.warning(f"SQL execution failed: {error_str[:120]}")

        # Quick fix: CAST → TRY_CAST for date conversion errors
        # The 7B model sometimes uses CAST instead of TRY_CAST on VARCHAR
        # date columns despite prompt instructions, especially under tight
        # context.  This deterministic rewrite avoids a full LLM retry.
        if "Conversion Error" in error_str and "CAST(" in sql:
            trycast_sql = re.sub(r'\bCAST\(', 'TRY_CAST(', sql)
            trycast_sql = _sanitise_sql(trycast_sql)
            try:
                rows, total = execute_query(user_id, trycast_sql)
                logger.info("CAST → TRY_CAST auto-fix resolved the conversion error")
                return rows, total, trycast_sql
            except ValueError:
                pass  # Fall through to LLM correction

        # Attempt 1: LLM self-correction with same schema
        corrected = await _fix_sql(sql, error_str, schema, question, cross_table_hints, date_context, status_semantics)
        if corrected:
            corrected = _sanitise_sql(corrected)
            try:
                rows, total = execute_query(user_id, corrected)
                return rows, total, corrected
            except ValueError as e2:
                logger.warning(f"Self-correction also failed: {str(e2)[:120]}")

        # Attempt 2: If multiple tables, retry with single most-relevant table
        if table_names and len(table_names) > 1:
            focused = select_relevant_tables(user_id, question, table_names, max_tables=1)
            if focused and focused != table_names:
                logger.info(f"Retrying with focused schema: {focused}")
                focused_schema = get_table_schema(user_id, focused)
                focused_sql = await _generate_sql(
                    question, focused_schema, None, "", date_context, status_semantics,
                )
                if focused_sql and not focused_sql.startswith("-- CANNOT"):
                    focused_sql = _sanitise_sql(focused_sql)
                    try:
                        rows, total = execute_query(user_id, focused_sql)
                        return rows, total, focused_sql
                    except ValueError as e3:
                        logger.warning(f"Focused retry also failed: {str(e3)[:120]}")

        return None, error_str, sql


def _strip_fences(text: str) -> str:
    """Remove markdown code fences and leading/trailing whitespace."""
    text = text.strip()
    text = re.sub(r"^```(?:sql|duckdb)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_sql(text: str) -> str:
    """
    Robustly extract a SQL SELECT statement from LLM output.

    Handles common failure modes from small models:
      - Prose before/after the SQL ("It looks like...\nSELECT ...")
      - SQL inside markdown fences
      - Multiple SQL blocks (take the first)
      - CANNOT_ANSWER responses
      - Pure prose with no SQL at all → returns ""
    """
    text = text.strip()
    if not text:
        return ""

    # Check for CANNOT_ANSWER first
    if "CANNOT_ANSWER" in text:
        m = re.search(r"--\s*CANNOT_ANSWER.*", text)
        return m.group(0) if m else "-- CANNOT_ANSWER: Unable to generate query"

    # Try extracting from markdown fences first
    fence_match = re.search(r"```(?:sql|duckdb)?\s*\n?([\s\S]*?)```", text)
    if fence_match:
        sql = fence_match.group(1).strip()
        if sql.upper().startswith(("SELECT", "WITH", "DESCRIBE")):
            return sql

    # Strip fences if they wrap the whole text
    stripped = _strip_fences(text)

    # If it's already clean SQL, return it
    if stripped.upper().startswith(("SELECT", "WITH", "DESCRIBE", "--")):
        return stripped

    # LLM emitted prose — try to find SQL buried inside
    # Look for SELECT ... pattern anywhere in the text
    sql_match = re.search(
        r"((?:WITH\s+\w+\s+AS\s*\([\s\S]*?\)\s*)?SELECT\s[\s\S]+?)(?:\n\n|\Z)",
        stripped,
        re.IGNORECASE,
    )
    if sql_match:
        candidate = sql_match.group(1).strip().rstrip(";")
        # Verify it looks like SQL (has FROM clause)
        if re.search(r"\bFROM\b", candidate, re.IGNORECASE):
            logger.warning(f"Extracted SQL from mixed prose/SQL response")
            return candidate

    # Last resort: look for any line starting with SELECT
    for line in stripped.split("\n"):
        line = line.strip()
        if line.upper().startswith("SELECT"):
            logger.warning(f"Extracted single-line SQL from prose response")
            return line

    # Nothing found — return empty so fallback kicks in
    logger.warning(f"No SQL found in LLM response: {text[:120]}...")
    return ""


def _empty(answer, question, sql=""):
    return {
        "question": question,
        "sql_generated": sql,
        "answer": answer,
        "data": None,
        "chart_config": None,
        "row_count": 0,
        "query_type": "sql",
        "sources": None,
    }


def _validate_answer(answer: str, rows: list[dict], row_count: int) -> str:
    """
    Deterministic validation: check if the LLM answer contains numbers
    that contradict the actual DuckDB results. If so, correct them.

    This prevents the LLM from hallucinating counts, sums, or averages
    that differ from what DuckDB actually computed.
    """
    if not rows or not answer:
        return answer

    # Extract all numbers from the LLM answer
    answer_numbers = re.findall(r"[\d,]+\.?\d*", answer)
    if not answer_numbers:
        return answer

    # Build a set of ground-truth numbers from the actual data
    truth_numbers = set()
    truth_numbers.add(str(row_count))

    for row in rows[:50]:  # check first 50 rows max
        for val in row.values():
            if val is None:
                continue
            if isinstance(val, (int, float)):
                # Add multiple representations: raw, comma-formatted, rounded
                truth_numbers.add(str(val))
                truth_numbers.add(f"{val:,}")
                if isinstance(val, float):
                    truth_numbers.add(f"{val:.2f}")
                    truth_numbers.add(str(int(val)) if val == int(val) else str(val))

    # For single-value results (COUNT, SUM, AVG, etc.), verify the answer
    if row_count == 1 and len(rows[0]) <= 2:
        actual_val = list(rows[0].values())[0]
        if actual_val is not None and isinstance(actual_val, (int, float)):
            actual_str = str(actual_val)
            actual_comma = f"{actual_val:,}"
            actual_int = str(int(actual_val)) if isinstance(actual_val, float) and actual_val == int(actual_val) else None

            # Check each number in the answer against the actual value
            for num_str in answer_numbers:
                num_clean = num_str.replace(",", "")
                try:
                    num_val = float(num_clean)
                except ValueError:
                    continue

                # If answer has a number that isn't the actual result, flag it
                if abs(num_val - float(actual_val)) > 0.01 and num_clean not in ("500",):  # ignore LIMIT
                    # The LLM got the number wrong — correct the answer
                    display = actual_comma if actual_val > 999 else actual_str
                    if actual_int:
                        display = f"{int(actual_val):,}" if actual_val > 999 else actual_int
                    answer = answer.replace(num_str, display)
                    logger.warning(
                        f"Answer validation: corrected '{num_str}' → '{display}' "
                        f"(actual DuckDB result: {actual_val})"
                    )
                    break  # correct only the first mismatch to avoid cascading replacements

    return answer


def _build_basic_answer(rows, row_count):
    """Build a minimal answer dict when the LLM can't format one."""
    if not rows:
        return {"answer": "The query returned no results.", "chart_config": None}
    cols = list(rows[0].keys())
    if row_count == 1 and len(cols) == 1:
        val = rows[0][cols[0]]
        return {"answer": f"The result is **{val}**.", "chart_config": None}
    return {
        "answer": f"Found {row_count} result{'s' if row_count != 1 else ''} across {len(cols)} column{'s' if len(cols) != 1 else ''}.",
        "chart_config": None,
    }


# ── Deterministic fallback (no LLM needed) ─────────────────────────

def _fallback_from_messages(messages: list[dict]) -> str:
    """Route to SQL fallback when called from _call_ollama failure path."""
    schema = ""
    for msg in messages:
        if msg["role"] == "system" and "SCHEMA:" in msg["content"]:
            schema = msg["content"].split("SCHEMA:")[-1].strip()
            break
    user_msg = messages[-1]["content"] if messages else ""
    if schema:
        return _fallback_sql(user_msg, schema, date_context=None)
    # For answer formatting calls, return a simple JSON
    return '{"answer": "Here are the query results.", "chart_config": null}'


def _inject_date_filter(sql: str, date_cols: list[str], date_context: dict | None) -> str:
    """
    Append a WHERE clause for resolved dates onto a fallback SQL query.
    Works for simple single-table queries produced by ``_fallback_sql()``.
    """
    if not date_context or not date_cols or not date_context.get("resolved"):
        return sql

    dc = date_cols[0]  # use the first date column
    resolved = date_context["resolved"][0]  # take the first resolved expression
    hint = resolved["sql_hint"]  # e.g. "= '2024-09-25'" or "BETWEEN '...' AND '...'"

    # Only inject if there is no WHERE already
    if " WHERE " in sql.upper():
        return sql

    # Inject before ORDER BY / GROUP BY / LIMIT
    for kw in (" ORDER BY ", " GROUP BY ", " LIMIT "):
        idx = sql.upper().find(kw)
        if idx != -1:
            return sql[:idx] + f' WHERE CAST("{dc}" AS DATE) {hint}' + sql[idx:]

    # Append at end
    return sql + f' WHERE CAST("{dc}" AS DATE) {hint}'


def _fallback_sql(question: str, schema: str, date_context: dict | None = None) -> str:
    """
    Pattern-matching SQL generator — works entirely offline without
    any LLM. Covers the most common analytical question patterns.
    """
    q = question.lower()

    tables = re.findall(r'Table "([^"]+)"', schema)
    if not tables:
        return "-- CANNOT_ANSWER: No tables found in schema"

    table = tables[0]
    # Collect columns and types (handles multi-word types like TIMESTAMP WITH TIME ZONE)
    col_types = re.findall(r"(\w+)\s+\(([^)]+)\)", schema)
    num_cols = [c for c, t in col_types if t.split()[0] in ("INTEGER", "BIGINT", "DOUBLE", "FLOAT", "DECIMAL", "HUGEINT")]
    date_cols = [c for c, t in col_types if t.split()[0] in ("DATE", "TIMESTAMP")]
    str_cols = [c for c, t in col_types if t == "VARCHAR"]
    all_cols = [c for c, _ in col_types]

    # Check if the question has any date expressions that need filtering
    has_date_expr = bool(date_context and date_context.get("resolved"))

    # Specific table by name
    for t in tables:
        if t.lower().replace("_", " ") in q or t.lower() in q:
            table = t
            break

    # Count
    if any(w in q for w in ["how many", "count", "total number", "number of"]):
        if str_cols:
            for sc in str_cols:
                if sc in q:
                    sql = f'SELECT "{sc}", COUNT(*) AS count FROM "{table}" GROUP BY "{sc}" ORDER BY count DESC LIMIT 20'
                    return _inject_date_filter(sql, date_cols, date_context) if has_date_expr else sql
        sql = f'SELECT COUNT(*) AS total_count FROM "{table}"'
        return _inject_date_filter(sql, date_cols, date_context) if has_date_expr else sql

    # Average
    if "average" in q or "avg " in q or "mean " in q:
        for nc in num_cols:
            if nc in q:
                sql = f'SELECT AVG("{nc}") AS average_{nc} FROM "{table}"'
                return _inject_date_filter(sql, date_cols, date_context) if has_date_expr else sql
        if num_cols:
            sql = f'SELECT AVG("{num_cols[0]}") AS average_{num_cols[0]} FROM "{table}"'
            return _inject_date_filter(sql, date_cols, date_context) if has_date_expr else sql

    # Sum / total
    if any(w in q for w in ["sum ", "sum(", "total", "grand total"]):
        for nc in num_cols:
            if nc in q:
                sql = f'SELECT SUM("{nc}") AS total_{nc} FROM "{table}"'
                return _inject_date_filter(sql, date_cols, date_context) if has_date_expr else sql
        if num_cols:
            sql = f'SELECT SUM("{num_cols[0]}") AS total_{num_cols[0]} FROM "{table}"'
            return _inject_date_filter(sql, date_cols, date_context) if has_date_expr else sql

    # Min / Max
    if "minimum" in q or "lowest" in q or "smallest" in q:
        for nc in num_cols:
            if nc in q:
                sql = f'SELECT MIN("{nc}") AS min_{nc} FROM "{table}"'
                return _inject_date_filter(sql, date_cols, date_context) if has_date_expr else sql
        if num_cols:
            sql = f'SELECT MIN("{num_cols[0]}") AS min_{num_cols[0]} FROM "{table}"'
            return _inject_date_filter(sql, date_cols, date_context) if has_date_expr else sql
    # "top N" pattern (e.g., "show top 5 employees", "top 10 salaries")
    top_match = re.search(r"\btop\s+(\d+)\b", q)
    if top_match or ("top" in q.split() and "top" not in ("stop", "desktop")):
        n = int(top_match.group(1)) if top_match else 10
        n = min(n, 100)
        # Find column to order by — prefer a mentioned numeric column
        order_col = None
        for nc in num_cols:
            if nc in q:
                order_col = nc
                break
        if not order_col and num_cols:
            order_col = num_cols[0]
        if order_col:
            sql = f'SELECT * FROM "{table}" ORDER BY "{order_col}" DESC LIMIT {n}'
            return _inject_date_filter(sql, date_cols, date_context) if has_date_expr else sql
        else:
            sql = f'SELECT * FROM "{table}" LIMIT {n}'
            return _inject_date_filter(sql, date_cols, date_context) if has_date_expr else sql

    if "maximum" in q or "highest" in q or "largest" in q:
        for nc in num_cols:
            if nc in q:
                sql = f'SELECT MAX("{nc}") AS max_{nc} FROM "{table}"'
                return _inject_date_filter(sql, date_cols, date_context) if has_date_expr else sql
        if num_cols:
            sql = f'SELECT MAX("{num_cols[0]}") AS max_{num_cols[0]} FROM "{table}"'
            return _inject_date_filter(sql, date_cols, date_context) if has_date_expr else sql

    # Unique / distinct
    if "unique" in q or "distinct" in q:
        for sc in str_cols:
            if sc in q:
                sql = f'SELECT DISTINCT "{sc}" FROM "{table}" ORDER BY "{sc}" LIMIT 100'
                return _inject_date_filter(sql, date_cols, date_context) if has_date_expr else sql
        if all_cols:
            sql = f'SELECT DISTINCT "{all_cols[0]}" FROM "{table}" ORDER BY "{all_cols[0]}" LIMIT 100'
            return _inject_date_filter(sql, date_cols, date_context) if has_date_expr else sql

    # Group by
    if "by" in q and ("breakdown" in q or "per" in q or "group" in q or "each" in q):
        group_col = str_cols[0] if str_cols else all_cols[0] if all_cols else None
        agg_col = num_cols[0] if num_cols else None
        if group_col and agg_col:
            sql = f'SELECT "{group_col}", SUM("{agg_col}") AS total FROM "{table}" GROUP BY "{group_col}" ORDER BY total DESC LIMIT 20'
            return _inject_date_filter(sql, date_cols, date_context) if has_date_expr else sql

    # Show all / list / display / describe
    if any(w in q for w in ["show all", "list all", "display all", "all rows", "everything", "all data"]):
        sql = f'SELECT * FROM "{table}" LIMIT 100'
        return _inject_date_filter(sql, date_cols, date_context) if has_date_expr else sql
    if any(w in q for w in ["columns", "fields", "schema", "structure", "describe"]):
        return f'DESCRIBE "{table}"'

    # Show first / last rows
    if "first" in q or "head" in q:
        m = re.search(r"(\d+)", q)
        n = int(m.group(1)) if m else 10
        sql = f'SELECT * FROM "{table}" LIMIT {min(n, 100)}'
        return _inject_date_filter(sql, date_cols, date_context) if has_date_expr else sql
    if "last" in q or "recent" in q:
        order_col = date_cols[0] if date_cols else all_cols[-1] if all_cols else "rowid"
        m = re.search(r"(\d+)", q)
        n = int(m.group(1)) if m else 10
        sql = f'SELECT * FROM "{table}" ORDER BY "{order_col}" DESC LIMIT {min(n, 100)}'
        return _inject_date_filter(sql, date_cols, date_context) if has_date_expr else sql

    # Default: preview (with date filter if dates were mentioned)
    sql = f'SELECT * FROM "{table}" LIMIT 20'
    return _inject_date_filter(sql, date_cols, date_context) if has_date_expr else sql
