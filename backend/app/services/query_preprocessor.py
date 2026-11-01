"""
Query Preprocessor — Phase 1 of DataLensAI intelligence pipeline.

Cleans messy user input BEFORE it reaches the LLM:
  1. Text normalization (lowercase, extra spaces, punctuation)
  2. Spell correction (domain-specific dictionary)
  3. Abbreviation expansion (months, common terms)
  4. Fuzzy column-name matching (maps user words → actual DB columns)
  5. Value-aware hints (identifies data values like names in the query)

The goal: turn "give attendence from 10 sept to 20 sept"
       into "give attendance from 10 september to 20 september"
so the LLM receives clean, unambiguous English.
"""

import logging
import re
from difflib import get_close_matches

from app.services.date_resolver import resolve_dates

logger = logging.getLogger(__name__)


# ── 1. Domain-specific spell corrections ──────────────────────────
# Common misspellings seen in data-analyst queries (Indian English context)
SPELL_CORRECTIONS: dict[str, str] = {
    # Attendance / HR
    "attendence": "attendance",
    "attendace": "attendance",
    "attendnce": "attendance",
    "atendance": "attendance",
    "attandance": "attendance",
    "attedance": "attendance",
    "attendane": "attendance",
    "attendens": "attendance",
    "presnt": "present",
    "persent": "present",
    "persnt": "present",
    "presenet": "present",
    "presant": "present",
    "prezent": "present",
    "absnt": "absent",
    "abscent": "absent",
    "absense": "absence",
    "absance": "absence",
    "absentee": "absent",
    "employe": "employee",
    "employes": "employees",
    "employess": "employees",
    "emplyee": "employee",
    "empolyee": "employee",
    "emploee": "employee",
    "salry": "salary",
    "salery": "salary",
    "sallary": "salary",
    "salaray": "salary",
    "salari": "salary",
    "deprtment": "department",
    "departmnt": "department",
    "deparment": "department",
    "deptartment": "department",
    "depatment": "department",
    "managr": "manager",
    "manger": "manager",
    "maneger": "manager",
    "mangr": "manager",
    # Data / Analytics
    "revnue": "revenue",
    "reveneu": "revenue",
    "revenu": "revenue",
    "proffit": "profit",
    "prfit": "profit",
    "expnse": "expense",
    "expence": "expense",
    "expences": "expenses",
    "expeses": "expenses",
    "quatity": "quantity",
    "quantiy": "quantity",
    "quntity": "quantity",
    "quantty": "quantity",
    "totol": "total",
    "toatl": "total",
    "tatal": "total",
    "averge": "average",
    "avrage": "average",
    "avergae": "average",
    "avrg": "average",
    "percnt": "percent",
    "percntage": "percentage",
    "persentage": "percentage",
    "percentge": "percentage",
    "maximm": "maximum",
    "mximum": "maximum",
    "minimm": "minimum",
    "mnimum": "minimum",
    "comparision": "comparison",
    "comparsion": "comparison",
    "compariosn": "comparison",
    # General
    "detials": "details",
    "detils": "details",
    "ditails": "details",
    "deatils": "details",
    "informtion": "information",
    "infomation": "information",
    "infrmation": "information",
    "reord": "record",
    "recods": "records",
    "recrd": "record",
    "colum": "column",
    "colums": "columns",
    "coulmn": "column",
    "montly": "monthly",
    "mothly": "monthly",
    "mnthly": "monthly",
    "weakly": "weekly",
    "weeky": "weekly",
    "wekly": "weekly",
    "yealy": "yearly",
    "yerly": "yearly",
    "anual": "annual",
    "annul": "annual",
}


