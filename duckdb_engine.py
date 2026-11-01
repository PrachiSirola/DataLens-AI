"""
DuckDB query engine — per-user isolated analytical database.

Each user gets their own DuckDB database file. Uploaded DataFrames
are registered as tables that can be queried with SQL.

Storage location is configured via DATA_DIR in .env (default: ~/.datalensai/duckdb/).
"""

import logging
import re
from pathlib import Path

import duckdb
import pandas as pd

from app.config import settings

logger = logging.getLogger(__name__)


def _user_db_path(user_id: str) -> Path:
    """Return the DuckDB database path for a user."""
    return settings.duckdb_dir / f"{user_id}.duckdb"


def _sanitize_table_name(filename: str, dataset_id: str, user_id: str = "") -> str:
    """Generate a safe, human-readable DuckDB table name from filename.

    Uses just the file stem (no random hex suffix) so that the local LLM
    can reliably copy the table name from the schema context.  When a
    table with the same base name already exists in the user's DB, a
    numeric suffix (_2, _3, …) is appended instead.
    """
    base = Path(filename).stem.lower()
    base = re.sub(r"[^a-z0-9_]", "_", base)
    base = re.sub(r"_+", "_", base).strip("_")
    if not base or base[0].isdigit():
        base = "t_" + base

    # Check for name collisions in the user's DuckDB
    if user_id:
        db_path = _user_db_path(user_id)
        if db_path.exists():
            try:
                con = duckdb.connect(str(db_path))
                existing = {t[0] for t in con.execute("SHOW TABLES").fetchall()}
                con.close()

                # If base name already taken, append _2, _3, …
                if base in existing:
                    n = 2
                    while f"{base}_{n}" in existing:
                        n += 1
                    base = f"{base}_{n}"
            except Exception:
                pass  # Fall through with the base name

    return base


def register_dataframe(
    user_id: str,
    dataset_id: str,
    filename: str,
    df: pd.DataFrame,
) -> tuple[str, int, int, list[str]]:
    """
    Load a DataFrame into the user's DuckDB as a persistent table.
    Returns (table_name, row_count, col_count, column_names).
    """
    table_name = _sanitize_table_name(filename, dataset_id, user_id)
    db_path = _user_db_path(user_id)

    con = duckdb.connect(str(db_path))
    try:
        # Drop table if exists (re-upload scenario)
        con.execute(f'DROP TABLE IF EXISTS "{table_name}"')
        # Create table from DataFrame
        con.execute(f'CREATE TABLE "{table_name}" AS SELECT * FROM df')

        row_count = con.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
        col_info = con.execute(f'DESCRIBE "{table_name}"').fetchall()
        columns = [row[0] for row in col_info]

        logger.info(
            f"Registered table '{table_name}' for user {user_id}: "
            f"{row_count} rows, {len(columns)} cols"
        )
        return table_name, row_count, len(columns), columns
    finally:
        con.close()


def drop_table(user_id: str, table_name: str) -> None:
    """Remove a table from the user's DuckDB."""
    db_path = _user_db_path(user_id)
    if not db_path.exists():
        return
    con = duckdb.connect(str(db_path))
    try:
        con.execute(f'DROP TABLE IF EXISTS "{table_name}"')
    finally:
        con.close()


def list_tables(user_id: str) -> list[dict]:
    """List all tables in the user's DuckDB with schema info."""
    db_path = _user_db_path(user_id)
    if not db_path.exists():
        return []

    con = duckdb.connect(str(db_path))
    try:
        tables = con.execute("SHOW TABLES").fetchall()
        result = []
        for (table_name,) in tables:
            col_info = con.execute(f'DESCRIBE "{table_name}"').fetchall()
            row_count = con.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
            result.append({
                "table_name": table_name,
                "columns": [
                    {"name": row[0], "type": row[1]} for row in col_info
                ],
                "row_count": row_count,
            })
        return result
    finally:
        con.close()


_DATE_LIKE_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}")


def _detect_date_like_varchars(con, table_name: str, cols: list) -> set[str]:
    """Detect VARCHAR columns whose values look like dates (e.g. '2024-01-15').

    Returns a set of column names that should be annotated in the schema
    so the LLM uses TRY_CAST instead of raw comparison.  At least half
    of the sampled non-null values must match a YYYY-MM-DD or YYYY/MM/DD
    pattern for the column to qualify.
    """
    date_like: set[str] = set()
    for col_name, col_type, *_ in cols:
        if col_type != "VARCHAR":
            continue
        try:
            samples = con.execute(
                f'SELECT DISTINCT "{col_name}" FROM "{table_name}" '
                f'WHERE "{col_name}" IS NOT NULL LIMIT 5'
            ).fetchall()
            if samples and sum(
                1 for s in samples if s[0] and _DATE_LIKE_RE.match(str(s[0]).strip())
            ) >= max(len(samples) * 0.5, 1):
                date_like.add(col_name)
        except Exception:
            pass
    return date_like


