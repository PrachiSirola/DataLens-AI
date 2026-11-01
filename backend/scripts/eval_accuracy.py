#!/usr/bin/env python3
"""
DataLensAI — SQL Accuracy Evaluation Harness (Generic)

Measures how accurately the LLM generates correct DuckDB SQL for
common analytical questions. Works with ANY CSV — auto-generates
test cases from the schema.

Usage:
  # From backend/ directory:
  python -m scripts.eval_accuracy

  # With a specific test CSV:
  python scripts/eval_accuracy.py --csv /path/to/data.csv

  # Run only against a specific synthetic dataset:
  python scripts/eval_accuracy.py --suite inventory
  python scripts/eval_accuracy.py --suite students
  python scripts/eval_accuracy.py --suite weather

  # Run all built-in suites:
  python scripts/eval_accuracy.py --suite all

Requirements:
  - Ollama running with the datalens-sql model
  - pip install httpx duckdb pandas tabulate
"""

import argparse
import asyncio
import json
import logging
import re
import sys
import time
from io import StringIO
from pathlib import Path

import duckdb
import httpx
import pandas as pd

# ── Configuration ─────────────────────────────────────────────────────

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "datalens-sql"  # change to "qwen2.5:7b" to compare base vs custom

# ── Synthetic datasets for diverse testing ─────────────────────────────

