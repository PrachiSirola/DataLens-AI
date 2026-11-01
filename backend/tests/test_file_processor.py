"""
Tests for file_processor.py — PDF multi-pass extraction, Excel support,
column cleaning, dedup, and type coercion.
"""

import io
import pytest
import pandas as pd

from app.services.file_processor import (
    _clean_col,
    _dedup_columns,
    _coerce_types,
    _group_compatible_tables,
    _parse_space_separated_text,
    process_csv,
    process_excel,
    process_file,
    FileProcessingError,
)


# ── _clean_col ──────────────────────────────────────────────────────

class TestCleanCol:
    def test_basic_lowercase_underscores(self):
        assert _clean_col("First Name") == "first_name"

    def test_strips_brackets(self):
        assert _clean_col("Email Address [Required]") == "email_address"

    def test_strips_parens(self):
        assert _clean_col("Revenue ($)") == "revenue"

    def test_hyphens_and_dots(self):
        assert _clean_col("Date-Of-Birth.Year") == "date_of_birth_year"

    def test_special_chars_removed(self):
        assert _clean_col("Name @#$%") == "name"

    def test_empty_string(self):
        assert _clean_col("") == "column"

    def test_only_special_chars(self):
        assert _clean_col("@#$%") == "column"

    def test_multiple_spaces(self):
        assert _clean_col("  Sales   Revenue  ") == "sales_revenue"


# ── _dedup_columns ──────────────────────────────────────────────────

class TestDedupColumns:
    def test_no_dupes(self):
        assert _dedup_columns(["a", "b", "c"]) == ["a", "b", "c"]

    def test_simple_dupes(self):
        assert _dedup_columns(["x", "x", "x"]) == ["x", "x_2", "x_3"]

    def test_mixed(self):
        result = _dedup_columns(["name", "age", "name", "dept", "name"])
        assert result == ["name", "age", "name_2", "dept", "name_3"]


# ── _coerce_types ───────────────────────────────────────────────────

class TestCoerceTypes:
    def test_numeric_coercion(self):
        df = pd.DataFrame({"val": ["1", "2", "3", "4"]})
        result = _coerce_types(df)
        assert pd.api.types.is_numeric_dtype(result["val"])

    def test_partial_numeric_stays_string_when_minority(self):
        df = pd.DataFrame({"val": ["hello", "world", "test", "1"]})
        result = _coerce_types(df)
        # Only 25% numeric, should stay string (not converted to numeric)
        assert not pd.api.types.is_numeric_dtype(result["val"])

    def test_date_coercion(self):
        # Use object dtype explicitly so _coerce_types sees dtype == object
        df = pd.DataFrame({"d": pd.array(["2024-01-15", "2024-02-20", "2024-03-10"], dtype=object)})
        result = _coerce_types(df)
        assert pd.api.types.is_datetime64_any_dtype(result["d"])

    def test_non_date_string_stays_string(self):
        df = pd.DataFrame({"x": ["alice", "bob", "charlie"]})
        result = _coerce_types(df)
        # Should not become numeric or datetime
        assert not pd.api.types.is_numeric_dtype(result["x"])
        assert not pd.api.types.is_datetime64_any_dtype(result["x"])


# ── _parse_space_separated_text ─────────────────────────────────────

class TestParseSpaceSeparatedText:
    def test_basic_table(self):
        lines = [
            "Name          Department    Salary",
            "Alice         Engineering   80000",
            "Bob           Marketing     75000",
            "Charlie       Sales         70000",
        ]
        df = _parse_space_separated_text(lines)
        assert df is not None
        assert len(df) == 3
        assert len(df.columns) == 3

    def test_with_mixed_spacing(self):
        lines = [
            "EmpID    Name          Status     Date",
            "101      Prachi        Present    2024-01-15",
            "102      Nikhil        Absent     2024-01-15",
            "103      Ravi          Present    2024-01-15",
            "104      Sneha         Present    2024-01-15",
        ]
        df = _parse_space_separated_text(lines)
        assert df is not None
        assert len(df) == 4
        assert len(df.columns) == 4

    def test_too_few_lines_returns_none(self):
        lines = ["Just a title", "One line"]
        assert _parse_space_separated_text(lines) is None

    def test_no_table_pattern_returns_none(self):
        lines = [
            "This is a paragraph of text.",
            "It has no table structure at all.",
            "Just flowing prose with normal spacing.",
        ]
        result = _parse_space_separated_text(lines)
        # Should return None since there's no 2+ space column separation
        assert result is None or (result is not None and len(result.columns) < 2)

    def test_skips_page_numbers(self):
        lines = [
            "Page 1 of 3",
            "Name          Score     Grade",
            "Alice         95        A",
            "Bob           82        B",
            "Page 2 of 3",
            "Charlie       78        C",
        ]
        df = _parse_space_separated_text(lines)
        assert df is not None
        assert len(df) >= 2  # Should skip page headers

    def test_handles_empty_lines(self):
        lines = [
            "",
            "ID    Name      Value",
            "",
            "1     Alpha     100",
            "2     Beta      200",
            "",
            "3     Gamma     300",
        ]
        df = _parse_space_separated_text(lines)
        assert df is not None
        assert len(df) == 3