def get_table_schema(user_id: str, table_names: list[str] | None = None) -> str:
    """
    Return a formatted schema description for LLM context.
    If table_names is None, returns schema for all tables.

    Context budget: When multiple tables are present, sample rows are
    reduced to keep the total prompt within the 7B model's context
    window (~4096 tokens).  Tables with poor data quality (>50% null
    columns) are flagged so the LLM deprioritises them.
    """
    db_path = _user_db_path(user_id)
    if not db_path.exists():
        return "No tables available."

    con = duckdb.connect(str(db_path))
    try:
        all_tables = [t[0] for t in con.execute("SHOW TABLES").fetchall()]
        if table_names:
            all_tables = [t for t in all_tables if t in table_names]

        if not all_tables:
            return "No tables available."

        # Context budget: fewer sample rows when multiple tables
        num_tables = len(all_tables)
        if num_tables == 1:
            sample_rows = 3
        elif num_tables <= 3:
            sample_rows = 2
        else:
            sample_rows = 1

        schema_parts = []
        for table_name in all_tables:
            cols = con.execute(f'DESCRIBE "{table_name}"').fetchall()
            row_count = con.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]

            # Data quality check: count how many columns are mostly null
            total_cols = len(cols)
            sparse_cols = 0
            if row_count > 0:
                for col_name, *_ in cols:
                    try:
                        null_count = con.execute(
                            f'SELECT COUNT(*) FROM "{table_name}" WHERE "{col_name}" IS NULL'
                        ).fetchone()[0]
                        if null_count > row_count * 0.8:
                            sparse_cols += 1
                    except Exception:
                        pass

            quality_warning = ""
            if total_cols > 20 and sparse_cols > total_cols * 0.5:
                quality_warning = (
                    f" ⚠ LOW QUALITY: {sparse_cols}/{total_cols} columns are mostly empty. "
                    f"This table may have been poorly extracted from a PDF. "
                    f"Prefer other tables with the same data if available."
                )

            # Only include non-sparse columns in schema for very wide tables
            if total_cols > 20 and sparse_cols > total_cols * 0.5:
                date_like_cols = _detect_date_like_varchars(con, table_name, cols)
                # Show only columns that actually have data
                useful_cols = []
                for col_name, col_type, *_ in cols:
                    try:
                        non_null = con.execute(
                            f'SELECT COUNT(*) FROM "{table_name}" WHERE "{col_name}" IS NOT NULL'
                        ).fetchone()[0]
                        if non_null > row_count * 0.2:
                            hint = ""
                            if col_name in date_like_cols:
                                hint = " ⚠ DATE-LIKE text — use TRY_CAST as DATE"
                            useful_cols.append(f'  - "{col_name}" ({col_type}){hint}')
                    except Exception:
                        useful_cols.append(f'  - "{col_name}" ({col_type})')
                col_list = "\n".join(useful_cols)
                schema_parts.append(
                    f'Table "{table_name}" ({row_count} rows, {total_cols} total cols, showing {len(useful_cols)} with data):{quality_warning}\n{col_list}'
                )
            else:
                date_like_cols = _detect_date_like_varchars(con, table_name, cols)
                col_lines = []
                for c in cols:
                    hint = ""
                    if c[0] in date_like_cols:
                        hint = " ⚠ DATE-LIKE text — use TRY_CAST as DATE"
                    col_lines.append(f'  - "{c[0]}" ({c[1]}){hint}')
                col_list = "\n".join(col_lines)
                schema_parts.append(
                    f'Table "{table_name}" ({row_count} rows):\n{col_list}'
                )

            # Add sample data (budget-aware row count)
            sample = con.execute(f'SELECT * FROM "{table_name}" LIMIT {sample_rows}').fetchdf()
            if not sample.empty:
                # For wide tables, only show non-null columns in sample
                if total_cols > 20:
                    non_null_cols = [c for c in sample.columns if sample[c].notna().any()]
                    sample = sample[non_null_cols]
                schema_parts.append(f"  Sample data:\n{sample.to_string(index=False)}")

            # Add categorical value hints for VARCHAR columns (helps LLM
            # understand data semantics — e.g. that "Overdue" is a status value)
            cat_hints = []
            max_cat_cols = 5 if num_tables <= 3 else 3
            for col_name, col_type, *_ in cols:
                if len(cat_hints) >= max_cat_cols:
                    break
                if col_type != "VARCHAR":
                    continue
                # Skip date-like columns (already annotated)
                if col_name in date_like_cols:
                    continue
                try:
                    distinct_count = con.execute(
                        f'SELECT COUNT(DISTINCT "{col_name}") FROM "{table_name}" '
                        f'WHERE "{col_name}" IS NOT NULL'
                    ).fetchone()[0]
                    if 2 <= distinct_count <= 15:
                        vals = con.execute(
                            f'SELECT DISTINCT "{col_name}" FROM "{table_name}" '
                            f'WHERE "{col_name}" IS NOT NULL ORDER BY "{col_name}" LIMIT 15'
                        ).fetchall()
                        cat_hints.append(
                            f'  Categorical "{col_name}": {", ".join(str(v[0]) for v in vals)}'
                        )
                except Exception:
                    pass
            if cat_hints:
                schema_parts.append("\n".join(cat_hints))

        return "\n\n".join(schema_parts)
    finally:
        con.close()


