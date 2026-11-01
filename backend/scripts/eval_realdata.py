#!/usr/bin/env python3
"""
DataLensAI — Real-Data SQL Accuracy Evaluation (Generic)

Tests the LLM against ANY real uploaded CSV data. Auto-generates test
cases from the actual schema and computes ground truth dynamically
from DuckDB queries.

Usage:
  # Test against any CSV(s):
  python -m scripts.eval_realdata --csv /path/to/data1.csv --csv /path/to/data2.csv

  # Legacy mode (backward compatible):
  python -m scripts.eval_realdata \
      --attendance /path/to/attendance.csv \
      --transactions /path/to/transactions.csv

  # Compare models:
  python -m scripts.eval_realdata --csv /path/to/data.csv --model qwen2.5:7b

Requirements:
  - Ollama running with the target model
  - pip install httpx duckdb pandas tabulate
"""

import argparse
import asyncio
import json
import logging
import re
import sys
import time
from pathlib import Path

import duckdb
import httpx
import pandas as pd

# ── Configuration ─────────────────────────────────────────────────────

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "datalens-sql"

# ── Helpers ───────────────────────────────────────────────────────────


def _build_schema(con: duckdb.DuckDBPyConnection, table_name: str) -> str:
    """Build schema string matching get_table_schema() format."""
    cols = con.execute(f'DESCRIBE "{table_name}"').fetchall()
    row_count = con.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]

    col_list = "\n".join(f'  - "{c[0]}" ({c[1]})' for c in cols)
    schema = f'Table "{table_name}" ({row_count} rows):\n{col_list}'

    sample = con.execute(f'SELECT * FROM "{table_name}" LIMIT 3').fetchdf()
    if not sample.empty:
        schema += f"\n  Sample data:\n{sample.to_string(index=False)}"

    return schema


def _build_system_prompt(schema: str) -> str:
    """Build the same system prompt used in production."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from app.services.ai_query_engine import SQL_SYSTEM_PROMPT, _extract_table_list
        table_list = _extract_table_list(schema)
        return SQL_SYSTEM_PROMPT.format(
            table_list=table_list,
            schema=schema,
            cross_table_hints="",
            date_context="",
            status_semantics="",
        )
    except ImportError:
        return f"""You are a DuckDB SQL expert. Output ONLY a valid DuckDB SQL SELECT query.
Always double-quote table and column names.