# ── process_csv ─────────────────────────────────────────────────────

class TestProcessCSV:
    def test_basic_csv(self):
        csv_bytes = b"Name,Age,City\nAlice,30,NYC\nBob,25,LA\n"
        df = process_csv(csv_bytes, "test.csv")
        assert len(df) == 2
        assert list(df.columns) == ["name", "age", "city"]

    def test_empty_csv_raises(self):
        csv_bytes = b""
        with pytest.raises(FileProcessingError):
            process_csv(csv_bytes, "empty.csv")

    def test_column_cleaning(self):
        csv_bytes = b"First Name [Required],Email Address [Required]\nAlice,alice@test.com\n"
        df = process_csv(csv_bytes, "test.csv")
        assert "first_name" in df.columns
        assert "email_address" in df.columns


# ── process_excel ───────────────────────────────────────────────────

class TestProcessExcel:
    def test_basic_xlsx(self):
        """Create a simple xlsx in memory and parse it."""
        df_orig = pd.DataFrame({"Name": ["Alice", "Bob"], "Score": [95, 82]})
        buf = io.BytesIO()
        df_orig.to_excel(buf, index=False, engine="openpyxl")
        buf.seek(0)

        df = process_excel(buf.read(), "test.xlsx")
        assert len(df) == 2
        assert "name" in df.columns
        assert "score" in df.columns

    def test_multi_sheet(self):
        """Multi-sheet Excel should combine all sheets."""
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            pd.DataFrame({"A": [1, 2]}).to_excel(writer, sheet_name="S1", index=False)
            pd.DataFrame({"A": [3, 4]}).to_excel(writer, sheet_name="S2", index=False)
        buf.seek(0)

        df = process_excel(buf.read(), "multi.xlsx")
        assert len(df) == 4


# ── process_file router ────────────────────────────────────────────

class TestProcessFileRouter:
    def test_csv_routing(self):
        csv_bytes = b"X,Y\n1,2\n3,4\n"
        df = process_file(csv_bytes, "test.csv", "csv")
        assert len(df) == 2

    def test_xlsx_routing(self):
        df_orig = pd.DataFrame({"Col": [10, 20]})
        buf = io.BytesIO()
        df_orig.to_excel(buf, index=False, engine="openpyxl")
        buf.seek(0)
        df = process_file(buf.read(), "test.xlsx", "xlsx")
        assert len(df) == 2

    def test_unsupported_type_raises(self):
        with pytest.raises(FileProcessingError):
            process_file(b"data", "test.json", "json")


# ── _group_compatible_tables ───────────────────────────────────────

class TestGroupCompatibleTables:
    def test_same_schema_merged(self):
        """Tables with identical headers should merge into one DataFrame."""
        t1 = [["Name", "Age"], ["Alice", "30"], ["Bob", "25"]]
        t2 = [["Name", "Age"], ["Charlie", "35"], ["Diana", "28"]]
        frames = _group_compatible_tables([t1, t2])
        assert len(frames) == 1
        assert len(frames[0]) == 4  # 4 data rows total

    def test_different_schema_kept_separate(self):
        """Tables with different column counts produce separate frames."""
        t1 = [["Name", "Age"], ["Alice", "30"]]
        t2 = [["X", "Y", "Z"], ["1", "2", "3"]]
        frames = _group_compatible_tables([t1, t2])
        assert len(frames) == 2

    def test_skips_wide_tables(self):
        """Tables with >30 columns are skipped as garbage."""
        # Create a table with 35 columns
        header = [f"Col{i}" for i in range(35)]
        row = [str(i) for i in range(35)]
        t1 = [header, row]
        # Normal table
        t2 = [["A", "B"], ["1", "2"]]
        frames = _group_compatible_tables([t1, t2])
        # Only the 2-col table should survive
        assert len(frames) == 1
        assert len(frames[0].columns) == 2

    def test_repeated_headers_across_pages_skipped(self):
        """Header rows repeated on subsequent pages should be skipped."""
        t1 = [["Name", "Score"], ["Alice", "90"], ["Bob", "85"]]
        t2 = [["Name", "Score"], ["Name", "Score"], ["Charlie", "78"]]  # header repeated
        frames = _group_compatible_tables([t1, t2])
        assert len(frames) == 1
        df = frames[0]
        # Should have 3 data rows, not 4 (repeated header excluded)
        assert len(df) == 3
        assert "Name" not in df["name"].values  # "Name" header row was skipped

    def test_empty_input(self):
        assert _group_compatible_tables([]) == []

    def test_keeps_largest_group_when_too_many_cols(self):
        """When multiple groups would concat to >30 cols, keep only largest."""
        # Group 1: 10 cols, 10 rows
        header1 = [f"A{i}" for i in range(10)]
        t1 = [header1] + [[str(j) for j in range(10)]] * 10
        # Group 2: 25 cols, 2 rows — together 35 > 30
        header2 = [f"X{i}" for i in range(25)]
        t2 = [header2, [str(i) for i in range(25)], [str(i) for i in range(25)]]
        frames = _group_compatible_tables([t1, t2])
        # Total cols 10+25=35 > 30, should keep only the largest group (10 rows)
        assert len(frames) == 1
        assert len(frames[0]) == 10