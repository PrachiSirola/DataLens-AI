"""
Date Resolver — Phase 2 of DataLensAI intelligence pipeline.

Resolves natural-language date/time expressions into concrete dates
and SQL-friendly hints:
  - Relative singles: today, yesterday
  - Relative ranges: this week, last month, last 7 days
  - Explicit ranges: "from 10 september to 20 september"
  - Current date context for LLM prompt injection

Runs AFTER spell correction and abbreviation expansion (step 7 in
the preprocessor pipeline), so month names are already full-length.
"""

import calendar
import logging
import re
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── Month and number lookups ──────────────────────────────────────

MONTH_MAP: dict[str, int] = {}
for _num in range(1, 13):
    MONTH_MAP[calendar.month_name[_num].lower()] = _num   # january → 1
    MONTH_MAP[calendar.month_abbr[_num].lower()] = _num   # jan → 1

NUMBER_WORDS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
    "thirty": 30,
}

# Pre-compiled month regex — longest names first so "september" beats "sep"
_MONTH_RE = "(?:" + "|".join(
    re.escape(m) for m in sorted(MONTH_MAP, key=len, reverse=True)
) + ")"

# A single date expression (all groups non-capturing for clean embedding)
_DATE_EXPR = (
    r"(?:"
    rf"\d{{1,2}}\s+{_MONTH_RE}\s+\d{{4}}"         # 10 september 2024
    rf"|{_MONTH_RE}\s+\d{{1,2}}\s*,?\s*\d{{4}}"   # september 10, 2024
    rf"|\d{{1,2}}\s+{_MONTH_RE}"                    # 10 september
    rf"|{_MONTH_RE}\s+\d{{4}}"                      # september 2024 (before MONTH+digit!)
    rf"|{_MONTH_RE}\s+\d{{1,2}}"                    # september 10
    rf"|{_MONTH_RE}"                                 # september
    r")"
)

# Number-word alternatives for "last N" patterns
_NUM_WORD_RE = "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))


# ── Core resolver ─────────────────────────────────────────────────