SCHEMA:
{schema}"""


async def _generate_sql(question: str, system_prompt: str) -> str:
    """Call Ollama to generate SQL."""
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
        "stream": False,
        "options": {"temperature": 0},
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(OLLAMA_URL, json=payload)
        resp.raise_for_status()
        content = resp.json().get("message", {}).get("content", "")

    content = content.strip()
    content = re.sub(r"^```(?:sql|duckdb)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)
    return content.strip()


# ── Validator helpers ─────────────────────────────────────────────────

def _has_value(rows, target, tolerance_pct=0.01):
    """Check if any cell in the first row matches target number."""
    if not rows:
        return False
    for v in rows[0].values():
        if v is None:
            continue
        try:
            actual = float(v)
            if abs(actual - target) < max(abs(target * tolerance_pct), 0.5):
                return True
        except (ValueError, TypeError):
            continue
    return False


def _any_row_contains(rows, text):
    """Check if any row contains the given text (case insensitive)."""
    text_lower = text.lower()
    return any(text_lower in str(r).lower() for r in rows)


# ══════════════════════════════════════════════════════════════════════
#  UNIVERSAL TEST GENERATOR
#  Auto-generates test cases from ANY CSV's actual schema and data.
#  Ground truth is computed directly from DuckDB.
# ══════════════════════════════════════════════════════════════════════

def _generate_universal_tests(
    con: duckdb.DuckDBPyConnection, table_name: str
) -> list[tuple[str, str, callable]]:
    """
    Auto-generate comprehensive test cases from any table's schema.
    Ground truth is computed from actual data via DuckDB queries.
    """
    cols = con.execute(f'DESCRIBE "{table_name}"').fetchall()
    col_info = {c[0]: c[1] for c in cols}
    total_rows = con.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]

    numeric_cols = [c for c, t in col_info.items()
                    if t in ("BIGINT", "INTEGER", "DOUBLE", "FLOAT", "DECIMAL", "HUGEINT")]
    varchar_cols = [c for c, t in col_info.items() if t == "VARCHAR"]
    date_cols = [c for c, t in col_info.items() if "DATE" in t or "TIMESTAMP" in t]

    tests = []
    prefix = table_name[:10]  # short prefix for test IDs

    # ── 1. Total row count ───────────────────────────────────────────
    tests.append((
        "How many rows are in the table?",
        f"{prefix}_count_total",
        lambda rows, n, tr=total_rows: (
            n == 1 and _has_value(rows, tr),
            f"Expected {tr}, got {rows}",
        ),
    ))

    # ── 2. COUNT GROUP BY (first varchar col) ────────────────────────
    if varchar_cols:
        group_col = varchar_cols[0]
        gt_groups = con.execute(
            f'SELECT COUNT(DISTINCT "{group_col}") FROM "{table_name}"'
        ).fetchone()[0]
        tests.append((
            f"How many rows per {group_col.replace('_', ' ')}?",
            f"{prefix}_count_groupby",
            lambda rows, n, eg=gt_groups: (
                n >= min(eg, 2),
                f"Expected ≥{min(eg, 2)} groups, got {n}",
            ),
        ))

    # ── 3-6. Numeric aggregations ────────────────────────────────────
    for num_col in numeric_cols[:2]:  # test up to 2 numeric columns
        col_label = num_col.replace("_", " ")
        short = num_col[:8]

        # AVG
        gt_avg = con.execute(
            f'SELECT ROUND(AVG("{num_col}"), 2) FROM "{table_name}"'
        ).fetchone()[0]
        if gt_avg is not None:
            tests.append((
                f"What is the average {col_label}?",
                f"{prefix}_{short}_avg",
                lambda rows, n, ea=float(gt_avg): (
                    n == 1 and _has_value(rows, ea),
                    f"Expected avg ~{ea}, got {rows}",
                ),
            ))

        # SUM
        gt_sum = con.execute(
            f'SELECT ROUND(SUM("{num_col}"), 2) FROM "{table_name}"'
        ).fetchone()[0]
        if gt_sum is not None:
            tests.append((
                f"What is the total {col_label}?",
                f"{prefix}_{short}_sum",
                lambda rows, n, es=float(gt_sum): (
                    n == 1 and _has_value(rows, es, tolerance_pct=0.001),
                    f"Expected sum ~{es}, got {rows}",
                ),
            ))

        # MAX
        gt_max = con.execute(
            f'SELECT MAX("{num_col}") FROM "{table_name}"'
        ).fetchone()[0]
        if gt_max is not None:
            tests.append((
                f"What is the maximum {col_label}?",
                f"{prefix}_{short}_max",
                lambda rows, n, em=float(gt_max): (
                    n == 1 and _has_value(rows, em),
                    f"Expected max {em}, got {rows}",
                ),
            ))

        # MIN
        gt_min = con.execute(
            f'SELECT MIN("{num_col}") FROM "{table_name}"'
        ).fetchone()[0]
        if gt_min is not None:
            tests.append((
                f"What is the minimum {col_label}?",
                f"{prefix}_{short}_min",
                lambda rows, n, em=float(gt_min): (
                    n == 1 and _has_value(rows, em),
                    f"Expected min {em}, got {rows}",
                ),
            ))

    # ── 7. TOP N ─────────────────────────────────────────────────────
    if numeric_cols:
        num_col = numeric_cols[0]
        tests.append((
            f"Show top 5 rows by highest {num_col.replace('_', ' ')}",
            f"{prefix}_top5",
            lambda rows, n: (
                n == 5,
                f"Expected 5 rows, got {n}",
            ),
        ))

    # ── 8. DISTINCT values ───────────────────────────────────────────
    if varchar_cols:
        col = varchar_cols[0]
        gt_distinct = con.execute(
            f'SELECT COUNT(DISTINCT "{col}") FROM "{table_name}"'
        ).fetchone()[0]
        tests.append((
            f"What are the unique {col.replace('_', ' ')} values?",
            f"{prefix}_distinct",
            lambda rows, n, ed=gt_distinct: (
                n >= min(ed, 3),
                f"Expected ≥{min(ed, 3)} distinct values, got {n}",
            ),
        ))

    # ── 9. Text/name search ──────────────────────────────────────────
    name_cols = [c for c in varchar_cols
                 if any(w in c.lower() for w in ("name", "title", "label", "city", "product"))]
    if name_cols:
        name_col = name_cols[0]
        sample_val = con.execute(
            f'SELECT "{name_col}" FROM "{table_name}" WHERE "{name_col}" IS NOT NULL LIMIT 1'
        ).fetchone()
        if sample_val:
            sample_name = str(sample_val[0])
            search_term = sample_name.split()[0] if " " in sample_name else sample_name[:6]
            gt_count = con.execute(
                f"SELECT COUNT(*) FROM \"{table_name}\" WHERE LOWER(\"{name_col}\") LIKE LOWER('%{search_term}%')"
            ).fetchone()[0]
            tests.append((
                f"Show all records for {search_term}",
                f"{prefix}_search",
                lambda rows, n, st=search_term.lower(), gc=gt_count: (
                    n >= 1 and any(st in str(r).lower() for r in rows),
                    f"Expected ≥1 row with '{st}' (ground truth: {gc}), got {n}",
                ),
            ))

    # ── 10. GROUP BY + aggregation ───────────────────────────────────
    if varchar_cols and numeric_cols:
        group_col = varchar_cols[0]
        num_col = numeric_cols[0]
        gt_groups = con.execute(
            f'SELECT COUNT(DISTINCT "{group_col}") FROM "{table_name}"'
        ).fetchone()[0]
        tests.append((
            f"What is the average {num_col.replace('_', ' ')} by {group_col.replace('_', ' ')}?",
            f"{prefix}_groupby_avg",
            lambda rows, n, eg=gt_groups: (
                n >= min(eg, 2),
                f"Expected ≥{min(eg, 2)} groups, got {n}",
            ),
        ))

    # ── 11. Date filtering (if date columns exist) ───────────────────
    if date_cols:
        date_col = date_cols[0]
        # Get a sample date from the data
        sample_date = con.execute(
            f'SELECT CAST("{date_col}" AS DATE) FROM "{table_name}" '
            f'WHERE "{date_col}" IS NOT NULL ORDER BY "{date_col}" LIMIT 1'
        ).fetchone()
        if sample_date:
            date_str = str(sample_date[0])
            gt_count = con.execute(
                f"SELECT COUNT(*) FROM \"{table_name}\" WHERE CAST(\"{date_col}\" AS DATE) = '{date_str}'"
            ).fetchone()[0]
            if gt_count > 0:
                tests.append((
                    f"How many records are on {date_str}?",
                    f"{prefix}_date_filter",
                    lambda rows, n, gc=gt_count: (
                        n == 1 and _has_value(rows, gc),
                        f"Expected count {gc}, got {rows}",
                    ),
                ))

    # ── 12. Categorical/status filter ────────────────────────────────
    # Find a categorical column with low cardinality
    for col in varchar_cols:
        distinct_count = con.execute(
            f'SELECT COUNT(DISTINCT "{col}") FROM "{table_name}"'
        ).fetchone()[0]
        if 2 <= distinct_count <= 20:
            # Get the most common value
            top_val = con.execute(
                f'SELECT "{col}", COUNT(*) as cnt FROM "{table_name}" '
                f'WHERE "{col}" IS NOT NULL GROUP BY "{col}" ORDER BY cnt DESC LIMIT 1'
            ).fetchone()
            if top_val:
                val_str = str(top_val[0])
                gt_count = int(top_val[1])
                tests.append((
                    f"How many records have {col.replace('_', ' ')} equal to {val_str}?",
                    f"{prefix}_cat_filter",
                    lambda rows, n, gc=gt_count: (
                        n == 1 and _has_value(rows, gc),
                        f"Expected {gc}, got {rows}",
                    ),
                ))
            break  # only one categorical test

    # ── 13. Percentage calculation ───────────────────────────────────
    for col in varchar_cols:
        distinct_count = con.execute(
            f'SELECT COUNT(DISTINCT "{col}") FROM "{table_name}"'
        ).fetchone()[0]
        if 2 <= distinct_count <= 20:
            top_val = con.execute(
                f'SELECT "{col}", COUNT(*) as cnt FROM "{table_name}" '
                f'WHERE "{col}" IS NOT NULL GROUP BY "{col}" ORDER BY cnt DESC LIMIT 1'
            ).fetchone()
            if top_val:
                val_str = str(top_val[0])
                gt_pct = round(int(top_val[1]) / total_rows * 100, 2)
                tests.append((
                    f"What percentage of records have {col.replace('_', ' ')} as {val_str}?",
                    f"{prefix}_percentage",
                    lambda rows, n, ep=gt_pct: (
                        n >= 1 and any(
                            abs(float(v) - ep) < max(ep * 0.05, 1)
                            for r in rows for v in r.values()
                            if v is not None and _is_numeric(v)
                        ),
                        f"Expected ~{ep}%, got {rows}",
                    ),
                ))
            break

    # ── 14. SUM by category ──────────────────────────────────────────
    if varchar_cols and numeric_cols:
        for cat_col in varchar_cols:
            distinct_count = con.execute(
                f'SELECT COUNT(DISTINCT "{cat_col}") FROM "{table_name}"'
            ).fetchone()[0]
            if 2 <= distinct_count <= 15:
                num_col = numeric_cols[0]
                tests.append((
                    f"What is the total {num_col.replace('_', ' ')} by {cat_col.replace('_', ' ')}?",
                    f"{prefix}_sum_by_cat",
                    lambda rows, n, dc=distinct_count: (
                        n >= min(dc, 2),
                        f"Expected ≥{min(dc, 2)} categories, got {n}",
                    ),
                ))
                break

    # ── 15. Column listing ───────────────────────────────────────────
    tests.append((
        "What are the column names?",
        f"{prefix}_columns",
        lambda rows, n, nc=len(cols): (
            n >= min(nc, 3),
            f"Expected ≥{min(nc, 3)} columns, got {n}",
        ),
    ))

    return tests


def _is_numeric(v) -> bool:
    """Check if a value can be converted to float."""
    try:
        float(str(v).replace(",", ""))
        return True
    except (ValueError, TypeError):
        return False


# ── Main evaluation loop ──────────────────────────────────────────────

async def run_eval(csv_paths: list[str]):
    """Run evaluation against any CSV datasets."""
    con = duckdb.connect(":memory:")

    all_tests = []
    loaded_tables = []

    for csv_path in csv_paths:
        df = pd.read_csv(csv_path)
        # Derive table name from filename
        table_name = Path(csv_path).stem.lower()
        table_name = re.sub(r"[^a-z0-9_]", "_", table_name)
        table_name = re.sub(r"_+", "_", table_name).strip("_")

        con.execute(f'CREATE TABLE "{table_name}" AS SELECT * FROM df')
        schema = _build_schema(con, table_name)
        sys_prompt = _build_system_prompt(schema)

        tests = _generate_universal_tests(con, table_name)
        for q, tid, validator in tests:
            all_tests.append((q, tid, validator, sys_prompt, table_name))

        loaded_tables.append((table_name, len(df), len(df.columns)))

    if not all_tests:
        print("ERROR: No CSV files provided. Use --csv /path/to/file.csv")
        sys.exit(1)

    print(f"\n{'═' * 65}")
    print(f"  DataLensAI Real-Data SQL Accuracy Evaluation (Generic)")
    print(f"  Model: {MODEL}")
    for tbl, rows, cols in loaded_tables:
        print(f"  Table: {tbl} ({rows} rows, {cols} cols)")
    print(f"  Total tests: {len(all_tests)}")
    print(f"{'═' * 65}\n")

    results = []
    passed = 0
    failed = 0
    errors = 0
    per_table = {}

    for i, (question, test_id, validator, sys_prompt, tbl) in enumerate(all_tests, 1):
        print(f"  [{i:2d}/{len(all_tests)}] [{tbl[:15]}] {question}")

        try:
            t0 = time.time()
            sql = await _generate_sql(question, sys_prompt)
            gen_time = time.time() - t0

            print(f"        SQL: {sql[:120]}{'...' if len(sql) > 120 else ''}")
            print(f"        Time: {gen_time:.1f}s")

            try:
                result_df = con.execute(sql).fetchdf()
                rows = result_df.to_dict(orient="records")
                row_count = len(result_df)

                ok, detail = validator(rows, row_count)
                if ok:
                    print(f"        ✓ PASS")
                    passed += 1
                    results.append({"test": test_id, "table": tbl, "status": "PASS", "time": gen_time, "sql": sql})
                else:
                    print(f"        ✗ FAIL — {detail}")
                    failed += 1
                    results.append({"test": test_id, "table": tbl, "status": "FAIL", "detail": detail, "sql": sql, "time": gen_time})

            except duckdb.Error as e:
                print(f"        ✗ SQL ERROR — {e}")
                errors += 1
                results.append({"test": test_id, "table": tbl, "status": "ERROR", "detail": str(e), "sql": sql, "time": gen_time})

        except Exception as e:
            print(f"        ✗ GENERATION ERROR — {e}")
            errors += 1
            results.append({"test": test_id, "table": tbl, "status": "ERROR", "detail": str(e)})

        print()

        # Track per-table stats
        if tbl not in per_table:
            per_table[tbl] = {"total": 0, "passed": 0}
        per_table[tbl]["total"] += 1
        if results[-1]["status"] == "PASS":
            per_table[tbl]["passed"] += 1

    con.close()

    # ── Summary ───────────────────────────────────────────────────────
    total = len(all_tests)
    accuracy = (passed / total * 100) if total > 0 else 0
    avg_time = sum(r.get("time", 0) for r in results) / total if total > 0 else 0

    print(f"{'═' * 65}")
    print(f"  RESULTS — {MODEL}")
    print(f"{'─' * 65}")
    print(f"  Overall:  {passed}/{total} ({accuracy:.1f}%)")
    for tbl, stats in per_table.items():
        tbl_pct = stats["passed"] / stats["total"] * 100 if stats["total"] > 0 else 0
        print(f"  {tbl[:20]:20s}: {stats['passed']}/{stats['total']} ({tbl_pct:.1f}%)")
    print(f"  Failed:   {failed}/{total}")
    print(f"  Errors:   {errors}/{total}")
    print(f"  Avg time: {avg_time:.1f}s per query")
    print(f"{'═' * 65}")

    if accuracy >= 90:
        print(f"\n  🟢 EXCELLENT — Model generalizes well on real data")
    elif accuracy >= 70:
        print(f"\n  🟡 GOOD — Some edge cases need work")
    elif accuracy >= 50:
        print(f"\n  🟠 FAIR — Significant improvement needed")
    else:
        print(f"\n  🔴 POOR — Needs more few-shot examples or prompt tuning")

    # Save detailed results
    report_path = Path(__file__).resolve().parent / f"eval_realdata_{MODEL.replace(':', '_')}.json"
    with open(report_path, "w") as f:
        json.dump({
            "model": MODEL,
            "total": total,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "accuracy_pct": round(accuracy, 1),
            "per_table": {
                tbl: {
                    "passed": s["passed"],
                    "total": s["total"],
                    "pct": round(s["passed"] / s["total"] * 100, 1) if s["total"] > 0 else 0,
                }
                for tbl, s in per_table.items()
            },
            "avg_time_s": round(avg_time, 1),
            "results": results,
        }, f, indent=2, default=str)
    print(f"\n  Results saved to: {report_path}")

    # Show failures
    failures = [r for r in results if r["status"] != "PASS"]
    if failures:
        print(f"\n{'─' * 65}")
        print(f"  FAILURES & ERRORS:")
        for r in failures:
            print(f"    [{r.get('table', '?')}] {r['test']}: {r['status']}")
            if "sql" in r:
                print(f"      SQL: {r['sql'][:100]}")
            if "detail" in r:
                print(f"      Detail: {r['detail'][:100]}")

    return accuracy


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DataLensAI Real-Data Evaluator (Generic)")
    parser.add_argument("--csv", action="append", help="Path to a CSV file (can repeat for multiple)")
    parser.add_argument("--model", default=MODEL, help=f"Ollama model (default: {MODEL})")
    # Legacy arguments for backward compatibility
    parser.add_argument("--attendance", help="(Legacy) Path to attendance CSV")
    parser.add_argument("--transactions", help="(Legacy) Path to transaction CSV")
    args = parser.parse_args()

    if args.model:
        MODEL = args.model

    # Build list of CSV paths
    csv_paths = args.csv or []
    if args.attendance:
        csv_paths.append(args.attendance)
    if args.transactions:
        csv_paths.append(args.transactions)

    if not csv_paths:
        print("ERROR: Provide at least one CSV via --csv /path/to/file.csv")
        print("       Legacy: --attendance /path/to/att.csv --transactions /path/to/txn.csv")
        sys.exit(1)

    accuracy = asyncio.run(run_eval(csv_paths))
    sys.exit(0 if accuracy >= 70 else 1)
