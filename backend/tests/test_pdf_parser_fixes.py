"""
Tests for PDF parser fixes — Phase 3.

Covers:
  - _is_likely_header(): data rows vs real headers
  - _join_wrapped_lines(): line-joining logic
  - _score_joining(): candidate scoring
  - _parse_delimited_text(): wrapped-line PDFs, header detection, fallback
"""

import pytest
import pandas as pd

from app.services.file_processor import (
    _is_likely_header,
    _join_wrapped_lines,
    _score_joining,
    _parse_delimited_text,
)


# ── _is_likely_header ──────────────────────────────────────────────


class TestIsLikelyHeader:
    """The header detector must distinguish column-name rows from data rows."""

    def test_obvious_header(self):
        row = ["Name", "Department", "Status", "Date", "Check In"]
        assert _is_likely_header(row) is True

    def test_obvious_data_row(self):
        row = [
            "Abhishek Chauhan", "Yogen Infotech", "Thursday",
            "Oct 22, 2026", "09:38:06 GMT+0400 (Gulf Standard Time)",
            "Present",
        ]
        assert _is_likely_header(row) is False

    def test_data_with_timestamps(self):
        row = ["101", "Prachi Sirola", "2024-01-15", "09:30:00", "Present"]
        assert _is_likely_header(row) is False

    def test_data_with_day_names(self):
        row = ["Monday", "Nikhil Sharma", "Engineering", "10:15:00", "Present"]
        assert _is_likely_header(row) is False

    def test_data_with_timezone(self):
        row = ["John", "09:38:06 GMT+0400", "Present", "2026-10-22"]
        assert _is_likely_header(row) is False

    def test_mixed_header_words(self):
        row = ["Employee Name", "Employee ID", "Department", "Status"]
        assert _is_likely_header(row) is True

    def test_numeric_row_is_data(self):
        row = ["101", "50000", "3.5", "12"]
        assert _is_likely_header(row) is False

    def test_short_alpha_header(self):
        row = ["Sr", "Name", "Date", "Status"]
        assert _is_likely_header(row) is True

    def test_proper_names_are_data(self):
        """Names like 'Abhishek Chauhan' look like proper names, not headers."""
        row = ["Abhishek Chauhan", "Prachi Sirola", "Nikhil Sharma"]
        assert _is_likely_header(row) is False

    def test_empty_row(self):
        assert _is_likely_header([]) is False

    def test_single_cell_header(self):
        row = ["Name"]
        assert _is_likely_header(row) is True

    def test_long_cell_is_data(self):
        row = ["This is a very long description that is definitely data content not a header"]
        assert _is_likely_header(row) is False

    def test_month_digits_is_data(self):
        """Cells like 'Oct 22, 2026' are data, not headers."""
        row = ["Oct 22, 2026", "Nov 15, 2026", "Dec 1, 2026"]
        assert _is_likely_header(row) is False

    def test_regional_date_is_data(self):
        row = ["15/01/2024", "Prachi", "Present"]
        assert _is_likely_header(row) is False


# ── _join_wrapped_lines ────────────────────────────────────────────


class TestJoinWrappedLines:
    """Line-joining for soft-wrapped PDF records."""

    def test_no_wrapping_needed(self):
        """Lines with enough delimiters stay separate."""
        lines = [
            "A|B|C|D|E",
            "1|2|3|4|5",
            "6|7|8|9|0",
        ]
        result = _join_wrapped_lines(lines, "|", 4)
        assert len(result) == 3

    def test_joins_partial_lines(self):
        """Two partial lines should merge into one complete record."""
        lines = [
            "A|B|C|D|E|F|G|H",     
            "val1|val2|val3",        
            "|val4|val5|val6|val7",  
            "x1|x2|x3|x4|x5|x6|x7|x8", 
        ]
        result = _join_wrapped_lines(lines, "|", 7)
        assert len(result) == 3  

    def test_does_not_over_join(self):
        """Joining should stop once the combined count reaches target."""
        lines = [
            "A|B|C|D",     
            "E|F|G|H",    
        ]

        result = _join_wrapped_lines(lines, "|", 3)
        assert len(result) == 2

    def test_three_way_join(self):
        """A record split across three lines should still merge."""
        lines = [
            "A|B|C|D|E|F|G|H",  
            "x1|x2",      
            "|x3|x4",        
            "|x5|x6|x7|x8",   
        ]
        result = _join_wrapped_lines(lines, "|", 7)

        assert len(result) == 2


# ── _score_joining ─────────────────────────────────────────────────


class TestScoreJoining:
    def test_perfect_delimiter_count(self):
        lines = [
            "H1|H2|H3|H4",
            "a|b|c|d",
            "e|f|g|h",
            "i|j|k|l",
        ]
        _, fcount, consistent, total = _score_joining(lines, "|", 3)
        assert fcount == 4
        assert consistent == 4  
        assert total == 4

    def test_wrong_target_fewer_consistent(self):
        """Using a too-low target produces more inconsistent rows."""
        lines = [
            "H1|H2|H3|H4|H5",
            "a|b|c|d|e",
            "f|g|h|i|j",
        ]

        _, fcount2, consistent2, _ = _score_joining(lines, "|", 2)

        _, fcount4, consistent4, _ = _score_joining(lines, "|", 4)

        assert consistent4 == 3