class DateResolver:
    """Resolves date expressions relative to a reference date."""

    def __init__(self, reference_date: Optional[date] = None):
        self.today = reference_date or date.today()

    def resolve(self, query: str) -> dict:
        """
        Scan *query* for date expressions and return structured context.

        Returns::

            {
                "current_date": "2024-09-25",
                "current_day": "Wednesday",
                "resolved": [ { original, type, sql_hint, ... }, ... ]
            }
        """
        resolved: list[dict] = []
        q = query.lower()

        # Order matters: explicit first (most specific), then relative
        resolved.extend(self._resolve_explicit_ranges(q))
        resolved.extend(self._resolve_relative_ranges(q))
        resolved.extend(self._resolve_relative_singles(q))

        # De-duplicate by original text
        seen: set[str] = set()
        unique: list[dict] = []
        for r in resolved:
            if r["original"] not in seen:
                seen.add(r["original"])
                unique.append(r)

        return {
            "current_date": self.today.isoformat(),
            "current_day": self.today.strftime("%A"),
            "resolved": unique,
        }

    # ── Single relative dates ──────────────────────────────────────

    def _resolve_relative_singles(self, q: str) -> list[dict]:
        """Resolve *today* and *yesterday*."""
        results: list[dict] = []

        if re.search(r"\btoday\b", q):
            d = self.today
            results.append({
                "original": "today",
                "type": "single_date",
                "date": d.isoformat(),
                "sql_hint": f"= '{d.isoformat()}'",
            })

        if re.search(r"\byesterday\b", q):
            d = self.today - timedelta(days=1)
            results.append({
                "original": "yesterday",
                "type": "single_date",
                "date": d.isoformat(),
                "sql_hint": f"= '{d.isoformat()}'",
            })

        return results

    # ── Relative ranges ────────────────────────────────────────────

    def _resolve_relative_ranges(self, q: str) -> list[dict]:
        """Resolve this/last week, this/last month, last N days/weeks/months."""
        results: list[dict] = []

        # --- "last/past/previous N days/weeks/months" ----------------
        pat = rf"\b(last|past|previous)\s+(\d+|{_NUM_WORD_RE})\s+(day|week|month)s?\b"
        m = re.search(pat, q)
        if m:
            n_raw = m.group(2)
            n = NUMBER_WORDS.get(n_raw)
            if n is None:
                try:
                    n = int(n_raw)
                except ValueError:
                    n = 1
            unit = m.group(3)
            start, end = self._last_n(n, unit)
            results.append(self._range_dict(m.group(0), "relative_range", start, end))

        # --- "this week" (Monday → Sunday) ----------------------------
        if re.search(r"\bthis\s+week\b", q):
            monday = self.today - timedelta(days=self.today.weekday())
            sunday = monday + timedelta(days=6)
            results.append(self._range_dict("this week", "relative_range", monday, sunday))

        # --- "last week" (not "last 2 weeks", handled above) ---------
        if re.search(r"\blast\s+week\b", q) and not re.search(r"\blast\s+\d+\s+week", q):
            this_monday = self.today - timedelta(days=self.today.weekday())
            last_sunday = this_monday - timedelta(days=1)
            last_monday = last_sunday - timedelta(days=6)
            results.append(self._range_dict("last week", "relative_range", last_monday, last_sunday))

        # --- "this month" ---------------------------------------------
        if re.search(r"\bthis\s+month\b", q):
            first = self.today.replace(day=1)
            last_day = calendar.monthrange(self.today.year, self.today.month)[1]
            last = self.today.replace(day=last_day)
            results.append(self._range_dict("this month", "relative_range", first, last))

        # --- "last month" (not "last 3 months") -----------------------
        if re.search(r"\blast\s+month\b", q) and not re.search(r"\blast\s+\d+\s+month", q):
            first_this = self.today.replace(day=1)
            end_prev = first_this - timedelta(days=1)
            start_prev = end_prev.replace(day=1)
            results.append(self._range_dict("last month", "relative_range", start_prev, end_prev))

        return results

    # ── Explicit date ranges ───────────────────────────────────────

    def _resolve_explicit_ranges(self, q: str) -> list[dict]:
        """
        Parse explicit ranges such as:
          - "from 10 september to 20 september"
          - "between january and march 2024"
          - "10 september to 20 september"   (no keyword)
        """
        results: list[dict] = []

        # Pattern WITH keyword: from/between ... to/and ...
        pat_kw = re.compile(
            rf"\b(?:from|between)\s+({_DATE_EXPR})\s+(?:to|and|till|until)\s+({_DATE_EXPR})",
            re.IGNORECASE,
        )
        m = pat_kw.search(q)
        if m:
            start_d = self._parse_date_expr(m.group(1))
            end_d = self._parse_date_expr(m.group(2), is_end=True)
            if start_d and end_d:
                results.append(self._range_dict(m.group(0), "explicit_range", start_d, end_d))
                return results   # one range per query is enough

        # Pattern WITHOUT keyword: DATE to DATE
        pat_bare = re.compile(
            rf"({_DATE_EXPR})\s+(?:to|till|until)\s+({_DATE_EXPR})",
            re.IGNORECASE,
        )
        m = pat_bare.search(q)
        if m:
            start_d = self._parse_date_expr(m.group(1))
            end_d = self._parse_date_expr(m.group(2), is_end=True)
            if start_d and end_d:
                results.append(self._range_dict(m.group(0), "explicit_range", start_d, end_d))

        return results

    # ── Date expression parser ─────────────────────────────────────

    def _parse_date_expr(self, expr: str, is_end: bool = False) -> Optional[date]:
        """
        Parse a textual date like "10 september", "september 2024".
        When *is_end* is True and the expression is a month-only or
        month+year, return the **last** day of that month.
        """
        expr = expr.strip().lower()

        # Try each month name (longest first avoids partial matches)
        for month_name in sorted(MONTH_MAP, key=len, reverse=True):
            mn = MONTH_MAP[month_name]
            esc = re.escape(month_name)

            # 10 september 2024
            m = re.fullmatch(rf"(\d{{1,2}})\s+{esc}\s+(\d{{4}})", expr)
            if m:
                return _safe_date(int(m.group(2)), mn, int(m.group(1)))

            # september 10, 2024
            m = re.fullmatch(rf"{esc}\s+(\d{{1,2}})\s*,?\s*(\d{{4}})", expr)
            if m:
                return _safe_date(int(m.group(2)), mn, int(m.group(1)))

            # 10 september
            m = re.fullmatch(rf"(\d{{1,2}})\s+{esc}", expr)
            if m:
                return _safe_date(self.today.year, mn, int(m.group(1)))

            # september 10
            m = re.fullmatch(rf"{esc}\s+(\d{{1,2}})", expr)
            if m:
                return _safe_date(self.today.year, mn, int(m.group(1)))

            # september 2024  (month + year)
            m = re.fullmatch(rf"{esc}\s+(\d{{4}})", expr)
            if m:
                yr = int(m.group(1))
                day = calendar.monthrange(yr, mn)[1] if is_end else 1
                return _safe_date(yr, mn, day)

            # september  (month only)
            if expr == month_name:
                day = calendar.monthrange(self.today.year, mn)[1] if is_end else 1
                return _safe_date(self.today.year, mn, day)

        return None

    # ── Helpers ────────────────────────────────────────────────────

    def _last_n(self, n: int, unit: str) -> tuple[date, date]:
        """Date range for 'last N days/weeks/months'."""
        end = self.today
        if unit == "day":
            start = end - timedelta(days=n)
        elif unit == "week":
            start = end - timedelta(weeks=n)
        elif unit == "month":
            month = self.today.month - n
            year = self.today.year
            while month <= 0:
                month += 12
                year -= 1
            day = min(self.today.day, calendar.monthrange(year, month)[1])
            start = date(year, month, day)
        else:
            start = end
        return start, end

    @staticmethod
    def _range_dict(original: str, rtype: str, start: date, end: date) -> dict:
        return {
            "original": original,
            "type": rtype,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "sql_hint": f"BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'",
        }


# ── Module-level helpers ──────────────────────────────────────────

def _safe_date(year: int, month: int, day: int) -> Optional[date]:
    """Return a ``date`` or ``None`` if the values are invalid."""
    try:
        return date(year, month, day)
    except ValueError:
        return None


# ── Convenience function ──────────────────────────────────────────

def resolve_dates(query: str, reference_date: Optional[date] = None) -> dict:
    """One-shot date resolution.  Returns the same dict as ``DateResolver.resolve()``."""
    return DateResolver(reference_date=reference_date).resolve(query)