# ── 2. Abbreviation expansions ────────────────────────────────────
ABBREVIATIONS: dict[str, str] = {
    # Months
    "jan": "january",
    "feb": "february",
    "mar": "march",
    "apr": "april",
    "jun": "june",
    "jul": "july",
    "aug": "august",
    "sep": "september",
    "sept": "september",
    "oct": "october",
    "nov": "november",
    "dec": "december",
    # Days
    "mon": "monday",
    "tue": "tuesday",
    "tues": "tuesday",
    "wed": "wednesday",
    "thu": "thursday",
    "thur": "thursday",
    "thurs": "thursday",
    "fri": "friday",
    "sat": "saturday",
    "sun": "sunday",
    # Common terms
    "emp": "employee",
    "emps": "employees",
    "dept": "department",
    "depts": "departments",
    "mgr": "manager",
    "mgrs": "managers",
    "sal": "salary",
    "avg": "average",
    "max": "maximum",
    "min": "minimum",
    "num": "number",
    "no.": "number",
    "nos": "numbers",
    "qty": "quantity",
    "amt": "amount",
    "yr": "year",
    "yrs": "years",
    "mo": "month",
    "mos": "months",
    "wk": "week",
    "wks": "weeks",
    "hr": "hour",
    "hrs": "hours",
    "info": "information",
    "approx": "approximately",
    "pct": "percent",
    "perc": "percent",
    "diff": "difference",
    "prev": "previous",
    "curr": "current",
    "comp": "company",
    "org": "organization",
    "addr": "address",
    "ph": "phone",
    "desc": "description",
    "asc": "ascending",
    "grp": "group",
    "tot": "total",
    "calc": "calculate",
    "bw": "between",
    "w/o": "without",
    "govt": "government",
    "pvt": "private",
    "ltd": "limited",
    "sr": "senior",
    "jr": "junior",
    "hq": "headquarters",
    "acc": "account",
    "txn": "transaction",
    "txns": "transactions",
    "inv": "invoice",
    "invs": "invoices",
    "ot": "overtime",
    "lv": "leave",
    "lvs": "leaves",
    "att": "attendance",
    "attd": "attendance",
}


# ── 3. Number shorthand expansions ────────────────────────────────
NUMBER_PATTERNS: list[tuple[str, int]] = [
    (r"(\d+)\s*cr(?:ore)?s?\b", 10_000_000),   # 5cr → 50000000
    (r"(\d+)\s*[lL](?:akh|ac)?s?\b", 100_000),  # 2lakh → 200000
    (r"(\d+)\s*[kK]\b", 1_000),                  # 10k → 10000
]


# ── Core preprocessor ─────────────────────────────────────────────