DATASETS = {
    # Original HR regression dataset (kept for backward compat)
    "employees": {
        "csv": """\
employee,department,salary,date,status
Prachi Sirola,Engineering,75000,2024-09-15,Present
Nikhil Sharma,Engineering,82000,2024-09-15,Absent
Anjali Verma,Marketing,68000,2024-09-15,Present
Rohit Patel,Sales,71000,2024-09-15,Present
Sneha Gupta,Engineering,79000,2024-09-15,Half Day
Prachi Sirola,Engineering,75000,2024-09-16,Present
Nikhil Sharma,Engineering,82000,2024-09-16,Present
Anjali Verma,Marketing,68000,2024-09-16,Absent
Rohit Patel,Sales,71000,2024-09-16,Absent
Sneha Gupta,Engineering,79000,2024-09-16,Present
Prachi Sirola,Engineering,75000,2024-09-17,Absent
Nikhil Sharma,Engineering,82000,2024-09-17,Present
Anjali Verma,Marketing,68000,2024-09-17,Present
Rohit Patel,Sales,71000,2024-09-17,Present
Sneha Gupta,Engineering,79000,2024-09-17,Present
""",
        "table_name": "employees",
    },

    # Product inventory dataset
    "inventory": {
        "csv": """\
product_id,product_name,category,price,stock_quantity,warehouse,supplier,last_restocked
P001,Widget Alpha,Electronics,29.99,150,Warehouse A,TechCorp,2024-03-10
P002,Gadget Beta,Electronics,49.99,80,Warehouse B,TechCorp,2024-03-12
P003,Organic Flour,Groceries,5.49,500,Warehouse A,FarmFresh,2024-03-08
P004,Steel Bolt M8,Hardware,0.75,10000,Warehouse C,MetalWorks,2024-02-28
P005,Widget Gamma,Electronics,34.99,0,Warehouse A,TechCorp,2024-01-15
P006,Almond Milk,Groceries,3.99,200,Warehouse B,FarmFresh,2024-03-11
P007,Copper Wire 2mm,Hardware,12.50,350,Warehouse C,MetalWorks,2024-03-05
P008,Bluetooth Speaker,Electronics,79.99,45,Warehouse A,AudioMax,2024-03-09
P009,Whole Wheat Bread,Groceries,4.29,120,Warehouse A,FarmFresh,2024-03-13
P010,Hex Nut M8,Hardware,0.50,15000,Warehouse C,MetalWorks,2024-03-01
P011,Widget Alpha,Electronics,29.99,200,Warehouse B,TechCorp,2024-03-14
P012,LED Panel,Electronics,89.99,30,Warehouse A,LightCo,2024-03-02
""",
        "table_name": "product_inventory",
    },

    # Student grades dataset
    "students": {
        "csv": """\
student_id,student_name,subject,score,grade,semester,enrollment_year
S101,Alice Chen,Mathematics,92,A,Fall 2024,2022
S102,Bob Martinez,Mathematics,78,B+,Fall 2024,2023
S103,Chloe Kim,Mathematics,55,D,Fall 2024,2022
S101,Alice Chen,Physics,88,A-,Fall 2024,2022
S102,Bob Martinez,Physics,65,C,Fall 2024,2023
S103,Chloe Kim,Physics,72,B-,Fall 2024,2022
S104,David Okonkwo,Mathematics,95,A+,Fall 2024,2024
S104,David Okonkwo,Physics,91,A,Fall 2024,2024
S105,Emma Johansson,Mathematics,44,F,Fall 2024,2023
S105,Emma Johansson,Physics,60,C-,Fall 2024,2023
S101,Alice Chen,Chemistry,85,A-,Fall 2024,2022
S102,Bob Martinez,Chemistry,70,B-,Fall 2024,2023
S103,Chloe Kim,Chemistry,48,F,Fall 2024,2022
S104,David Okonkwo,Chemistry,93,A,Fall 2024,2024
S105,Emma Johansson,Chemistry,58,D+,Fall 2024,2023
""",
        "table_name": "student_grades",
    },

    # Weather station dataset
    "weather": {
        "csv": """\
station_id,city,date,temperature_c,humidity_pct,wind_speed_kmh,condition,rainfall_mm
WS01,Mumbai,2024-06-01,34.2,78,12,Sunny,0.0
WS02,Delhi,2024-06-01,42.1,35,8,Hot,0.0
WS03,Bangalore,2024-06-01,28.5,65,15,Cloudy,2.5
WS01,Mumbai,2024-06-02,33.8,82,18,Rainy,15.3
WS02,Delhi,2024-06-02,40.5,40,6,Hot,0.0
WS03,Bangalore,2024-06-02,27.9,70,20,Rainy,8.7
WS01,Mumbai,2024-06-03,31.2,85,22,Rainy,28.1
WS02,Delhi,2024-06-03,38.7,45,10,Cloudy,0.5
WS03,Bangalore,2024-06-03,29.1,60,12,Sunny,0.0
WS01,Mumbai,2024-06-04,32.5,80,14,Cloudy,3.2
WS02,Delhi,2024-06-04,41.3,32,5,Hot,0.0
WS03,Bangalore,2024-06-04,28.0,68,16,Sunny,0.0
""",
        "table_name": "weather_readings",
    },
}


# ── Parameterized test generator ─────────────────────────────────────


