#!/usr/bin/env python3
"""
DataLensAI — Multi-Dataset Regression Test

Loads multiple synthetic CSVs simultaneously and runs queries that
should target specific tables. Verifies that:
  1. The correct table is selected for each query
  2. SQL is generated (not prose)
  3. Queries execute without errors
  4. Results match expected ground truth

Compares results: same queries with individual vs. all datasets loaded.

Usage:
  python3 -m scripts.eval_multitable
  python3 -m scripts.eval_multitable --model qwen2.5:7b
"""

import argparse
import asyncio
import io
import json
import logging
import re
import sys
import time
from pathlib import Path

import duckdb
import pandas as pd

# ── Configuration ─────────────────────────────────────────────────────

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "datalens-sql"

# ── Synthetic Datasets ────────────────────────────────────────────────

DATASETS = {
    "library_loans": pd.DataFrame({
        "book_title": ["The Great Gatsby", "1984", "To Kill a Mockingbird", "Pride and Prejudice",
                       "The Catcher in the Rye", "Brave New World", "Moby Dick", "War and Peace",
                       "The Odyssey", "Hamlet", "Jane Eyre", "Wuthering Heights"],
        "author": ["F. Scott Fitzgerald", "George Orwell", "Harper Lee", "Jane Austen",
                   "J.D. Salinger", "Aldous Huxley", "Herman Melville", "Leo Tolstoy",
                   "Homer", "William Shakespeare", "Charlotte Brontë", "Emily Brontë"],
        "genre": ["Fiction", "Dystopian", "Fiction", "Romance",
                  "Fiction", "Dystopian", "Adventure", "Historical",
                  "Epic", "Tragedy", "Romance", "Romance"],
        "borrower_name": ["Alice Smith", "Bob Jones", "Carol White", "David Brown",
                          "Eve Davis", "Frank Wilson", "Grace Lee", "Henry Taylor",
                          "Ivy Clark", "Jack Lewis", "Kate Adams", "Leo Martin"],
        "loan_date": ["2024-01-15", "2024-01-20", "2024-02-01", "2024-02-10",
                      "2024-02-15", "2024-03-01", "2024-03-10", "2024-03-15",
                      "2024-04-01", "2024-04-05", "2024-04-10", "2024-04-20"],
        "return_date": ["2024-02-15", "2024-02-20", None, "2024-03-10",
                        "2024-03-15", None, "2024-04-10", "2024-04-15",
                        None, "2024-05-05", "2024-05-10", None],
        "status": ["Returned", "Returned", "Overdue", "Returned",
                   "Returned", "Overdue", "Returned", "Returned",
                   "On Loan", "Returned", "Returned", "On Loan"],
    }),
    "weather_readings": pd.DataFrame({
        "station": ["Downtown", "Airport", "Harbor", "Mountain",
                    "Downtown", "Airport", "Harbor", "Mountain",
                    "Downtown", "Airport", "Harbor", "Mountain"],
        "date": ["2024-06-01", "2024-06-01", "2024-06-01", "2024-06-01",
                 "2024-06-02", "2024-06-02", "2024-06-02", "2024-06-02",
                 "2024-06-03", "2024-06-03", "2024-06-03", "2024-06-03"],
        "temperature": [28.5, 26.3, 27.1, 22.0, 30.2, 28.1, 29.0, 23.5,
                        27.8, 25.9, 26.5, 21.2],
        "humidity": [65, 70, 80, 45, 60, 68, 78, 42, 67, 72, 82, 48],
        "wind_speed": [12.5, 18.3, 15.0, 25.0, 10.2, 20.1, 13.5, 28.0,
                       14.0, 16.5, 12.0, 30.5],
        "condition": ["Sunny", "Cloudy", "Rainy", "Windy",
                      "Sunny", "Sunny", "Cloudy", "Windy",
                      "Cloudy", "Rainy", "Rainy", "Clear"],
    }),
    "cafe_sales": pd.DataFrame({
        "item_name": ["Espresso", "Latte", "Cappuccino", "Mocha", "Americano",
                      "Espresso", "Latte", "Croissant", "Muffin", "Bagel",
                      "Latte", "Cappuccino", "Espresso", "Sandwich", "Cookie"],
        "category": ["Coffee", "Coffee", "Coffee", "Coffee", "Coffee",
                     "Coffee", "Coffee", "Pastry", "Pastry", "Pastry",
                     "Coffee", "Coffee", "Coffee", "Food", "Pastry"],
        "price": [3.50, 4.50, 4.00, 5.00, 3.00,
                  3.50, 4.50, 3.00, 2.50, 2.00,
                  4.50, 4.00, 3.50, 6.50, 2.00],
        "quantity": [2, 1, 3, 1, 2, 1, 2, 3, 1, 2,
                     1, 1, 2, 1, 3],
        "sale_date": ["2024-07-01", "2024-07-01", "2024-07-01", "2024-07-02", "2024-07-02",
                      "2024-07-02", "2024-07-03", "2024-07-03", "2024-07-03", "2024-07-04",
                      "2024-07-04", "2024-07-04", "2024-07-05", "2024-07-05", "2024-07-05"],
    }),
    "ecommerce_orders": pd.DataFrame({
        "order_id": [1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008, 1009, 1010],
        "customer_name": ["Alice", "Bob", "Carol", "David", "Eve",
                          "Frank", "Grace", "Henry", "Ivy", "Jack"],
        "product": ["Laptop", "Phone", "Tablet", "Headphones", "Monitor",
                    "Keyboard", "Mouse", "Webcam", "Speaker", "Charger"],
        "category": ["Electronics", "Electronics", "Electronics", "Accessories", "Electronics",
                     "Accessories", "Accessories", "Electronics", "Electronics", "Accessories"],
        "amount": [999.99, 699.50, 449.00, 89.99, 349.00,
                   59.99, 29.99, 79.99, 149.99, 24.99],
        "order_date": ["2024-08-01", "2024-08-02", "2024-08-03", "2024-08-04", "2024-08-05",
                       "2024-08-06", "2024-08-07", "2024-08-08", "2024-08-09", "2024-08-10"],
        "status": ["Delivered", "Shipped", "Delivered", "Processing", "Delivered",
                   "Shipped", "Delivered", "Processing", "Delivered", "Cancelled"],
    }),
    "support_tickets": pd.DataFrame({
        "ticket_id": [5001, 5002, 5003, 5004, 5005, 5006, 5007, 5008],
        "customer": ["Alice", "Bob", "Carol", "David", "Eve", "Frank", "Grace", "Henry"],
        "issue": ["Login failure", "Payment error", "Slow loading", "Feature request",
                  "Data export bug", "UI glitch", "Password reset", "API timeout"],
        "priority": ["High", "Critical", "Medium", "Low", "High", "Medium", "Low", "Critical"],
        "assigned_to": ["Team A", "Team B", "Team A", "Team C", "Team B", "Team A", "Team C", "Team B"],
        "created_date": ["2024-09-01", "2024-09-02", "2024-09-03", "2024-09-04",
                         "2024-09-05", "2024-09-06", "2024-09-07", "2024-09-08"],
        "resolved": ["Yes", "No", "Yes", "No", "Yes", "Yes", "No", "No"],
    }),
    # VARCHAR date columns with NULLs and invalid values — regression for TRY_CAST
    "project_tasks": pd.DataFrame({
        "task_name": ["Design homepage", "Build API", "Write tests", "Deploy v1",
                      "Fix login bug", "Update docs"],
        "assignee": ["Alice", "Bob", "Carol", "David", "Eve", "Frank"],
        "priority": ["High", "Medium", "High", "Critical", "Low", "Medium"],
        "due_date": ["2024-12-01", "2024-12-15", "2024-11-30", None,
                     "invalid-date", "2024-12-20"],
        "completed_date": ["2024-11-28", None, "2024-11-29", None, None, "2024-12-18"],
        "status": ["Done", "In Progress", "Done", "Blocked", "Open", "Done"],
    }),
}


