"""
Tests for cross-table shared column detection in duckdb_engine.py.
"""

import shutil
from pathlib import Path

import pandas as pd
import pytest

from app.services.duckdb_engine import (
    register_dataframe,
    detect_shared_columns,
    drop_table,
    list_tables,
)

TEST_USER = "test_cross_table_user_9999"
DB_DIR = Path("data/duckdb")


@pytest.fixture(autouse=True)
def cleanup():
    """Remove test user's DuckDB file after each test."""
    yield
    db_path = DB_DIR / f"{TEST_USER}.duckdb"
    if db_path.exists():
        db_path.unlink()


def _register(name: str, df: pd.DataFrame, dataset_id: str = "aaaaaa") -> str:
    """Helper to register a table and return the table name."""
    table_name, _, _, _ = register_dataframe(TEST_USER, dataset_id, name, df)
    return table_name


class TestDetectSharedColumns:
    def test_no_tables_returns_empty(self):
        assert detect_shared_columns(TEST_USER) == ""

    def test_single_table_returns_empty(self):
        _register("employees.csv", pd.DataFrame({
            "emp_id": [1, 2],
            "name": ["Alice", "Bob"],
        }), "aaa111")
        assert detect_shared_columns(TEST_USER) == ""

    def test_two_tables_shared_columns(self):
        _register("attendance.csv", pd.DataFrame({
            "employee_name": ["Alice", "Bob"],
            "status": ["Present", "Absent"],
            "date": ["2024-01-15", "2024-01-15"],
        }), "aaa111")
        _register("salary.csv", pd.DataFrame({
            "employee_name": ["Alice", "Bob"],
            "salary": [80000, 75000],
            "department": ["Eng", "Mkt"],
        }), "bbb222")

        result = detect_shared_columns(TEST_USER)
        assert "employee_name" in result
        assert "JOIN" in result or "UNION" in result

    def test_no_shared_columns(self):
        _register("file_a.csv", pd.DataFrame({
            "col_a": [1, 2],
            "col_b": [3, 4],
        }), "aaa111")
        _register("file_b.csv", pd.DataFrame({
            "col_x": [5, 6],
            "col_y": [7, 8],
        }), "bbb222")

        result = detect_shared_columns(TEST_USER)
        assert result == ""

    def test_prioritizes_identifier_columns(self):
        _register("t1.csv", pd.DataFrame({
            "emp_id": [1],
            "name": ["Alice"],
            "date": ["2024-01-01"],
            "value": [100],
        }), "aaa111")
        _register("t2.csv", pd.DataFrame({
            "emp_id": [1],
            "name": ["Alice"],
            "date": ["2024-01-01"],
            "score": [95],
        }), "bbb222")

        result = detect_shared_columns(TEST_USER)
        # emp_id and name should appear before date in the hint
        assert "emp_id" in result
        assert "name" in result

    def test_three_tables_multiple_pairs(self):
        _register("a.csv", pd.DataFrame({
            "id": [1], "name": ["X"], "dept": ["A"],
        }), "aaa111")
        _register("b.csv", pd.DataFrame({
            "id": [1], "name": ["X"], "salary": [100],
        }), "bbb222")
        _register("c.csv", pd.DataFrame({
            "id": [1], "score": [95],
        }), "ccc333")

        result = detect_shared_columns(TEST_USER)
        # Should have hints for (a,b), (a,c), (b,c) pairs
        assert result.count("share columns") >= 2

    def test_filtered_by_table_names(self):
        t1 = _register("alpha.csv", pd.DataFrame({
            "id": [1], "val": [10],
        }), "aaa111")
        t2 = _register("bravo.csv", pd.DataFrame({
            "id": [1], "val": [20],
        }), "bbb222")
        t3 = _register("charlie.csv", pd.DataFrame({
            "id": [1], "other": [30],
        }), "ccc333")

        # Only look at t1 and t3
        result = detect_shared_columns(TEST_USER, [t1, t3])
        assert "id" in result
        # t2 should not be mentioned
        assert t2 not in result