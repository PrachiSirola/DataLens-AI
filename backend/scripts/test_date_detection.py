#!/usr/bin/env python3
"""
DataLensAI — Date Format Detection Tests (Phase 2)

Tests the enhanced _detect_dayfirst() and detect_date_format() functions
against ISO, DD/MM/YYYY, MM/DD/YYYY, mixed, named-month, and invalid formats.

Usage:
  cd backend
  python -m scripts.test_date_detection
"""

import sys
from pathlib import Path

import pandas as pd

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.services.file_processor import _detect_dayfirst, detect_date_format, DateFormatResult

# ── Test helpers ──────────────────────────────────────────────────────

passed = 0
failed = 0
total = 0


def check(test_name: str, series_data: list, expected_dayfirst: bool | None,
           expected_format: str | None = None, expect_mixed: bool = False,
           expect_invalid: bool = False):
    """Run a single test case."""
    global passed, failed, total
    total += 1

    series = pd.Series(series_data)
    result = detect_date_format(series)

    ok = True
    issues = []

    # Check dayfirst
    if result.dayfirst != expected_dayfirst:
        ok = False
        issues.append(f"dayfirst: expected {expected_dayfirst}, got {result.dayfirst}")

    # Check format type (optional)
    if expected_format and result.format_type != expected_format:
        ok = False
        issues.append(f"format_type: expected '{expected_format}', got '{result.format_type}'")

    # Check mixed flag
    if expect_mixed and not result.mixed:
        ok = False
        issues.append(f"expected mixed=True but got mixed=False")

    # Check invalid detection
    if expect_invalid and result.invalid_count == 0:
        ok = False
        issues.append(f"expected invalid_count > 0 but got 0")

    if ok:
        passed += 1
        print(f"  ✓ {test_name}")
    else:
        failed += 1
        print(f"  ✗ {test_name}")
        for issue in issues:
            print(f"      {issue}")
        print(f"      detail: {result.detail}")

    return ok


# ── Test Cases ────────────────────────────────────────────────────────

def test_iso_format():
    """ISO dates (YYYY-MM-DD) — the most common in our CSVs."""
    print("\n── ISO Format (YYYY-MM-DD) ──")

    check("Pure ISO dates", [
        "2024-01-15", "2024-02-20", "2024-03-10",
        "2024-04-05", "2024-05-30",
    ], expected_dayfirst=False, expected_format="iso")

    check("ISO with slashes", [
        "2024/01/15", "2024/02/20", "2024/03/10",
    ], expected_dayfirst=False, expected_format="iso")

    check("ISO with high day values", [
        "2024-01-31", "2024-02-28", "2024-03-25",
        "2024-04-30", "2024-12-31",
    ], expected_dayfirst=False, expected_format="iso")


def test_dayfirst_format():
    """Day-first dates (DD/MM/YYYY) — common in India, UK, EU."""
    print("\n── Day-First (DD/MM/YYYY) ──")

    check("Clear day-first (day > 12)", [
        "15/01/2024", "20/02/2024", "25/03/2024",
        "30/04/2024", "31/05/2024",
    ], expected_dayfirst=True, expected_format="dayfirst")

    check("Day-first with dots", [
        "15.01.2024", "20.02.2024", "25.03.2024",
    ], expected_dayfirst=True, expected_format="dayfirst")

    check("Day-first with dashes", [
        "15-01-2024", "20-02-2024", "25-03-2024",
    ], expected_dayfirst=True, expected_format="dayfirst")

    check("Day-first 2-digit year", [
        "15/01/24", "20/02/24", "25/03/24",
    ], expected_dayfirst=True, expected_format="dayfirst")


def test_monthfirst_format():
    """Month-first dates (MM/DD/YYYY) — US format."""
    print("\n── Month-First (MM/DD/YYYY) ──")

    check("Clear month-first (day > 12 in second position)", [
        "01/15/2024", "02/20/2024", "03/25/2024",
        "04/30/2024", "05/31/2024",
    ], expected_dayfirst=False, expected_format="monthfirst")

    check("US format with dashes", [
        "01-15-2024", "02-20-2024", "03-25-2024",
    ], expected_dayfirst=False, expected_format="monthfirst")


def test_ambiguous_format():
    """Ambiguous dates where both parts ≤ 12."""
    print("\n── Ambiguous Dates ──")

    check("All parts ≤ 12 (truly ambiguous)", [
        "01/02/2024", "03/04/2024", "05/06/2024",
        "07/08/2024", "09/10/2024",
    ], expected_dayfirst=None, expected_format="ambiguous")

    check("Same value in both positions", [
        "01/01/2024", "02/02/2024", "03/03/2024",
    ], expected_dayfirst=None, expected_format="ambiguous")