# ── Test Cases ────────────────────────────────────────────────────────
# Each: (question, expected_table, validator_fn)

def _has_rows(rows, n, min_n):
    return n >= min_n, f"Expected ≥{min_n} rows, got {n}"


def _has_value_approx(rows, n, target, tol=0.05):
    if not rows:
        return False, f"No rows returned, expected ~{target}"
    for r in rows:
        for v in r.values():
            try:
                if abs(float(v) - target) <= max(abs(target * tol), 0.5):
                    return True, ""
            except (ValueError, TypeError):
                continue
    return False, f"Expected ~{target}, got {rows}"


def _mentions_table(sql, table_fragment):
    return table_fragment.lower() in sql.lower()


TEST_CASES = [
    # Library queries
    (
        "How many books are in the library?",
        "library_loans",
        lambda rows, n: (n == 1 and any(
            str(v) == "12" for r in rows for v in r.values()
        ), f"Expected 12, got {rows}"),
    ),
    (
        "Which books are overdue?",
        "library_loans",
        lambda rows, n: (
            n >= 2 and (
                # If status column is in the output, verify it says Overdue
                any("overdue" in str(r.get("status", "")).lower() for r in rows)
                # If status column is absent (LLM used it in WHERE but not SELECT),
                # verify returned rows match known overdue fixture entries
                or any(
                    any(str(v).strip().lower() in (
                        "carol white", "frank wilson",
                        "to kill a mockingbird", "brave new world",
                    ) for v in r.values() if v is not None)
                    for r in rows
                )
            ),
            f"Expected ≥2 overdue books (Carol White / To Kill a Mockingbird, "
            f"Frank Wilson / Brave New World), got {n} rows"
        ),
    ),
    (
        "How many books per genre?",
        "library_loans",
        lambda rows, n: _has_rows(rows, n, 3),
    ),

    # Weather queries
    (
        "What is the average temperature?",
        "weather_readings",
        lambda rows, n: _has_value_approx(rows, n, 26.34),
    ),
    (
        "Which station has the highest wind speed?",
        "weather_readings",
        lambda rows, n: (n >= 1 and any(
            "mountain" in str(r).lower() for r in rows
        ), f"Expected Mountain, got {rows}"),
    ),
    (
        "Show weather readings for Downtown",
        "weather_readings",
        lambda rows, n: (n >= 1 and all(
            "downtown" in str(r).lower() for r in rows
        ), f"Expected all Downtown, got {rows}"),
    ),

    # Café queries
    (
        "What is the total sales amount?",
        "cafe_sales",
        lambda rows, n: (n >= 1, f"Expected aggregate result, got {n} rows"),
    ),
    (
        "How many items per category in the café?",
        "cafe_sales",
        lambda rows, n: _has_rows(rows, n, 2),
    ),

    # E-commerce queries
    (
        "What is the most expensive order?",
        "ecommerce_orders",
        lambda rows, n: (n >= 1 and any(
            "laptop" in str(r).lower() or "999" in str(r) for r in rows
        ), f"Expected Laptop/999.99, got {rows}"),
    ),
    (
        "How many orders are delivered?",
        "ecommerce_orders",
        lambda rows, n: (n >= 1 and any(
            str(v) == "5" for r in rows for v in r.values()
        ), f"Expected 5 delivered, got {rows}"),
    ),

    # Support ticket queries
    (
        "How many support tickets are critical priority?",
        "support_tickets",
        lambda rows, n: (n >= 1 and any(
            str(v) == "2" for r in rows for v in r.values()
        ), f"Expected 2 critical, got {rows}"),
    ),
    (
        "Which tickets are unresolved?",
        "support_tickets",
        lambda rows, n: _has_rows(rows, n, 3),
    ),

    # Project tasks — VARCHAR date columns with NULLs and invalid values
    (
        "How many project tasks are completed?",
        "project_tasks",
        lambda rows, n: (n >= 1 and any(
            str(v) == "3" for r in rows for v in r.values()
        ), f"Expected 3 completed, got {rows}"),
    ),
    (
        "Which tasks have no due date?",
        "project_tasks",
        lambda rows, n: (n >= 1, f"Expected at least 1 row (Deploy v1), got {n}"),
    ),
    (
        "Show tasks due before December 15 2024",
        "project_tasks",
        lambda rows, n: _has_rows(rows, n, 2),
    ),
]


