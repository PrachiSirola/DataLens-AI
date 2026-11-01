"""
Tests for the Query Preprocessor (Phases 1 & 2).

Covers: spell correction, abbreviation expansion, number expansion,
fuzzy column matching, value detection, date resolution integration,
and full pipeline with the messy real-world queries the user provided.
"""

import pytest
from unittest.mock import patch
from datetime import date

from app.services.query_preprocessor import QueryPreprocessor, preprocess_query


# ── Fixtures ──────────────────────────────────────────────────────────

SAMPLE_COLUMNS = [
    "employee_id", "first_name", "last_name", "department",
    "designation", "date", "status", "check_in", "check_out",
    "working_hours", "salary", "overtime",
]

SAMPLE_VALUES = {
    "first_name": ["Prachi", "Nikhil", "Rahul", "Sneha", "Amit"],
    "last_name": ["Sirola", "Sharma", "Patel", "Gupta"],
    "department": ["Engineering / IT", "Human Resources", "Finance", "Marketing"],
    "status": ["Present", "Absent", "Half Day", "On Leave"],
    "designation": ["Software Engineer", "Senior Developer", "Team Lead", "Manager"],
}


@pytest.fixture
def preprocessor():
    return QueryPreprocessor(column_names=SAMPLE_COLUMNS, column_values=SAMPLE_VALUES)


@pytest.fixture
def bare_preprocessor():
    """Preprocessor with no schema context."""
    return QueryPreprocessor()


# ── Spell Correction ─────────────────────────────────────────────────

class TestSpellCorrection:
    def test_attendance_typo(self, preprocessor):
        result = preprocessor.process("show attendence report")
        assert "attendance" in result["cleaned"]
        assert any("attendence" in c for c in result["corrections"])

    def test_multiple_typos(self, preprocessor):
        result = preprocessor.process("how many persent employes today")
        assert "present" in result["cleaned"]
        assert "employees" in result["cleaned"]
        assert "persent" not in result["cleaned"]
        assert "employes" not in result["cleaned"]

    def test_salary_typo(self, preprocessor):
        result = preprocessor.process("show salry of all emplyee")
        assert "salary" in result["cleaned"]
        assert "employee" in result["cleaned"]

    def test_department_typo(self, preprocessor):
        result = preprocessor.process("list deprtment wise data")
        assert "department" in result["cleaned"]

    def test_no_false_corrections(self, preprocessor):
        result = preprocessor.process("show total salary by department")
        assert result["corrections"] == []  # no typo corrections needed


# ── Abbreviation Expansion ───────────────────────────────────────────

class TestAbbreviationExpansion:
    def test_month_abbreviations(self, preprocessor):
        result = preprocessor.process("data from jan to sept")
        assert "january" in result["cleaned"]
        assert "september" in result["cleaned"]

    def test_employee_abbreviation(self, preprocessor):
        result = preprocessor.process("show all emp details")
        assert "employee" in result["cleaned"]

    def test_department_abbreviation(self, preprocessor):
        result = preprocessor.process("dept wise salary")
        assert "department" in result["cleaned"]

    def test_salary_abbreviation(self, preprocessor):
        result = preprocessor.process("avg sal by dept")
        assert "average" in result["cleaned"]
        assert "salary" in result["cleaned"]
        assert "department" in result["cleaned"]

    def test_attendance_abbreviation(self, preprocessor):
        result = preprocessor.process("att report for last wk")
        assert "attendance" in result["cleaned"]
        assert "week" in result["cleaned"]

    def test_combined_spell_and_abbr(self, preprocessor):
        """Spell correction runs before abbreviation expansion."""
        result = preprocessor.process("attendence of emp in sept")
        assert "attendance" in result["cleaned"]
        assert "employee" in result["cleaned"]
        assert "september" in result["cleaned"]


# ── Number Expansion ─────────────────────────────────────────────────

class TestNumberExpansion:
    def test_k_expansion(self, preprocessor):
        result = preprocessor.process("salary above 10k")
        assert "10000" in result["cleaned"]

    def test_lakh_expansion(self, preprocessor):
        result = preprocessor.process("revenue over 2lakh")
        assert "200000" in result["cleaned"]

    def test_crore_expansion(self, preprocessor):
        result = preprocessor.process("profit more than 5cr")
        assert "50000000" in result["cleaned"]

    def test_k_with_space(self, preprocessor):
        result = preprocessor.process("employees earning above 50 k")
        assert "50000" in result["cleaned"]


# ── Fuzzy Column Matching ────────────────────────────────────────────