class QueryPreprocessor:
    """
    Stateful preprocessor that uses schema context (column names and
    sample values) to intelligently clean user queries.
    """

    def __init__(
        self,
        column_names: list[str] | None = None,
        column_values: dict[str, list[str]] | None = None,
    ):
        """
        Args:
            column_names: List of actual DuckDB column names (e.g., ['first_name', 'department'])
            column_values: Dict mapping column names to their sample unique values
                           (e.g., {'first_name': ['Prachi', 'Nikhil', ...]})
        """
        self.column_names = column_names or []
        self.column_values = column_values or {}

        # Build a flat lookup: value_lower → (column_name, original_value)
        self._value_index: dict[str, tuple[str, str]] = {}
        for col, values in self.column_values.items():
            for v in values:
                if v and len(str(v)) > 1:  # skip single chars and nulls
                    self._value_index[str(v).lower()] = (col, str(v))

        # Human-readable column aliases (underscore → space)
        self._col_aliases: dict[str, str] = {}
        for col in self.column_names:
            # "first_name" → "first name", "email_address" → "email address"
            alias = col.replace("_", " ")
            self._col_aliases[alias] = col
            # Also single-word parts: "first" → "first_name", "email" → "email_address"
            parts = col.split("_")
            if len(parts) > 1:
                for part in parts:
                    if len(part) > 2 and part not in self._col_aliases:
                        self._col_aliases[part] = col

    def process(self, raw_query: str) -> dict:
        """
        Full preprocessing pipeline. Returns a dict with:
          - cleaned: the cleaned query string
          - original: the raw input
          - corrections: list of changes made (for debugging/logging)
          - value_hints: detected data values with their columns
        """
        original = raw_query.strip()
        corrections: list[str] = []
        value_hints: list[dict] = []

        text = original

        # Step 1: Basic normalization
        text = self._normalize(text)

        # Step 2: Expand number shorthands (10k → 10000)
        text, num_fixes = self._expand_numbers(text)
        corrections.extend(num_fixes)

        # Step 3: Spell correction (word-level)
        text, spell_fixes = self._correct_spelling(text)
        corrections.extend(spell_fixes)

        # Step 4: Abbreviation expansion (word-level)
        text, abbr_fixes = self._expand_abbreviations(text)
        corrections.extend(abbr_fixes)

        # Step 5: Fuzzy column-name matching
        text, col_fixes = self._match_columns(text)
        corrections.extend(col_fixes)

        # Step 6: Detect data values in query
        value_hints = self._detect_values(text)

        # Step 7: Resolve date/time expressions
        date_context = self._resolve_dates(text)

        # Final cleanup
        text = re.sub(r"\s+", " ", text).strip()

        if corrections:
            logger.info(
                f"Preprocessed query: '{original}' → '{text}' "
                f"({len(corrections)} corrections: {corrections})"
            )

        return {
            "cleaned": text,
            "original": original,
            "corrections": corrections,
            "value_hints": value_hints,
            "date_context": date_context,
        }

    # ── Step 1: Normalize ─────────────────────────────────────────

    @staticmethod
    def _normalize(text: str) -> str:
        """Lowercase, collapse whitespace, clean punctuation."""
        text = text.lower().strip()
        # Remove trailing question marks and periods (keep internal ones)
        text = text.rstrip("?.!")
        # Collapse multiple spaces/tabs into one
        text = re.sub(r"\s+", " ", text)
        # Normalize common quote variants
        text = text.replace("‘", "'").replace("’", "'")
        text = text.replace("“", '"').replace("”", '"')
        return text

    # ── Step 2: Number shorthands ─────────────────────────────────

    @staticmethod
    def _expand_numbers(text: str) -> tuple[str, list[str]]:
        """Expand shorthand like 10k→10000, 5cr→50000000."""
        fixes = []
        for pattern, multiplier in NUMBER_PATTERNS:
            for m in re.finditer(pattern, text, re.IGNORECASE):
                original_match = m.group(0)
                number = int(m.group(1)) * multiplier
                text = text.replace(original_match, str(number), 1)
                fixes.append(f"{original_match}→{number}")
        return text, fixes

    # ── Step 3: Spell correction ──────────────────────────────────

    @staticmethod
    def _correct_spelling(text: str) -> tuple[str, list[str]]:
        """Fix domain-specific misspellings word by word."""
        words = text.split()
        fixes = []
        result = []
        for word in words:
            # Strip trailing punctuation for lookup, preserve it
            stripped = word.rstrip(",.;:!?")
            suffix = word[len(stripped):]
            lower = stripped.lower()

            if lower in SPELL_CORRECTIONS:
                corrected = SPELL_CORRECTIONS[lower]
                fixes.append(f"{stripped}→{corrected}")
                result.append(corrected + suffix)
            else:
                result.append(word)
        return " ".join(result), fixes

    # ── Step 4: Abbreviation expansion ────────────────────────────

    @staticmethod
    def _expand_abbreviations(text: str) -> tuple[str, list[str]]:
        """Expand common abbreviations."""
        words = text.split()
        fixes = []
        result = []
        for word in words:
            stripped = word.rstrip(",.;:!?")
            suffix = word[len(stripped):]
            lower = stripped.lower()

            if lower in ABBREVIATIONS:
                expanded = ABBREVIATIONS[lower]
                fixes.append(f"{stripped}→{expanded}")
                result.append(expanded + suffix)
            else:
                result.append(word)
        return " ".join(result), fixes

    # ── Step 5: Fuzzy column matching ─────────────────────────────

    def _match_columns(self, text: str) -> tuple[str, list[str]]:
        """
        If a word looks like a misspelled column name, note the match as a hint
        WITHOUT rewriting the question text. This prevents biasing the LLM
        toward a single column interpretation.

        Returns the ORIGINAL text unchanged + a list of column-match hints.
        """
        if not self.column_names:
            return text, []

        words = text.split()
        fixes = []
        all_targets = list(self._col_aliases.keys()) + self.column_names

        for word in words:
            stripped = word.rstrip(",.;:!?")

            # Skip short words, numbers, and common English words
            if len(stripped) <= 2 or stripped.isdigit() or stripped in _COMMON_WORDS:
                continue

            # Skip if already a known column or alias
            if stripped in self.column_names or stripped in self._col_aliases:
                continue

            # Skip if it's a known data value
            if stripped in self._value_index:
                continue

            # Fuzzy match against column names/aliases
            matches = get_close_matches(stripped, all_targets, n=1, cutoff=0.75)
            if matches:
                match = matches[0]
                # Don't flag if the match is a common English word
                if match not in _COMMON_WORDS and match != stripped:
                    actual_col = self._col_aliases.get(match, match)
                    fixes.append(f"'{stripped}'≈column:{actual_col}")

        # Return original text unchanged — hints go as metadata, not text edits
        return text, fixes

    # ── Step 6: Value detection ───────────────────────────────────

    def _detect_values(self, text: str) -> list[dict]:
        """
        Scan the cleaned query for words/phrases that match known data values.
        Returns hints like: [{"value": "Prachi", "column": "first_name"}]
        """
        if not self._value_index:
            return []

        hints = []
        words = text.split()
        seen = set()

        # Check individual words
        for word in words:
            stripped = word.rstrip(",.;:!?").lower()
            if stripped in self._value_index and stripped not in seen:
                col, original_val = self._value_index[stripped]
                hints.append({"value": original_val, "column": col, "matched_word": stripped})
                seen.add(stripped)

        # Check two-word phrases (e.g., "engineering / it", "half day")
        for i in range(len(words) - 1):
            phrase = f"{words[i]} {words[i+1]}".lower()
            phrase_clean = phrase.rstrip(",.;:!?")
            if phrase_clean in self._value_index and phrase_clean not in seen:
                col, original_val = self._value_index[phrase_clean]
                hints.append({"value": original_val, "column": col, "matched_word": phrase_clean})
                seen.add(phrase_clean)

        # Check three-word phrases
        for i in range(len(words) - 2):
            phrase = f"{words[i]} {words[i+1]} {words[i+2]}".lower()
            phrase_clean = phrase.rstrip(",.;:!?")
            if phrase_clean in self._value_index and phrase_clean not in seen:
                col, original_val = self._value_index[phrase_clean]
                hints.append({"value": original_val, "column": col, "matched_word": phrase_clean})
                seen.add(phrase_clean)

        # Fuzzy match remaining words against values (catch typos in names)
        if len(self._value_index) <= 500:  # only for small value sets
            value_keys = list(self._value_index.keys())
            for word in words:
                stripped = word.rstrip(",.;:!?").lower()
                if stripped in seen or len(stripped) <= 2 or stripped in _COMMON_WORDS:
                    continue
                matches = get_close_matches(stripped, value_keys, n=1, cutoff=0.8)
                if matches and matches[0] not in seen:
                    col, original_val = self._value_index[matches[0]]
                    hints.append({
                        "value": original_val,
                        "column": col,
                        "matched_word": stripped,
                        "fuzzy": True,
                    })
                    seen.add(matches[0])

        return hints

    # ── Step 7: Date resolution ──────────────────────────────────

    @staticmethod
    def _resolve_dates(text: str) -> dict:
        """Resolve natural-language date expressions via the DateResolver service."""
        return resolve_dates(text)