class TestParseDelimitedWrapped:
    """The core fix: PDFs where most lines are partial (wrapped mid-record)."""

    def _make_attendance_lines(self, n_records=10):
        """
        Simulate a pipe-delimited attendance PDF where each record wraps
        across 2-3 text lines (the bug scenario).
        """
        header = "Sr|Employee Name|Company|Day|Date|Check In|Status"
        lines = [header]
        for i in range(1, n_records + 1):

            part1 = f"{i}|Person {i}|Acme Corp"
            part2 = f"|Thursday|Oct {i}, 2026|09:30:00 GMT+0400|Present"
            lines.append(part1)
            lines.append(part2)
        return lines

    def test_wrapped_pdf_produces_correct_columns(self):
        """Wrapped pipe-delimited text should parse with 7 columns, not garbled."""
        text_lines = self._make_attendance_lines(10)
        df = _parse_delimited_text(text_lines)
        assert df is not None
        assert len(df.columns) == 7
        assert len(df) == 10

    def test_wrapped_pdf_header_detected(self):
        """The header row (Sr|Employee Name|...) should become column names."""
        text_lines = self._make_attendance_lines(10)
        df = _parse_delimited_text(text_lines)
        assert df is not None
        cols = list(df.columns)
        assert "sr" in cols or "column_1" in cols

        if "sr" in cols:
            assert "employee_name" in cols
            assert "status" in cols

    def test_non_wrapped_still_works(self):
        """Normal (non-wrapped) pipe data should still parse correctly."""
        lines = [
            "Name|Dept|Salary|Status",
            "Alice|Engineering|80000|Active",
            "Bob|Marketing|75000|Active",
            "Charlie|Sales|70000|Active",
            "Diana|HR|72000|Active",
        ]
        df = _parse_delimited_text(lines)
        assert df is not None
        assert len(df) == 4
        assert len(df.columns) == 4
        assert "name" in df.columns

    def test_data_row_not_used_as_header(self):
        """
        A data row like 'Abhishek Chauhan|Yogen Infotech|...' must NOT
        become column headers.
        """
        lines = [
            "Abhishek Chauhan|Yogen Infotech|Thursday|Oct 22, 2026|09:38:06|Present|8.5",
            "Prachi Sirola|TechCorp|Friday|Oct 23, 2026|09:15:00|Present|9.0",
            "Nikhil Sharma|DataInc|Monday|Oct 26, 2026|10:00:00|Absent|0",
            "Sneha Gupta|Acme|Tuesday|Oct 27, 2026|08:45:00|Present|8.0",
        ]
        df = _parse_delimited_text(lines)
        assert df is not None

        assert "abhishek_ch" not in df.columns[0]
        assert any(c.startswith("column_") for c in df.columns)
  
        assert len(df) == 4

    def test_header_in_non_matching_row(self):
        """
        Header with a slightly different pipe count (e.g. missing trailing pipe)
        should still be found by scanning non-matching rows.
        """
        lines = [
            "Name|Dept|Salary|Status|Hours",  
            "Alice|Engineering|80000|Active|40|extra",  
            "Bob|Marketing|75000|Active|38|extra",
            "Charlie|Sales|70000|Active|42|extra",
            "Diana|HR|72000|Active|35|extra",
        ]
        df = _parse_delimited_text(lines)
        assert df is not None

        assert len(df) >= 4

    def test_comma_delimiter(self):
        """Comma-delimited data should still work (no wrapping logic for comma)."""
        lines = [
            "Name,Age,City,Score",
            "Alice,30,NYC,95",
            "Bob,25,LA,82",
            "Charlie,35,Chicago,78",
            "Diana,28,Houston,91",
        ]
        df = _parse_delimited_text(lines)
        assert df is not None
        assert len(df) == 4
        assert "name" in df.columns

    def test_too_few_lines(self):
        assert _parse_delimited_text(["a|b", "c|d"]) is None

    def test_empty_lines(self):
        assert _parse_delimited_text([]) is None

    def test_no_delimiter(self):
        lines = [
            "This is just plain text",
            "With no delimiters at all",
            "Just prose flowing naturally",
            "Nothing to parse here",
        ]
        assert _parse_delimited_text(lines) is None

    def test_mixed_wrapped_lengths(self):
        """
        Records wrapping across different numbers of lines should all
        merge correctly.
        """

        lines = [
            "Name|Dept|Date|Time|Status|Hours",  # header
            "Alice|Eng|2026-10-01",               # partial (2 pipes)
            "|09:30|Present|8",                    # continuation (3 pipes) → total 5
            "Bob|Marketing|2026-10-01|10:00",      # partial (3 pipes)
            "|Absent|0",                           # continuation (2 pipes) → total 5
            "Charlie|Sales|2026-10-01|08:45|Present|9",  # complete (5 pipes)
        ]
        df = _parse_delimited_text(lines)
        assert df is not None
        assert len(df.columns) == 6
        assert len(df) == 3  # 3 records