"""
Tests for the AI query engine — fallback SQL generator and validation.
These run entirely offline, no Ollama or MongoDB needed.
"""

import pytest
from app.services.ai_query_engine import _fallback_sql, _sanitise_sql, _build_basic_answer


# ── Schema fixture ──────────────────────────────────────────────────

SAMPLE_SCHEMA = '''Table "sales_data_abc123" (500 rows): date (DATE), product (VARCHAR), quantity (INTEGER), revenue (DOUBLE), region (VARCHAR)
  Sample data:
       date    product  quantity  revenue  region
 2024-01-01     Widget        10   150.00    East
 2024-01-02     Gadget         5   225.50    West
 2024-01-03     Widget         8   120.00   North'''


# ── Fallback SQL tests ──────────────────────────────────────────────

class TestFallbackSQL:
    def test_count_query(self):
        sql = _fallback_sql("how many rows are there?", SAMPLE_SCHEMA)
        assert "COUNT(*)" in sql.upper()
        assert "sales_data_abc123" in sql

    def test_average_query(self):
        sql = _fallback_sql("what is the average revenue?", SAMPLE_SCHEMA)
        assert "AVG" in sql.upper()
        assert "revenue" in sql

    def test_sum_query(self):
        sql = _fallback_sql("total quantity sold", SAMPLE_SCHEMA)
        assert "SUM" in sql.upper()
        assert "quantity" in sql

    def test_show_all(self):
        sql = _fallback_sql("show all data", SAMPLE_SCHEMA)
        assert "SELECT *" in sql
        assert "LIMIT" in sql

    def test_max_query(self):
        sql = _fallback_sql("highest revenue", SAMPLE_SCHEMA)
        assert "ORDER BY" in sql.upper()
        assert "DESC" in sql.upper()

    def test_distinct_query(self):
        sql = _fallback_sql("unique regions", SAMPLE_SCHEMA)
        assert "DISTINCT" in sql.upper()

    def test_group_by(self):
        sql = _fallback_sql("breakdown by region", SAMPLE_SCHEMA)
        assert "GROUP BY" in sql.upper()

    def test_describe(self):
        sql = _fallback_sql("what columns are there?", SAMPLE_SCHEMA)
        assert "DESCRIBE" in sql.upper()

    def test_first_rows(self):
        sql = _fallback_sql("show first 5 rows", SAMPLE_SCHEMA)
        assert "LIMIT 5" in sql

    def test_default_preview(self):
        sql = _fallback_sql("something random", SAMPLE_SCHEMA)
        assert "SELECT *" in sql
        assert "LIMIT 20" in sql

    def test_no_tables(self):
        sql = _fallback_sql("anything", "No tables here")
        assert "CANNOT_ANSWER" in sql

    def test_count_by_category(self):
        sql = _fallback_sql("how many products per region?", SAMPLE_SCHEMA)
        assert "COUNT" in sql.upper()
        assert "GROUP BY" in sql.upper()

    def test_sum_specific_column(self):
        sql = _fallback_sql("sum of revenue", SAMPLE_SCHEMA)
        assert "SUM" in sql.upper()
        assert "revenue" in sql


# ── SQL sanitisation tests ──────────────────────────────────────────

class TestSanitiseSQL:
    def test_select_passes(self):
        result = _sanitise_sql("SELECT * FROM t")
        assert result.startswith("SELECT")

    def test_limit_added(self):
        result = _sanitise_sql("SELECT * FROM t")
        assert "LIMIT 500" in result

    def test_existing_limit_preserved(self):
        result = _sanitise_sql("SELECT * FROM t LIMIT 10")
        assert "LIMIT 10" in result
        assert result.count("LIMIT") == 1

    def test_aggregation_no_limit(self):
        result = _sanitise_sql("SELECT COUNT(*) FROM t")
        assert "LIMIT" not in result

    def test_drop_blocked(self):
        with pytest.raises(ValueError, match="Only SELECT"):
            _sanitise_sql("DROP TABLE users")

    def test_insert_blocked(self):
        with pytest.raises(ValueError, match="Only SELECT"):
            _sanitise_sql("INSERT INTO t VALUES (1)")

    def test_delete_blocked(self):
        with pytest.raises(ValueError, match="Only SELECT"):
            _sanitise_sql("DELETE FROM t")

    def test_update_blocked(self):
        with pytest.raises(ValueError, match="Only SELECT"):
            _sanitise_sql("UPDATE t SET x = 1")

    def test_create_blocked(self):
        with pytest.raises(ValueError, match="Only SELECT"):
            _sanitise_sql("CREATE TABLE t (id INT)")

    def test_copy_blocked(self):
        with pytest.raises(ValueError, match="Only SELECT"):
            _sanitise_sql("COPY t TO '/tmp/out.csv'")

    def test_markdown_fences_stripped(self):
        result = _sanitise_sql("```sql\nSELECT 1\n```")
        assert result.startswith("SELECT")
        assert "```" not in result

    def test_sum_aggregation_no_limit(self):
        result = _sanitise_sql("SELECT SUM(revenue) FROM sales")
        assert "LIMIT" not in result


# ── Basic answer builder tests ──────────────────────────────────────

class TestBuildBasicAnswer:
    def test_empty_rows(self):
        result = _build_basic_answer([], 0)
        assert "no results" in result["answer"].lower()
        assert result["chart_config"] is None

    def test_single_value(self):
        result = _build_basic_answer([{"total": 42}], 1)
        assert "42" in result["answer"]

    def test_multiple_rows(self):
        rows = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
        result = _build_basic_answer(rows, 2)
        assert "2 result" in result["answer"]
        assert "2 column" in result["answer"]
