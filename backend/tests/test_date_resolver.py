"""
Tests for the Date Resolver (Phase 2).

Covers: relative singles, relative ranges, explicit date ranges,
date expression parser, edge cases, and convenience function.

All tests pin a reference date to 2024-09-25 (Wednesday) so results
are deterministic regardless of when the suite runs.
"""

import pytest
from datetime import date

from app.services.date_resolver import DateResolver, resolve_dates


# ── Fixture: a resolver pinned to 2024-09-25 (Wednesday) ─────────

REF_DATE = date(2024, 9, 25)


@pytest.fixture
def resolver():
    return DateResolver(reference_date=REF_DATE)


# ── Current date context ─────────────────────────────────────────

class TestCurrentDateContext:
    def test_current_date_and_day(self, resolver):
        result = resolver.resolve("show data")
        assert result["current_date"] == "2024-09-25"
        assert result["current_day"] == "Wednesday"

    def test_no_dates_still_returns_context(self, resolver):
        result = resolver.resolve("show all employees")
        assert result["current_date"] == "2024-09-25"
        assert result["resolved"] == []


# ── Relative singles ──────────────────────────────────────────────

class TestRelativeSingles:
    def test_today(self, resolver):
        result = resolver.resolve("how many present today")
        resolved = result["resolved"]
        assert len(resolved) == 1
        assert resolved[0]["original"] == "today"
        assert resolved[0]["type"] == "single_date"
        assert resolved[0]["date"] == "2024-09-25"
        assert resolved[0]["sql_hint"] == "= '2024-09-25'"

    def test_yesterday(self, resolver):
        result = resolver.resolve("attendance yesterday")
        resolved = result["resolved"]
        assert len(resolved) == 1
        assert resolved[0]["original"] == "yesterday"
        assert resolved[0]["date"] == "2024-09-24"

    def test_today_and_yesterday(self, resolver):
        result = resolver.resolve("compare today and yesterday")
        resolved = result["resolved"]
        originals = {r["original"] for r in resolved}
        assert "today" in originals
        assert "yesterday" in originals


# ── Relative ranges ───────────────────────────────────────────────

class TestRelativeRanges:
    def test_this_week(self, resolver):
        """2024-09-25 is Wednesday. This week = Mon 23 → Sun 29."""
        result = resolver.resolve("show this week data")
        resolved = result["resolved"]
        assert len(resolved) == 1
        r = resolved[0]
        assert r["original"] == "this week"
        assert r["start_date"] == "2024-09-23"
        assert r["end_date"] == "2024-09-29"

    def test_last_week(self, resolver):
        """Last week = Mon 16 → Sun 22."""
        result = resolver.resolve("attendance last week")
        resolved = result["resolved"]
        assert len(resolved) == 1
        r = resolved[0]
        assert r["start_date"] == "2024-09-16"
        assert r["end_date"] == "2024-09-22"

    def test_this_month(self, resolver):
        result = resolver.resolve("salary this month")
        resolved = result["resolved"]
        assert len(resolved) == 1
        r = resolved[0]
        assert r["start_date"] == "2024-09-01"
        assert r["end_date"] == "2024-09-30"

    def test_last_month(self, resolver):
        result = resolver.resolve("report for last month")
        resolved = result["resolved"]
        assert len(resolved) == 1
        r = resolved[0]
        assert r["start_date"] == "2024-08-01"
        assert r["end_date"] == "2024-08-31"

    def test_last_7_days(self, resolver):
        result = resolver.resolve("show last 7 days")
        resolved = result["resolved"]
        assert len(resolved) == 1
        r = resolved[0]
        assert r["start_date"] == "2024-09-18"
        assert r["end_date"] == "2024-09-25"

    def test_last_30_days(self, resolver):
        result = resolver.resolve("data for last 30 days")
        resolved = result["resolved"]
        assert len(resolved) == 1
        r = resolved[0]
        assert r["start_date"] == "2024-08-26"
        assert r["end_date"] == "2024-09-25"

    def test_last_3_months(self, resolver):
        result = resolver.resolve("show last 3 months")
        resolved = result["resolved"]
        assert len(resolved) == 1
        r = resolved[0]
        assert r["start_date"] == "2024-06-25"
        assert r["end_date"] == "2024-09-25"

    def test_last_2_weeks(self, resolver):
        result = resolver.resolve("past 2 weeks report")
        resolved = result["resolved"]
        assert len(resolved) == 1
        r = resolved[0]
        assert r["start_date"] == "2024-09-11"
        assert r["end_date"] == "2024-09-25"

    def test_number_word_five_days(self, resolver):
        result = resolver.resolve("show last five days")
        resolved = result["resolved"]
        assert len(resolved) == 1
        r = resolved[0]
        assert r["start_date"] == "2024-09-20"
        assert r["end_date"] == "2024-09-25"

    def test_previous_keyword(self, resolver):
        result = resolver.resolve("previous 10 days data")
        resolved = result["resolved"]
        assert len(resolved) == 1