# ── Helpers ───────────────────────────────────────────────────────────

def _build_schema(con, table_name):
    """Build schema context matching production get_table_schema() behaviour.

    Includes:
      - DATE-LIKE annotations for VARCHAR columns storing dates
      - Categorical value hints for low-cardinality VARCHAR columns
    """
    cols = con.execute(f'DESCRIBE "{table_name}"').fetchall()
    row_count = con.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]

    # Detect date-like VARCHAR columns (mirrors _detect_date_like_varchars)
    date_re = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}")
    date_like = set()
    for col_name, col_type, *_ in cols:
        if col_type != "VARCHAR":
            continue
        try:
            samples = con.execute(
                f'SELECT DISTINCT "{col_name}" FROM "{table_name}" '
                f'WHERE "{col_name}" IS NOT NULL LIMIT 5'
            ).fetchall()
            if samples and sum(
                1 for s in samples if s[0] and date_re.match(str(s[0]).strip())
            ) >= max(len(samples) * 0.5, 1):
                date_like.add(col_name)
        except Exception:
            pass

    col_lines = []
    for c in cols:
        hint = ""
        if c[0] in date_like:
            hint = " ⚠ DATE-LIKE text — use TRY_CAST as DATE"
        col_lines.append(f'  - "{c[0]}" ({c[1]}){hint}')
    col_list = "\n".join(col_lines)

    schema = f'Table "{table_name}" ({row_count} rows):\n{col_list}'
    sample = con.execute(f'SELECT * FROM "{table_name}" LIMIT 2').fetchdf()
    if not sample.empty:
        schema += f"\n  Sample data:\n{sample.to_string(index=False)}"

    # Categorical value hints for low-cardinality VARCHAR columns
    cat_hints = []
    for col_name, col_type, *_ in cols:
        if col_type != "VARCHAR" or col_name in date_like:
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
        schema += "\n" + "\n".join(cat_hints)

    return schema