def test_named_month_format():
    """Named month formats (15-Jan-2024, Jan 15 2024, etc.)."""
    print("\n── Named Month Format ──")

    check("DD-Mon-YYYY format", [
        "15-Jan-2024", "20-Feb-2024", "25-Mar-2024",
    ], expected_dayfirst=True, expected_format="named_month")

    check("Full month names", [
        "15 January 2024", "20 February 2024", "25 March 2024",
    ], expected_dayfirst=True, expected_format="named_month")

    check("Mon DD, YYYY format", [
        "Jan 15, 2024", "Feb 20, 2024", "Mar 25, 2024",
    ], expected_dayfirst=True, expected_format="named_month")


def test_mixed_format():
    """Mixed formats — should be flagged."""
    print("\n── Mixed Formats (should flag) ──")

    check("ISO + day-first mix", [
        "2024-01-15", "2024-02-20",  # ISO
        "25/03/2024", "30/04/2024",  # day-first
    ], expected_dayfirst=True, expect_mixed=True)

    check("Day-first + month-first CONFLICT", [
        "25/03/2024", "30/04/2024",  # day-first (25, 30 > 12)
        "03/25/2024", "04/30/2024",  # month-first (25, 30 > 12 in part2)
    ], expected_dayfirst=True, expected_format="mixed", expect_mixed=True)


def test_invalid_dates():
    """Invalid/unparseable dates."""
    print("\n── Invalid Dates ──")

    check("Out-of-range values", [
        "32/01/2024", "2024-13-01", "00/05/2024",
    ], expected_dayfirst=None, expect_invalid=True)

    check("Mixed with garbage (no digits = skipped, not invalid)", [
        "2024-01-15", "not-a-date", "hello",
        "2024-02-20", "???",
    ], expected_dayfirst=False, expected_format="iso", expect_invalid=False)

    check("Mixed with digit-containing garbage (counts as invalid)", [
        "2024-01-15", "99/99/2024", "2024-02-20", "0/0/2024",
    ], expected_dayfirst=False, expected_format="iso", expect_invalid=True)

    check("Empty/null values handled", [
        None, "", "NaN", "NaT",
        "2024-01-15", "2024-02-20",
    ], expected_dayfirst=False, expected_format="iso")


def test_real_csv_patterns():
    """Patterns from our actual DataLensAI CSVs."""
    print("\n── Real CSV Patterns ──")

    check("Attendance CSV dates (ISO)", [
        "2026-08-03", "2026-08-04", "2026-08-05",
        "2026-09-15", "2026-10-28",
    ], expected_dayfirst=False, expected_format="iso")

    check("Transaction CSV dates (ISO)", [
        "2024-01-01", "2024-06-15", "2025-03-20",
        "2026-10-27", "2026-10-28",
    ], expected_dayfirst=False, expected_format="iso")


def test_backward_compat():
    """Verify _detect_dayfirst() still works (backward compatibility)."""
    print("\n── Backward Compatibility (_detect_dayfirst) ──")

    s_iso = pd.Series(["2024-01-15", "2024-02-20"])
    result = _detect_dayfirst(s_iso)
    global passed, failed, total
    total += 1
    if result is False:
        passed += 1
        print("  ✓ _detect_dayfirst ISO → False")
    else:
        failed += 1
        print(f"  ✗ _detect_dayfirst ISO → expected False, got {result}")

    s_day = pd.Series(["25/03/2024", "30/04/2024"])
    result = _detect_dayfirst(s_day)
    total += 1
    if result is True:
        passed += 1
        print("  ✓ _detect_dayfirst day-first → True")
    else:
        failed += 1
        print(f"  ✗ _detect_dayfirst day-first → expected True, got {result}")

    s_ambig = pd.Series(["01/02/2024", "03/04/2024"])
    result = _detect_dayfirst(s_ambig)
    total += 1
    if result is None:
        passed += 1
        print("  ✓ _detect_dayfirst ambiguous → None")
    else:
        failed += 1
        print(f"  ✗ _detect_dayfirst ambiguous → expected None, got {result}")


# ── Runner ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"{'═' * 55}")
    print(f"  DataLensAI Date Format Detection Tests")
    print(f"{'═' * 55}")

    test_iso_format()
    test_dayfirst_format()
    test_monthfirst_format()
    test_ambiguous_format()
    test_named_month_format()
    test_mixed_format()
    test_invalid_dates()
    test_real_csv_patterns()
    test_backward_compat()

    print(f"\n{'═' * 55}")
    print(f"  RESULTS: {passed}/{total} passed, {failed} failed")
    accuracy = passed / total * 100 if total > 0 else 0
    print(f"  Accuracy: {accuracy:.1f}%")
    print(f"{'═' * 55}")

    if accuracy >= 90:
        print(f"\n  🟢 Date detection is robust")
    elif accuracy >= 70:
        print(f"\n  🟡 Some edge cases need work")
    else:
        print(f"\n  🔴 Significant issues detected")

    sys.exit(0 if failed == 0 else 1)