def _auto_generate_tests(
    con: duckdb.DuckDBPyConnection, table_name: str, df: pd.DataFrame
) -> list[tuple[str, str, callable]]:
    """
    Auto-generate test cases from ANY CSV's actual schema and data.
    Returns generic SQL pattern tests that work regardless of domain.
    """
    cols = [c[0] for c in con.execute(f'DESCRIBE "{table_name}"').fetchall()]
    col_types = {c[0]: c[1] for c in con.execute(f'DESCRIBE "{table_name}"').fetchall()}
    row_count = len(df)

    numeric_cols = [c for c, t in col_types.items() if t in ("BIGINT", "INTEGER", "DOUBLE", "FLOAT", "DECIMAL")]
    varchar_cols = [c for c, t in col_types.items() if t == "VARCHAR"]
    date_cols = [c for c, t in col_types.items() if "DATE" in t or "TIMESTAMP" in t]

    tests = []

    # 1. COUNT total rows
    tests.append((
        "How many rows are in the table?",
        "count_total",
        lambda rows, n: (
            n == 1
            and len(rows) == 1
            and row_count in [int(float(v)) for v in rows[0].values() if v is not None and str(v).replace(".", "").replace("-", "").isdigit()],
            f"Expected {row_count}, got {rows}",
        ),
    ))

    # 2. COUNT GROUP BY (if a varchar column exists)
    if varchar_cols:
        group_col = varchar_cols[0]
        expected_groups = df[group_col].nunique()
        tests.append((
            f"How many rows per {group_col.replace('_', ' ')}?",
            "count_groupby",
            lambda rows, n, eg=expected_groups: (
                n >= 2 or eg < 2,
                f"Expected ≥2 groups (or <2 unique), got {n} rows",
            ),
        ))

    # 3. AVG of a numeric column
    if numeric_cols:
        num_col = numeric_cols[0]
        expected_avg = round(df[num_col].mean(), 2)
        tests.append((
            f"What is the average {num_col.replace('_', ' ')}?",
            "average",
            lambda rows, n, ea=expected_avg: (
                n == 1
                and any(
                    abs(float(v) - ea) < max(ea * 0.01, 1)
                    for v in rows[0].values()
                    if v is not None and _is_numeric(v)
                ),
                f"Expected avg ~{ea}, got {rows}",
            ),
        ))

    # 4. SUM of a numeric column
    if numeric_cols:
        num_col = numeric_cols[0]
        expected_sum = round(float(df[num_col].sum()), 2)
        tests.append((
            f"What is the total {num_col.replace('_', ' ')}?",
            "sum",
            lambda rows, n, es=expected_sum: (
                n == 1
                and any(
                    abs(float(v) - es) < max(es * 0.01, 1)
                    for v in rows[0].values()
                    if v is not None and _is_numeric(v)
                ),
                f"Expected sum ~{es}, got {rows}",
            ),
        ))

    # 5. MAX
    if numeric_cols:
        num_col = numeric_cols[0]
        expected_max = float(df[num_col].max())
        tests.append((
            f"What is the maximum {num_col.replace('_', ' ')}?",
            "max",
            lambda rows, n, em=expected_max: (
                n == 1
                and any(
                    abs(float(v) - em) < max(em * 0.001, 0.1)
                    for v in rows[0].values()
                    if v is not None and _is_numeric(v)
                ),
                f"Expected max {em}, got {rows}",
            ),
        ))

    # 6. MIN
    if numeric_cols:
        num_col = numeric_cols[0]
        expected_min = float(df[num_col].min())
        tests.append((
            f"What is the minimum {num_col.replace('_', ' ')}?",
            "min",
            lambda rows, n, em=expected_min: (
                n == 1
                and any(
                    abs(float(v) - em) < max(abs(em) * 0.001, 0.1)
                    for v in rows[0].values()
                    if v is not None and _is_numeric(v)
                ),
                f"Expected min {em}, got {rows}",
            ),
        ))

    # 7. TOP N
    if numeric_cols:
        num_col = numeric_cols[0]
        tests.append((
            f"Show top 3 rows by highest {num_col.replace('_', ' ')}",
            "top_n",
            lambda rows, n: (
                len(rows) == 3,
                f"Expected 3 rows, got {len(rows)}",
            ),
        ))

    # 8. DISTINCT values
    if varchar_cols:
        col = varchar_cols[0]
        expected_distinct = df[col].nunique()
        tests.append((
            f"What are the unique {col.replace('_', ' ')} values?",
            "distinct",
            lambda rows, n, ed=expected_distinct: (
                n >= min(ed, 2),
                f"Expected ≥{min(ed, 2)} distinct values, got {n}",
            ),
        ))

    # 9. Text search (if a name-like varchar column exists)
    name_cols = [c for c in varchar_cols if any(w in c.lower() for w in ("name", "title", "label", "city", "product"))]
    if name_cols:
        name_col = name_cols[0]
        sample_value = str(df[name_col].iloc[0])
        # Use first word of the name for partial search
        search_term = sample_value.split()[0] if " " in sample_value else sample_value[:5]
        tests.append((
            f"Show all records for {search_term}",
            "name_search",
            lambda rows, n, st=search_term.lower(): (
                n >= 1
                and any(st in str(r).lower() for r in rows),
                f"Expected rows containing '{st}', got {rows}",
            ),
        ))

    # 10. GROUP BY + AGG
    if varchar_cols and numeric_cols:
        group_col = varchar_cols[0]
        num_col = numeric_cols[0]
        tests.append((
            f"What is the average {num_col.replace('_', ' ')} by {group_col.replace('_', ' ')}?",
            "groupby_agg",
            lambda rows, n: (
                n >= 1,
                f"Expected ≥1 group, got {n} rows",
            ),
        ))

    # 11. Column listing
    tests.append((
        "What are the column names?",
        "describe",
        lambda rows, n, nc=len(cols): (
            n >= min(nc, 3),
            f"Expected ≥{min(nc, 3)} columns listed, got {n}",
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


# ── SQL generation (mirrors ai_query_engine._generate_sql) ────────────


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


# ── Main evaluation loop ──────────────────────────────────────────────


async def run_eval(csv_path: str | None = None, suite: str | None = None):
    """Run the evaluation suite."""
    suites_to_run = []

    if csv_path:
        # Custom CSV provided
        df = pd.read_csv(csv_path)
        table_name = Path(csv_path).stem.lower()
        table_name = re.sub(r"[^a-z0-9_]", "_", table_name)
        suites_to_run.append(("custom", table_name, df))
    elif suite and suite != "all":
        # Specific built-in suite
        if suite not in DATASETS:
            print(f"Unknown suite '{suite}'. Available: {', '.join(DATASETS.keys())}")
            sys.exit(1)
        ds = DATASETS[suite]
        df = pd.read_csv(StringIO(ds["csv"]))
        suites_to_run.append((suite, ds["table_name"], df))
    elif suite == "all":
        # Run all built-in suites
        for name, ds in DATASETS.items():
            df = pd.read_csv(StringIO(ds["csv"]))
            suites_to_run.append((name, ds["table_name"], df))
    else:
        # Default: run employees (legacy) + all diverse suites
        for name, ds in DATASETS.items():
            df = pd.read_csv(StringIO(ds["csv"]))
            suites_to_run.append((name, ds["table_name"], df))

    all_results = {}
    total_passed = 0
    total_tests = 0

    for suite_name, table_name, df in suites_to_run:
        con = duckdb.connect(":memory:")
        con.execute(f'CREATE TABLE "{table_name}" AS SELECT * FROM df')
        schema = _build_schema(con, table_name)
        system_prompt = _build_system_prompt(schema)

        tests = _auto_generate_tests(con, table_name, df)

        print(f"\n{'═' * 60}")
        print(f"  Suite: {suite_name}")
        print(f"  Model: {MODEL}")
        print(f"  Table: {table_name} ({len(df)} rows, {len(df.columns)} cols)")
        print(f"  Tests: {len(tests)}")
        print(f"{'═' * 60}\n")

        results = []
        passed = 0
        failed = 0
        errors = 0

        for i, (question, test_id, validator) in enumerate(tests, 1):
            print(f"  [{i:2d}/{len(tests)}] {question}")

            try:
                t0 = time.time()
                sql = await _generate_sql(question, system_prompt)
                gen_time = time.time() - t0

                print(f"        SQL: {sql[:100]}{'...' if len(sql) > 100 else ''}")
                print(f"        Time: {gen_time:.1f}s")

                try:
                    result_df = con.execute(sql).fetchdf()
                    rows = result_df.to_dict(orient="records")
                    row_count = len(result_df)

                    ok, detail = validator(rows, row_count)
                    if ok:
                        print(f"        ✓ PASS")
                        passed += 1
                        results.append({"test": test_id, "status": "PASS", "time": gen_time})
                    else:
                        print(f"        ✗ FAIL — {detail}")
                        failed += 1
                        results.append({"test": test_id, "status": "FAIL", "detail": detail, "sql": sql, "time": gen_time})

                except duckdb.Error as e:
                    print(f"        ✗ SQL ERROR — {e}")
                    errors += 1
                    results.append({"test": test_id, "status": "ERROR", "detail": str(e), "sql": sql, "time": gen_time})

            except Exception as e:
                print(f"        ✗ GENERATION ERROR — {e}")
                errors += 1
                results.append({"test": test_id, "status": "ERROR", "detail": str(e)})

            print()

        con.close()

        total = len(tests)
        accuracy = (passed / total * 100) if total > 0 else 0
        avg_time = sum(r.get("time", 0) for r in results) / total if total > 0 else 0

        total_passed += passed
        total_tests += total

        print(f"{'─' * 60}")
        print(f"  {suite_name}: {passed}/{total} passed ({accuracy:.1f}%)")
        print(f"  Avg time: {avg_time:.1f}s per query")

        all_results[suite_name] = {
            "table": table_name,
            "total": total,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "accuracy_pct": round(accuracy, 1),
            "avg_time_s": round(avg_time, 1),
            "results": results,
        }

    # ── Overall Summary ──────────────────────────────────────────────
    overall_accuracy = (total_passed / total_tests * 100) if total_tests > 0 else 0

    print(f"\n{'═' * 60}")
    print(f"  OVERALL RESULTS")
    print(f"{'─' * 60}")
    for name, data in all_results.items():
        print(f"  {name:20s}: {data['passed']}/{data['total']} ({data['accuracy_pct']}%)")
    print(f"{'─' * 60}")
    print(f"  {'TOTAL':20s}: {total_passed}/{total_tests} ({overall_accuracy:.1f}%)")
    print(f"{'═' * 60}")

    if overall_accuracy >= 90:
        print(f"\n  🟢 EXCELLENT — Model generalizes well across domains")
    elif overall_accuracy >= 70:
        print(f"\n  🟡 GOOD — Some edge cases need work")
    elif overall_accuracy >= 50:
        print(f"\n  🟠 FAIR — Significant improvement needed")
    else:
        print(f"\n  🔴 POOR — Consider more few-shot examples or prompt tuning")

    # Save detailed results
    report_path = Path(__file__).resolve().parent / "eval_results.json"
    with open(report_path, "w") as f:
        json.dump({
            "model": MODEL,
            "overall_accuracy_pct": round(overall_accuracy, 1),
            "total_passed": total_passed,
            "total_tests": total_tests,
            "suites": all_results,
        }, f, indent=2, default=str)
    print(f"\n  Detailed results saved to: {report_path}")

    # Show failures
    for name, data in all_results.items():
        failures = [r for r in data["results"] if r["status"] != "PASS"]
        if failures:
            print(f"\n{'─' * 60}")
            print(f"  FAILURES in {name}:")
            for r in failures:
                print(f"    {r['test']}: {r['status']}")
                if "sql" in r:
                    print(f"      SQL: {r['sql'][:80]}")
                if "detail" in r:
                    print(f"      Detail: {r['detail'][:80]}")

    return overall_accuracy


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DataLensAI SQL Accuracy Evaluator")
    parser.add_argument("--csv", help="Path to a CSV file to test against")
    parser.add_argument("--suite", help="Built-in suite to run: employees, inventory, students, weather, or 'all'")
    parser.add_argument("--model", default=MODEL, help=f"Ollama model name (default: {MODEL})")
    args = parser.parse_args()

    if args.model:
        MODEL = args.model

    accuracy = asyncio.run(run_eval(args.csv, args.suite))
    sys.exit(0 if accuracy >= 70 else 1)