def get_column_values(
    user_id: str,
    table_names: list[str] | None = None,
    max_values: int = 50,
) -> dict[str, list[str]]:
    """
    Return a dict mapping VARCHAR column names to their top unique values.
    Used by the query preprocessor to detect data values in user input.
    E.g.: {"first_name": ["Prachi", "Nikhil", ...], "department": ["Engineering / IT", ...]}
    """
    db_path = _user_db_path(user_id)
    if not db_path.exists():
        return {}

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        all_tables = [t[0] for t in con.execute("SHOW TABLES").fetchall()]
        if table_names:
            all_tables = [t for t in all_tables if t in table_names]

        result: dict[str, list[str]] = {}
        for table_name in all_tables:
            cols = con.execute(f'DESCRIBE "{table_name}"').fetchall()
            for col_name, col_type, *_ in cols:
                if col_type != "VARCHAR":
                    continue
                try:
                    rows = con.execute(
                        f'SELECT DISTINCT "{col_name}" FROM "{table_name}" '
                        f'WHERE "{col_name}" IS NOT NULL '
                        f'ORDER BY "{col_name}" LIMIT {max_values}'
                    ).fetchall()
                    values = [str(r[0]).strip() for r in rows if r[0] and str(r[0]).strip()]
                    if values:
                        # Merge if column already seen from another table
                        if col_name in result:
                            merged = list(dict.fromkeys(result[col_name] + values))
                            result[col_name] = merged[:max_values]
                        else:
                            result[col_name] = values
                except duckdb.Error:
                    continue
        return result
    finally:
        con.close()


def select_relevant_tables(
    user_id: str,
    question: str,
    table_names: list[str] | None = None,
    max_tables: int = 3,
) -> list[str]:
    """
    Pick the table(s) most relevant to a user question.

    Scoring:
      - Table name words overlap with question → +3 per word
      - Column name words overlap with question → +1 per column
      - If no overlap at all, return all tables (LLM decides)
      - If ≤ max_tables exist, return all (no filtering needed)

    Returns a list of table names, sorted by relevance score descending.
    """
    db_path = _user_db_path(user_id)
    if not db_path.exists():
        return table_names or []

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        all_tables = [t[0] for t in con.execute("SHOW TABLES").fetchall()]
        if table_names:
            all_tables = [t for t in all_tables if t in table_names]

        if len(all_tables) <= max_tables:
            return all_tables

        q_words = set(re.findall(r"[a-z]{2,}", question.lower()))
        # Remove very common words that would match anything
        stopwords = {
            "the", "is", "in", "of", "to", "and", "for", "how", "many",
            "what", "show", "me", "all", "from", "with", "are", "was",
            "were", "been", "being", "have", "has", "had", "do", "does",
            "did", "will", "would", "could", "should", "can", "may",
            "this", "that", "these", "those", "each", "per", "by",
            "get", "give", "list", "display", "tell", "find",
        }
        q_words -= stopwords

        scores: dict[str, int] = {}
        for table_name in all_tables:
            score = 0
            # Table name matching (strong signal)
            t_words = set(table_name.lower().replace("_", " ").split())
            t_words -= stopwords
            for tw in t_words:
                if tw in q_words or any(tw in qw or qw in tw for qw in q_words):
                    score += 3

            # Column name matching
            try:
                cols = con.execute(f'DESCRIBE "{table_name}"').fetchall()
                for col_name, *_ in cols:
                    c_words = set(col_name.lower().replace("_", " ").split())
                    c_words -= stopwords
                    if c_words & q_words:
                        score += 1
            except duckdb.Error:
                pass

            scores[table_name] = score

        # If no table scored anything, return all (let LLM figure it out)
        if max(scores.values()) == 0:
            return all_tables

        # Return top max_tables by score, include any with score > 0
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        selected = [t for t, s in ranked[:max_tables] if s > 0]
        return selected if selected else all_tables

    finally:
        con.close()