def _build_multi_schema(con, table_names):
    parts = [_build_schema(con, t) for t in table_names]
    return "\n\n".join(parts)


def _build_system_prompt(schema):
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
        return f"You are a DuckDB SQL expert. Output ONLY SQL.\n\nSCHEMA:\n{schema}"


async def _generate_sql(question, system_prompt):
    import httpx
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

    # Use the same extraction logic as production
    try:
        from app.services.ai_query_engine import _extract_sql
        return _extract_sql(content)
    except ImportError:
        content = content.strip()
        content = re.sub(r"^```(?:sql|duckdb)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        return content.strip()


# ── Main ──────────────────────────────────────────────────────────────

async def run_eval():
    con = duckdb.connect(":memory:")

    # Load all datasets
    table_names = []
    for name, df in DATASETS.items():
        con.execute(f'CREATE TABLE "{name}" AS SELECT * FROM df')
        table_names.append(name)

    all_schema = _build_multi_schema(con, table_names)
    all_prompt = _build_system_prompt(all_schema)

    print(f"\n{'═' * 70}")
    print(f"  DataLensAI Multi-Dataset Regression Test")
    print(f"  Model: {MODEL}")
    print(f"  Tables: {', '.join(table_names)}")
    print(f"  Tests: {len(TEST_CASES)}")
    print(f"{'═' * 70}\n")

    # ── Phase 1: Individual dataset tests (baseline) ──────────────────
    print(f"  PHASE 1: Individual dataset queries (baseline)\n")
    individual_results = []
    for i, (question, expected_table, validator) in enumerate(TEST_CASES, 1):
        single_schema = _build_schema(con, expected_table)
        single_prompt = _build_system_prompt(single_schema)

        print(f"  [{i:2d}/{len(TEST_CASES)}] [{expected_table[:18]}] {question}")
        try:
            t0 = time.time()
            sql = await _generate_sql(question, single_prompt)
            gen_time = time.time() - t0

            if not sql or not sql.upper().startswith(("SELECT", "WITH", "DESCRIBE")):
                print(f"        ✗ NO SQL — got: {(sql or '(empty)')[:80]}")
                individual_results.append({"status": "NO_SQL", "table": expected_table})
                continue

            # Execute with CAST→TRY_CAST auto-fix (mirrors production retry)
            try:
                result_df = con.execute(sql).fetchdf()
            except duckdb.Error as cast_err:
                if "Conversion Error" in str(cast_err) and "CAST(" in sql:
                    sql = re.sub(r'\bCAST\(', 'TRY_CAST(', sql)
                    result_df = con.execute(sql).fetchdf()
                else:
                    raise
            rows = result_df.to_dict(orient="records")
            ok, detail = validator(rows, len(result_df))
            status = "PASS" if ok else "FAIL"
            print(f"        {'✓' if ok else '✗'} {status} ({gen_time:.1f}s) — {detail if not ok else ''}")
            individual_results.append({"status": status, "table": expected_table, "sql": sql})

        except Exception as e:
            print(f"        ✗ ERROR — {e}")
            individual_results.append({"status": "ERROR", "table": expected_table, "detail": str(e)})
        print()

    # ── Phase 2: All datasets loaded (the actual test) ────────────────
    print(f"\n  PHASE 2: ALL datasets loaded simultaneously\n")
    multi_results = []
    for i, (question, expected_table, validator) in enumerate(TEST_CASES, 1):
        print(f"  [{i:2d}/{len(TEST_CASES)}] [{expected_table[:18]}] {question}")
        try:
            t0 = time.time()
            sql = await _generate_sql(question, all_prompt)
            gen_time = time.time() - t0

            if not sql or not sql.upper().startswith(("SELECT", "WITH", "DESCRIBE")):
                print(f"        ✗ NO SQL — got: {(sql or '(empty)')[:80]}")
                multi_results.append({"status": "NO_SQL", "table": expected_table, "raw": sql})
                continue

            # Check if the correct table was targeted
            targeted_correct = _mentions_table(sql, expected_table)
            table_note = "✓ correct table" if targeted_correct else "⚠ wrong table"

            # Execute with CAST→TRY_CAST auto-fix (mirrors production retry)
            try:
                result_df = con.execute(sql).fetchdf()
            except duckdb.Error as cast_err:
                if "Conversion Error" in str(cast_err) and "CAST(" in sql:
                    sql = re.sub(r'\bCAST\(', 'TRY_CAST(', sql)
                    result_df = con.execute(sql).fetchdf()
                else:
                    raise
            rows = result_df.to_dict(orient="records")
            ok, detail = validator(rows, len(result_df))
            status = "PASS" if ok else "FAIL"
            print(f"        SQL: {sql[:100]}{'...' if len(sql) > 100 else ''}")
            print(f"        {'✓' if ok else '✗'} {status} ({gen_time:.1f}s) [{table_note}] {detail if not ok else ''}")
            multi_results.append({
                "status": status, "table": expected_table, "sql": sql,
                "correct_table": targeted_correct,
            })

        except Exception as e:
            print(f"        ✗ ERROR — {e}")
            multi_results.append({"status": "ERROR", "table": expected_table, "detail": str(e)})
        print()

    con.close()

    # ── Summary ───────────────────────────────────────────────────────
    ind_pass = sum(1 for r in individual_results if r["status"] == "PASS")
    multi_pass = sum(1 for r in multi_results if r["status"] == "PASS")
    multi_correct_table = sum(1 for r in multi_results if r.get("correct_table", False))
    total = len(TEST_CASES)

    print(f"{'═' * 70}")
    print(f"  RESULTS")
    print(f"{'─' * 70}")
    print(f"  Individual (baseline): {ind_pass}/{total} ({ind_pass/total*100:.0f}%)")
    print(f"  Multi-dataset:         {multi_pass}/{total} ({multi_pass/total*100:.0f}%)")
    print(f"  Correct table chosen:  {multi_correct_table}/{total} ({multi_correct_table/total*100:.0f}%)")
    print(f"{'─' * 70}")

    regressions = []
    for i, (ind, multi) in enumerate(zip(individual_results, multi_results)):
        if ind["status"] == "PASS" and multi["status"] != "PASS":
            regressions.append((TEST_CASES[i][0], TEST_CASES[i][1], multi.get("detail", multi["status"])))

    if regressions:
        print(f"\n  ⚠ REGRESSIONS ({len(regressions)} queries pass individually but fail with all datasets):")
        for q, t, d in regressions:
            print(f"    - [{t}] {q}")
            print(f"      → {d[:100]}")
    else:
        print(f"\n  ✅ No regressions — multi-dataset performs as well as individual")

    print(f"{'═' * 70}")

    # Save results
    report_path = Path(__file__).resolve().parent / f"eval_multitable_{MODEL.replace(':', '_')}.json"
    with open(report_path, "w") as f:
        json.dump({
            "model": MODEL,
            "individual_pass": ind_pass,
            "multi_pass": multi_pass,
            "correct_table_selection": multi_correct_table,
            "total": total,
            "regressions": len(regressions),
        }, f, indent=2)
    print(f"\n  Results saved to: {report_path}")

    return multi_pass, total


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DataLensAI Multi-Dataset Regression Test")
    parser.add_argument("--model", default=MODEL, help=f"Ollama model (default: {MODEL})")
    args = parser.parse_args()
    if args.model:
        MODEL = args.model

    multi_pass, total = asyncio.run(run_eval())
    sys.exit(0 if multi_pass >= total * 0.7 else 1)