class TestFuzzyColumnMatching:
    def test_close_column_name(self, preprocessor):
        result = preprocessor.process("show working hour data")
        # "working_hours" should be matched
        assert any("column:" in c for c in result["corrections"])

    def test_no_match_common_words(self, preprocessor):
        """Common English words should NOT fuzzy-match to columns."""
        result = preprocessor.process("show me the total count")
        # "total" and "show" are common words, should not be matched to columns
        col_corrections = [c for c in result["corrections"] if "column:" in c]
        assert len(col_corrections) == 0

    def test_exact_column_not_fuzzied(self, preprocessor):
        """Already-correct column names shouldn't generate corrections."""
        result = preprocessor.process("salary of each department")
        col_corrections = [c for c in result["corrections"] if "column:" in c]
        assert len(col_corrections) == 0


# ── Value Detection ──────────────────────────────────────────────────

class TestValueDetection:
    def test_detects_name(self, preprocessor):
        result = preprocessor.process("prachi attendance")
        hints = result["value_hints"]
        assert len(hints) >= 1
        prachi_hint = next((h for h in hints if h["value"] == "Prachi"), None)
        assert prachi_hint is not None
        assert prachi_hint["column"] == "first_name"

    def test_detects_department(self, preprocessor):
        result = preprocessor.process("show finance department data")
        hints = result["value_hints"]
        assert any(h["value"] == "Finance" for h in hints)

    def test_detects_status(self, preprocessor):
        result = preprocessor.process("how many absent today")
        hints = result["value_hints"]
        assert any(h["value"] == "Absent" for h in hints)

    def test_detects_two_word_value(self, preprocessor):
        result = preprocessor.process("employees on half day")
        hints = result["value_hints"]
        assert any(h["value"] == "Half Day" for h in hints)

    def test_detects_multiple_values(self, preprocessor):
        result = preprocessor.process("prachi attendance in finance")
        hints = result["value_hints"]
        values_found = {h["value"] for h in hints}
        assert "Prachi" in values_found
        assert "Finance" in values_found

    def test_fuzzy_name_detection(self, preprocessor):
        """Misspelled names should be caught by fuzzy matching."""
        result = preprocessor.process("show prachii attendance")
        hints = result["value_hints"]
        # "prachii" should fuzzy-match to "Prachi"
        prachi_hint = next((h for h in hints if h["value"] == "Prachi"), None)
        assert prachi_hint is not None
        assert prachi_hint.get("fuzzy") is True

    def test_no_false_value_detection(self, preprocessor):
        """Common words should not be detected as data values."""
        result = preprocessor.process("how many employees are there")
        # None of these common words should match data values
        hints = result["value_hints"]
        assert len(hints) == 0


# ── Full Pipeline: Real-World Messy Queries ──────────────────────────

class TestRealWorldQueries:
    """
    Test the exact messy queries the user described as failing.
    These are the critical acceptance tests for Phase 1.
    """

    def test_attendance_date_range(self, preprocessor):
        """'give attendence from 10 sept to 20 sept'"""
        result = preprocessor.process("give attendence from 10 sept to 20 sept")
        cleaned = result["cleaned"]
        assert "attendance" in cleaned
        assert "september" in cleaned
        assert "attendence" not in cleaned
        assert "sept" not in cleaned.split()  # "sept" should be expanded

    def test_absent_employees_last_month(self, preprocessor):
        """'show absent emp last month'"""
        result = preprocessor.process("show absent emp last month")
        cleaned = result["cleaned"]
        assert "employee" in cleaned
        # "absent" should trigger a value hint
        hints = result["value_hints"]
        assert any(h["value"] == "Absent" for h in hints)

    def test_present_count_today(self, preprocessor):
        """'how many persent today'"""
        result = preprocessor.process("how many persent today")
        cleaned = result["cleaned"]
        assert "present" in cleaned
        assert "persent" not in cleaned

    def test_person_attendance(self, preprocessor):
        """'prachi attendence'"""
        result = preprocessor.process("prachi attendence")
        cleaned = result["cleaned"]
        assert "attendance" in cleaned
        hints = result["value_hints"]
        assert any(h["value"] == "Prachi" for h in hints)

    def test_salary_query_with_abbreviations(self, preprocessor):
        """'avg sal of emp in sept'"""
        result = preprocessor.process("avg sal of emp in sept")
        cleaned = result["cleaned"]
        assert "average" in cleaned
        assert "salary" in cleaned
        assert "employee" in cleaned
        assert "september" in cleaned

    def test_department_query_with_typos(self, preprocessor):
        """'deprtment wise attendence report'"""
        result = preprocessor.process("deprtment wise attendence report")
        cleaned = result["cleaned"]
        assert "department" in cleaned
        assert "attendance" in cleaned

    def test_number_shorthand_in_query(self, preprocessor):
        """'emp with sal above 50k'"""
        result = preprocessor.process("emp with sal above 50k")
        cleaned = result["cleaned"]
        assert "employee" in cleaned
        assert "salary" in cleaned
        assert "50000" in cleaned

    def test_multiple_name_query(self, preprocessor):
        """'compare prachi and nikhil attendance'"""
        result = preprocessor.process("compare prachi and nikhil attendance")
        hints = result["value_hints"]
        values = {h["value"] for h in hints}
        assert "Prachi" in values
        assert "Nikhil" in values