# ── Convenience function ──────────────────────────────────────────

def preprocess_query(
    raw_query: str,
    column_names: list[str] | None = None,
    column_values: dict[str, list[str]] | None = None,
) -> dict:
    """One-shot preprocessing. Returns the same dict as QueryPreprocessor.process()."""
    pp = QueryPreprocessor(column_names=column_names, column_values=column_values)
    return pp.process(raw_query)


# ── Common English words to skip during fuzzy matching ────────────
# These are words that should NOT be matched to column names even if
# they're close in edit distance.
_COMMON_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "must", "can", "could", "need",
    "i", "me", "my", "we", "our", "you", "your", "he", "she", "it",
    "they", "them", "their", "this", "that", "these", "those",
    "what", "which", "who", "whom", "whose", "where", "when", "why", "how",
    "all", "each", "every", "both", "few", "more", "most", "other",
    "some", "any", "no", "not", "only", "own", "same", "so", "than",
    "too", "very", "just", "also", "now", "then", "here", "there",
    "and", "but", "or", "nor", "for", "yet", "with", "from", "to",
    "in", "on", "at", "by", "of", "up", "out", "off", "over", "under",
    "about", "into", "through", "during", "before", "after", "between",
    "show", "give", "get", "find", "list", "display", "tell", "fetch",
    "data", "detail", "details", "result", "results", "report", "table",
    "row", "rows", "column", "columns", "value", "values", "record",
    "records", "entry", "entries", "count", "total", "sum", "average",
    "first", "last", "next", "previous", "new", "old", "top", "bottom",
    "many", "much", "less", "more", "like", "per", "each",
    "today", "yesterday", "tomorrow", "week", "month", "year", "day",
    "date", "time", "from", "since", "until", "till", "between",
    "compare", "comparison", "difference", "change", "trend",
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "name", "email", "phone", "address", "status", "type", "code",
})