# ── Explicit date ranges ──────────────────────────────────────────

class TestExplicitRanges:
    def test_from_to_with_day_month(self, resolver):
        result = resolver.resolve("attendance from 10 september to 20 september")
        resolved = result["resolved"]
        assert len(resolved) == 1
        r = resolved[0]
        assert r["type"] == "explicit_range"
        assert r["start_date"] == "2024-09-10"
        assert r["end_date"] == "2024-09-20"

    def test_between_and(self, resolver):
        result = resolver.resolve("show data between january and march")
        resolved = result["resolved"]
        assert len(resolved) == 1
        r = resolved[0]
        # january 1 → march 31
        assert r["start_date"] == "2024-01-01"
        assert r["end_date"] == "2024-03-31"

    def test_full_date_with_year(self, resolver):
        result = resolver.resolve("from 1 january 2024 to 31 march 2024")
        resolved = result["resolved"]
        assert len(resolved) == 1
        r = resolved[0]
        assert r["start_date"] == "2024-01-01"
        assert r["end_date"] == "2024-03-31"

    def test_bare_range_no_keyword(self, resolver):
        result = resolver.resolve("10 september to 20 september")
        resolved = result["resolved"]
        assert len(resolved) == 1
        r = resolved[0]
        assert r["start_date"] == "2024-09-10"
        assert r["end_date"] == "2024-09-20"

    def test_month_year_range(self, resolver):
        result = resolver.resolve("from september 2023 to january 2024")
        resolved = result["resolved"]
        assert len(resolved) == 1
        r = resolved[0]
        assert r["start_date"] == "2023-09-01"
        assert r["end_date"] == "2024-01-31"

    def test_us_date_format(self, resolver):
        """september 10, 2024"""
        result = resolver.resolve("from september 10, 2024 to september 20, 2024")
        resolved = result["resolved"]
        assert len(resolved) == 1
        r = resolved[0]
        assert r["start_date"] == "2024-09-10"
        assert r["end_date"] == "2024-09-20"

    def test_till_keyword(self, resolver):
        result = resolver.resolve("from march till june")
        resolved = result["resolved"]
        assert len(resolved) == 1
        r = resolved[0]
        assert r["start_date"] == "2024-03-01"
        assert r["end_date"] == "2024-06-30"


# ── SQL hint format ───────────────────────────────────────────────

class TestSQLHints:
    def test_single_date_hint(self, resolver):
        result = resolver.resolve("today")
        assert result["resolved"][0]["sql_hint"] == "= '2024-09-25'"

    def test_range_hint_between(self, resolver):
        result = resolver.resolve("this week")
        hint = result["resolved"][0]["sql_hint"]
        assert hint.startswith("BETWEEN")
        assert "2024-09-23" in hint
        assert "2024-09-29" in hint


# ── Edge cases ────────────────────────────────────────────────────

class TestEdgeCases:
    def test_no_date_expressions(self, resolver):
        result = resolver.resolve("show salary by department")
        assert result["resolved"] == []

    def test_dedup_same_expression(self, resolver):
        """Multiple occurrences of 'today' should produce one entry."""
        result = resolver.resolve("today vs yesterday today")
        originals = [r["original"] for r in result["resolved"]]
        assert originals.count("today") == 1

    def test_month_abbreviation_already_expanded(self, resolver):
        """The preprocessor expands 'sept' → 'september' before we run."""
        result = resolver.resolve("from 10 september to 20 september")
        assert len(result["resolved"]) == 1

    def test_last_month_crossing_year(self):
        """January → last month = December of previous year."""
        jan_resolver = DateResolver(reference_date=date(2024, 1, 15))
        result = jan_resolver.resolve("last month data")
        resolved = result["resolved"]
        assert len(resolved) == 1
        r = resolved[0]
        assert r["start_date"] == "2023-12-01"
        assert r["end_date"] == "2023-12-31"

    def test_last_months_crossing_year(self):
        """Last 3 months from February → November of previous year."""
        feb_resolver = DateResolver(reference_date=date(2024, 2, 10))
        result = feb_resolver.resolve("last 3 months")
        resolved = result["resolved"]
        assert len(resolved) == 1
        r = resolved[0]
        assert r["start_date"] == "2023-11-10"

    def test_empty_query(self, resolver):
        result = resolver.resolve("")
        assert result["resolved"] == []


# ── Convenience function ──────────────────────────────────────────

class TestConvenienceFunction:
    def test_resolve_dates_basic(self):
        result = resolve_dates("show today data", reference_date=REF_DATE)
        assert result["current_date"] == "2024-09-25"
        assert len(result["resolved"]) == 1
        assert result["resolved"][0]["original"] == "today"

    def test_resolve_dates_no_ref(self):
        """Without a reference date, uses actual today."""
        result = resolve_dates("show data")
        assert result["current_date"] == date.today().isoformat()