# ── Convenience Function ─────────────────────────────────────────────

class TestConvenienceFunction:
    def test_preprocess_query_basic(self):
        result = preprocess_query("attendence report")
        assert result["cleaned"] == "attendance report"
        assert result["original"] == "attendence report"

    def test_preprocess_query_with_schema(self):
        result = preprocess_query(
            "prachi sal",
            column_names=SAMPLE_COLUMNS,
            column_values=SAMPLE_VALUES,
        )
        assert "salary" in result["cleaned"]
        assert any(h["value"] == "Prachi" for h in result["value_hints"])


# ── Edge Cases ───────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_query(self, bare_preprocessor):
        result = bare_preprocessor.process("")
        assert result["cleaned"] == ""

    def test_single_word(self, bare_preprocessor):
        result = bare_preprocessor.process("salary")
        assert result["cleaned"] == "salary"

    def test_already_clean(self, preprocessor):
        result = preprocessor.process("show total salary by department")
        # Should return the same text (lowercased)
        assert result["cleaned"] == "show total salary by department"
        assert len(result["corrections"]) == 0

    def test_unicode_quotes_normalized(self, preprocessor):
        result = preprocessor.process("show “finance” department")
        assert '"finance"' in result["cleaned"]

    def test_excessive_whitespace(self, preprocessor):
        result = preprocessor.process("  show   attendence    report  ")
        assert result["cleaned"] == "show attendance report"
        assert "  " not in result["cleaned"]

    def test_trailing_punctuation_stripped(self, preprocessor):
        result = preprocessor.process("how many present today?")
        assert result["cleaned"] == "how many present today"

    def test_no_schema_still_works(self, bare_preprocessor):
        """Preprocessor should still fix typos even without schema context."""
        result = bare_preprocessor.process("attendence of employes in sept")
        assert "attendance" in result["cleaned"]
        assert "employees" in result["cleaned"]
        assert "september" in result["cleaned"]


# ── Date Resolution Integration (Phase 2) ───────────────────────────

class TestDateResolutionIntegration:
    """Test that date resolution runs as step 7 of the preprocessing pipeline."""

    REF = date(2024, 9, 25)

    def test_date_context_in_result(self, preprocessor):
        """The result dict should always include date_context."""
        result = preprocessor.process("show salary")
        assert "date_context" in result
        assert "current_date" in result["date_context"]
        assert "current_day" in result["date_context"]

    @patch("app.services.query_preprocessor.resolve_dates")
    def test_today_resolved(self, mock_resolve, preprocessor):
        """'today' should trigger date resolution."""
        mock_resolve.return_value = {
            "current_date": "2024-09-25",
            "current_day": "Wednesday",
            "resolved": [{"original": "today", "type": "single_date",
                          "date": "2024-09-25", "sql_hint": "= '2024-09-25'"}],
        }
        result = preprocessor.process("how many present today")
        mock_resolve.assert_called_once()
        dc = result["date_context"]
        assert len(dc["resolved"]) == 1
        assert dc["resolved"][0]["original"] == "today"

    @patch("app.services.query_preprocessor.resolve_dates")
    def test_abbreviation_expanded_before_date_resolve(self, mock_resolve, preprocessor):
        """
        'sept' → 'september' (step 4) should happen BEFORE step 7,
        so the date resolver sees 'september' not 'sept'.
        """
        mock_resolve.return_value = {
            "current_date": "2024-09-25",
            "current_day": "Wednesday",
            "resolved": [],
        }
        preprocessor.process("data from 10 sept to 20 sept")
        # The call to resolve_dates should receive the cleaned text with "september"
        call_args = mock_resolve.call_args[0][0]
        assert "september" in call_args
        assert "sept" not in call_args.split()

    def test_spell_and_date_combined(self, preprocessor):
        """Spell correction + date resolution work together."""
        result = preprocessor.process("attendence today")
        assert "attendance" in result["cleaned"]
        assert "date_context" in result
        dc = result["date_context"]
        assert len(dc["resolved"]) >= 1
        assert dc["resolved"][0]["original"] == "today"

    def test_no_date_expression(self, preprocessor):
        """Queries without date words should produce empty resolved list."""
        result = preprocessor.process("show salary by department")
        dc = result["date_context"]
        assert dc["resolved"] == []