def get_all_column_names(user_id: str, table_names: list[str] | None = None) -> list[str]:
    """
    Return a flat list of all column names across the user's tables.
    Lightweight — used for schema-aware query routing.
    """
    db_path = _user_db_path(user_id)
    if not db_path.exists():
        return []

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        all_tables = [t[0] for t in con.execute("SHOW TABLES").fetchall()]
        if table_names:
            all_tables = [t for t in all_tables if t in table_names]

        columns = set()
        for table_name in all_tables:
            cols = con.execute(f'DESCRIBE "{table_name}"').fetchall()
            for col_name, *_ in cols:
                columns.add(col_name)
        return list(columns)
    finally:
        con.close()


def detect_shared_columns(user_id: str, table_names: list[str] | None = None) -> str:
    """
    Detect columns shared across multiple tables (potential join/union keys).
    Returns a human-readable hint string for the LLM prompt.
    E.g.: 'Tables "attendance_abc" and "salary_def" share columns: employee, employee_id, department'
    """
    db_path = _user_db_path(user_id)
    if not db_path.exists():
        return ""

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        all_tables = [t[0] for t in con.execute("SHOW TABLES").fetchall()]
        if table_names:
            all_tables = [t for t in all_tables if t in table_names]

        if len(all_tables) < 2:
            return ""

        # Build a dict: table_name → set of column names
        table_columns: dict[str, set[str]] = {}
        for table_name in all_tables:
            cols = con.execute(f'DESCRIBE "{table_name}"').fetchall()
            table_columns[table_name] = {c[0] for c in cols}

        # Find shared columns between each pair
        hints = []
        seen_pairs = set()
        for t1 in all_tables:
            for t2 in all_tables:
                if t1 >= t2:
                    continue
                pair_key = (t1, t2)
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                shared = table_columns[t1] & table_columns[t2]
                if shared:
                    # Prioritize identifier-like columns (domain-neutral)
                    id_cols = [c for c in shared if any(
                        kw in c.lower() for kw in ("id", "name", "code", "email", "key", "ref", "number")
                    )]
                    other_cols = [c for c in shared if c not in id_cols]
                    ordered = id_cols + other_cols

                    hints.append(
                        f'Tables "{t1}" and "{t2}" share columns: {", ".join(ordered)}. '
                        f"You can JOIN on these or UNION ALL rows from both tables."
                    )

        return "\n".join(hints)
    finally:
        con.close()


def execute_query(user_id: str, sql: str) -> tuple[list[dict], int]:
    """
    Execute a SQL query against the user's DuckDB.
    Returns (rows_as_dicts, total_row_count).
    Raises ValueError on SQL errors.
    """
    db_path = _user_db_path(user_id)
    if not db_path.exists():
        raise ValueError("No datasets uploaded yet.")

    # Safety: block anything that isn't a read
    sql_upper = sql.strip().upper()
    blocked = (
        "DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE",
        "TRUNCATE", "GRANT", "REVOKE", "COPY", "EXPORT", "IMPORT",
        "ATTACH", "DETACH", "LOAD", "INSTALL", "PRAGMA", "SET",
    )
    if any(sql_upper.startswith(kw) for kw in blocked):
        raise ValueError("Only SELECT queries are allowed.")
    # Also block semicolon-separated multi-statements
    if ";" in sql.rstrip().rstrip(";"):
        raise ValueError("Multi-statement queries are not allowed.")

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        result = con.execute(sql).fetchdf()
        rows = result.head(500).to_dict(orient="records")
        # Convert any non-serializable types to JSON-safe values
        import numpy as np
        for row in rows:
            for k, v in row.items():
                if v is None:
                    continue
                elif isinstance(v, float) and (pd.isna(v) or np.isnan(v)):
                    row[k] = None
                elif isinstance(v, (np.integer,)):
                    row[k] = int(v)
                elif isinstance(v, (np.floating,)):
                    row[k] = float(v)
                elif isinstance(v, np.bool_):
                    row[k] = bool(v)
                elif hasattr(v, "isoformat"):
                    row[k] = v.isoformat()
                elif isinstance(v, (bytes, bytearray)):
                    row[k] = str(v)
                elif isinstance(v, np.ndarray):
                    row[k] = v.tolist()
        return rows, len(result)
    except duckdb.Error as e:
        raise ValueError(f"SQL error: {e}")
    finally:
        con.close()
