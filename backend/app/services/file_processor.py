"""
File processing service — parses CSV, PDF, DOCX, XLSX into pandas DataFrames.
Each file type has its own extraction strategy.
"""

import io
import logging
import re
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


class FileProcessingError(Exception):
    """Raised when a file cannot be processed."""
    pass


def _clean_col(name: str) -> str:
    """Normalize a column name for DuckDB and LLM compatibility.

    Examples:
        'First Name [Required]' → 'first_name'
        'Email Address [Required]' → 'email_address'
        'Org Unit Path [Required]' → 'org_unit_path'
        'Revenue ($)' → 'revenue'
    """
    s = str(name).strip()
    # Remove anything inside brackets, parens, or curly braces (and the brackets themselves)
    s = re.sub(r"\s*[\[\(\{][^\]\)\}]*[\]\)\}]", "", s)
    s = s.strip().lower()
    # Replace spaces, hyphens, dots with underscores
    s = re.sub(r"[\s\-\.]+", "_", s)
    # Remove any remaining non-alphanumeric chars (except underscores)
    s = re.sub(r"[^a-z0-9_]", "", s)
    # Collapse multiple underscores and strip leading/trailing
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "column"


def _dedup_columns(columns: list[str]) -> list[str]:
    """Ensure column names are unique by appending _2, _3, etc."""
    seen: dict[str, int] = {}
    result = []
    for col in columns:
        if col in seen:
            seen[col] += 1
            result.append(f"{col}_{seen[col]}")
        else:
            seen[col] = 1
            result.append(col)
    return result


class DateFormatResult:
    """Result of date format detection with details for ambiguity handling."""

    __slots__ = ("dayfirst", "format_type", "mixed", "invalid_count",
                 "total_checked", "detail")

    def __init__(
        self,
        dayfirst: bool | None = None,
        format_type: str = "unknown",
        mixed: bool = False,
        invalid_count: int = 0,
        total_checked: int = 0,
        detail: str = "",
    ):
        self.dayfirst = dayfirst
        self.format_type = format_type      # iso, dayfirst, monthfirst, mixed, ambiguous, unknown
        self.mixed = mixed                   # True if multiple formats detected
        self.invalid_count = invalid_count   # count of unparseable values
        self.total_checked = total_checked
        self.detail = detail


def _detect_dayfirst(series: pd.Series) -> bool | None:
    """
    Heuristic to detect whether date values use day-first (DD/MM/YYYY)
    or month-first (MM/DD/YYYY) format.

    Enhanced strategy (Phase 2):
      - Sample up to 100 non-null date-looking values
      - Detect ISO (YYYY-MM-DD), day-first, month-first, named-month, and mixed formats
      - Flag mixed formats (e.g., some ISO + some DD/MM) with a warning
      - Count invalid/unparseable values
      - Return None for ambiguous cases (all parts ≤ 12)
    """
    result = detect_date_format(series)
    return result.dayfirst


def detect_date_format(series: pd.Series) -> DateFormatResult:
    """
    Full date format detection with detailed results.

    Returns a DateFormatResult with:
      - dayfirst: True/False/None
      - format_type: iso, dayfirst, monthfirst, named_month, ambiguous, mixed, unknown
      - mixed: True if multiple incompatible formats detected
      - invalid_count: number of values that couldn't be parsed as dates
      - detail: human-readable explanation
    """
    sample = series.dropna().head(100)

    if sample.empty:
        return DateFormatResult(detail="No non-null values found")

    iso_count = 0
    dayfirst_count = 0       # part1 > 12 → definitely day-first
    monthfirst_count = 0     # part2 > 12 → definitely month-first
    ambiguous_count = 0      # both parts ≤ 12, can't tell
    named_month_count = 0    # "15-Jan-2024" or "Jan 15, 2024"
    invalid_count = 0
    total = 0

    # Named month patterns
    month_names_re = (
        r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    )

    for val in sample:
        s = str(val).strip()
        if not s or s.lower() in ("nat", "none", "null", "nan", ""):
            continue
        total += 1

        # 1. ISO format: YYYY-MM-DD or YYYY/MM/DD
        m_iso = re.match(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
        if m_iso:
            month_part = int(m_iso.group(2))
            day_part = int(m_iso.group(3))
            if 1 <= month_part <= 12 and 1 <= day_part <= 31:
                iso_count += 1
                continue
            else:
                invalid_count += 1
                continue

        # 2. Named month: "15-Jan-2024", "Jan 15, 2024", "15 January 2024"
        if re.search(month_names_re, s, re.IGNORECASE):
            named_month_count += 1
            continue

        # 3. Regional: DD/MM/YYYY or MM/DD/YYYY
        m = re.match(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", s)
        if m:
            part1, part2 = int(m.group(1)), int(m.group(2))
            year_part = int(m.group(3))

            # Basic validation
            if part1 > 31 or part2 > 31 or part1 == 0 or part2 == 0:
                invalid_count += 1
                continue

            if part1 > 12 and part2 <= 12:
                dayfirst_count += 1    # e.g., 25/03/2024 → day-first
            elif part2 > 12 and part1 <= 12:
                monthfirst_count += 1  # e.g., 03/25/2024 → month-first
            elif part1 <= 12 and part2 <= 12:
                ambiguous_count += 1   # e.g., 03/05/2024 → can't tell
            else:
                invalid_count += 1     # both > 12 → invalid date
            continue

        # 4. Unrecognized format
        # Check if it might still be a date (has digits and separators)
        if re.search(r"\d", s):
            invalid_count += 1
        # else: not a date at all, skip

    if total == 0:
        return DateFormatResult(detail="No date-like values found")

    # ── Decision logic ──

    # Pure ISO
    if iso_count > 0 and dayfirst_count == 0 and monthfirst_count == 0:
        return DateFormatResult(
            dayfirst=False,
            format_type="iso",
            invalid_count=invalid_count,
            total_checked=total,
            detail=f"ISO format (YYYY-MM-DD): {iso_count}/{total} values",
        )

    # Pure named month (unambiguous)
    if named_month_count > 0 and dayfirst_count == 0 and monthfirst_count == 0 and iso_count == 0:
        return DateFormatResult(
            dayfirst=True,  # pandas handles named months well with dayfirst=True
            format_type="named_month",
            invalid_count=invalid_count,
            total_checked=total,
            detail=f"Named month format: {named_month_count}/{total} values",
        )

    # Clear day-first (e.g., DD/MM/YYYY)
    if dayfirst_count > 0 and monthfirst_count == 0:
        mixed = iso_count > 0 or named_month_count > 0
        return DateFormatResult(
            dayfirst=True,
            format_type="mixed" if mixed else "dayfirst",
            mixed=mixed,
            invalid_count=invalid_count,
            total_checked=total,
            detail=f"Day-first (DD/MM/YYYY): {dayfirst_count} definite"
                   + (f", {iso_count} ISO, {named_month_count} named" if mixed else "")
                   + (f", {ambiguous_count} ambiguous" if ambiguous_count else ""),
        )

    # Clear month-first (e.g., MM/DD/YYYY)
    if monthfirst_count > 0 and dayfirst_count == 0:
        mixed = iso_count > 0 or named_month_count > 0
        return DateFormatResult(
            dayfirst=False,
            format_type="mixed" if mixed else "monthfirst",
            mixed=mixed,
            invalid_count=invalid_count,
            total_checked=total,
            detail=f"Month-first (MM/DD/YYYY): {monthfirst_count} definite"
                   + (f", {iso_count} ISO, {named_month_count} named" if mixed else "")
                   + (f", {ambiguous_count} ambiguous" if ambiguous_count else ""),
        )

    # CONFLICT: both day-first and month-first detected → mixed format!
    if dayfirst_count > 0 and monthfirst_count > 0:
        # Use the majority format
        majority_dayfirst = dayfirst_count >= monthfirst_count
        return DateFormatResult(
            dayfirst=majority_dayfirst,
            format_type="mixed",
            mixed=True,
            invalid_count=invalid_count,
            total_checked=total,
            detail=f"MIXED FORMAT WARNING: {dayfirst_count} day-first + {monthfirst_count} month-first. "
                   f"Using {'day-first' if majority_dayfirst else 'month-first'} (majority). "
                   f"Some dates may be misinterpreted!",
        )

    # All ambiguous (all parts ≤ 12)
    if ambiguous_count > 0:
        return DateFormatResult(
            dayfirst=None,
            format_type="ambiguous",
            invalid_count=invalid_count,
            total_checked=total,
            detail=f"Ambiguous format: {ambiguous_count}/{total} values have both parts ≤ 12. "
                   f"Cannot determine DD/MM vs MM/DD without context.",
        )

    return DateFormatResult(
        invalid_count=invalid_count,
        total_checked=total,
        detail=f"Could not determine format from {total} values "
               f"({invalid_count} invalid, {iso_count} ISO, {named_month_count} named)",
    )


def _coerce_types(df: pd.DataFrame) -> pd.DataFrame:
    """Try to convert columns to numeric or datetime types.

    Enhanced (Phase 2): uses detect_date_format() for detailed format
    detection, logs warnings for mixed/ambiguous formats, and handles
    invalid dates gracefully.
    """
    for col in df.columns:
        # Skip columns that are already numeric
        if pd.api.types.is_numeric_dtype(df[col]):
            continue
        # Try numeric first
        converted = pd.to_numeric(df[col], errors="coerce")
        if converted.notna().sum() > 0.5 * df[col].notna().sum():
            df[col] = converted
            continue
        # Try datetime
        if pd.api.types.is_string_dtype(df[col]):
            sample = df[col].dropna().head(10)
            if sample.empty:
                continue
            # Check if values look like dates
            date_patterns = any(
                re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}", str(v))
                for v in sample
            )
            if not date_patterns:
                # Also check for named-month formats like "15-Jan-2024"
                month_re = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
                date_patterns = any(
                    re.search(month_re, str(v), re.IGNORECASE) and re.search(r"\d", str(v))
                    for v in sample
                )

            if date_patterns:
                # Enhanced detection with full reporting
                fmt_result = detect_date_format(df[col])

                if fmt_result.mixed:
                    logger.warning(
                        f"Column '{col}': {fmt_result.detail}"
                    )
                if fmt_result.invalid_count > 0:
                    logger.warning(
                        f"Column '{col}': {fmt_result.invalid_count}/{fmt_result.total_checked} "
                        f"date values could not be parsed"
                    )

                dayfirst = fmt_result.dayfirst
                try:
                    kwargs = {"errors": "coerce", "format": "mixed"}
                    if dayfirst is not None:
                        kwargs["dayfirst"] = dayfirst
                    df[col] = pd.to_datetime(df[col], **kwargs)
                except Exception:
                    try:
                        df[col] = pd.to_datetime(df[col], errors="coerce")
                    except Exception:
                        pass

                # Log how many NaTs resulted from coercion
                if pd.api.types.is_datetime64_any_dtype(df[col]):
                    nat_count = df[col].isna().sum() - sample.isna().sum()
                    if nat_count > 0:
                        logger.info(
                            f"Column '{col}': {nat_count} values became NaT after date parsing "
                            f"(format: {fmt_result.format_type})"
                        )
    return df


# ── CSV ──────────────────────────────────────────────────────────────

def process_csv(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """Parse a CSV file into a DataFrame."""
    try:
        # Try common encodings
        for encoding in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
            try:
                df = pd.read_csv(io.BytesIO(file_bytes), encoding=encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise FileProcessingError(f"Could not decode {filename} with any common encoding.")

        if df.empty:
            raise FileProcessingError(f"CSV file {filename} is empty.")

        # Clean column names: strip brackets/tags, lowercase, replace spaces with underscores
        df.columns = _dedup_columns([_clean_col(c) for c in df.columns])

        logger.info(f"CSV processed: {filename} — {len(df)} rows, {len(df.columns)} columns")
        return df

    except FileProcessingError:
        raise
    except Exception as e:
        raise FileProcessingError(f"Failed to parse CSV '{filename}': {e}")


# ── PDF (major rewrite) ─────────────────────────────────────────────

def _parse_space_separated_text(text_lines: list[str]) -> pd.DataFrame | None:
    """
    Parse space-separated tabular text extracted from a PDF.

    Many PDFs (especially attendance reports) have columns separated by
    multiple spaces but no explicit delimiter. This function detects
    consistent column boundaries using the header row's whitespace gaps.
    """
    if len(text_lines) < 3:
        return None

    # Filter out empty/very short lines and obvious non-data lines
    clean_lines = []
    for line in text_lines:
        stripped = line.strip()
        if not stripped or len(stripped) < 3:
            continue
        # Skip page numbers, headers like "Page 1 of 5"
        if re.match(r"^(page\s+\d+|---|===|\*\*\*)", stripped, re.IGNORECASE):
            continue
        clean_lines.append(stripped)

    if len(clean_lines) < 3:
        return None

    # Strategy: find the header line (the one with the most distinct "words"
    # separated by 2+ spaces, appearing near the top)
    best_header_idx = -1
    best_col_count = 0
    for i in range(min(5, len(clean_lines))):
        # Split on 2+ spaces
        parts = re.split(r"\s{2,}", clean_lines[i])
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) >= 3 and len(parts) > best_col_count:
            # Check that the next few lines have similar word count
            sample_counts = []
            for j in range(i + 1, min(i + 6, len(clean_lines))):
                sample_parts = re.split(r"\s{2,}", clean_lines[j])
                sample_parts = [p.strip() for p in sample_parts if p.strip()]
                sample_counts.append(len(sample_parts))
            if sample_counts:
                avg_count = sum(sample_counts) / len(sample_counts)
                # Header and data should have roughly similar column counts
                if abs(avg_count - len(parts)) <= 2:
                    best_header_idx = i
                    best_col_count = len(parts)

    if best_header_idx < 0 or best_col_count < 2:
        return None

    header_line = clean_lines[best_header_idx]
    header_parts = re.split(r"\s{2,}", header_line)
    headers = [_clean_col(h) for h in header_parts if h.strip()]
    headers = _dedup_columns(headers)
    num_cols = len(headers)

    # Parse data rows
    data_rows = []
    for line in clean_lines[best_header_idx + 1:]:
        parts = re.split(r"\s{2,}", line)
        parts = [p.strip() for p in parts if p.strip()]

        if not parts:
            continue

        # If column count matches (± 1), treat as data row
        if abs(len(parts) - num_cols) <= 1:
            # Pad or trim to match header count
            if len(parts) < num_cols:
                parts.extend([""] * (num_cols - len(parts)))
            elif len(parts) > num_cols:
                # Merge extra parts into the last column
                parts = parts[:num_cols - 1] + [" ".join(parts[num_cols - 1:])]
            data_rows.append(parts)

    if len(data_rows) < 2:
        return None

    df = pd.DataFrame(data_rows, columns=headers)
    # Clean up whitespace in all string columns
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].str.strip()

    df = _coerce_types(df)

    logger.info(f"PDF text parsed as space-separated table: {len(df)} rows, {num_cols} cols")
    return df


def _is_likely_header(row: list[str]) -> bool:
    """
    Determine whether a row is a table header vs a row of data.

    Looks for data-like signals (timestamps, day names, proper names,
    timezone strings) and header-like signals (common column-name words).
    Returns ``True`` only when header signals clearly dominate.
    """
    # Generic header words — domain-neutral, kept small so detection
    # relies primarily on statistical patterns below.
    HEADER_WORDS = {
        "name", "id", "number", "no", "sr", "sno",
        "date", "day", "time", "status", "type", "category",
        "code", "total", "count", "description", "title", "role",
        "email", "phone", "address", "location", "action",
        "remark", "remarks", "value", "amount", "price", "cost",
        "quantity", "unit", "size", "weight", "height", "width",
        "rating", "score", "level", "grade", "rank", "index",
        "source", "target", "key", "label", "tag", "group",
        "region", "country", "city", "state", "zip", "year", "month",
    }
    DAY_NAMES = {
        "monday", "tuesday", "wednesday", "thursday", "friday",
        "saturday", "sunday", "mon", "tue", "wed", "thu", "fri",
        "sat", "sun",
    }
    MONTH_NAMES = {
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november",
        "december", "jan", "feb", "mar", "apr", "jun", "jul",
        "aug", "sep", "oct", "nov", "dec",
    }

    if not row:
        return False

    data_signals = 0
    header_signals = 0

    for cell in row:
        cell_clean = cell.strip()
        cell_lower = cell_clean.lower()
        cell_words = set(cell_lower.split())

        # ── Data-like signals ────────────────────────────────────
        # Time pattern (HH:MM or HH:MM:SS)
        if re.search(r"\d{1,2}:\d{2}(:\d{2})?", cell_clean):
            data_signals += 2

        # ISO date (YYYY-MM-DD)
        if re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", cell_clean):
            data_signals += 2

        # Regional date (DD-MM-YYYY or MM/DD/YYYY)
        if re.search(r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}", cell_clean):
            data_signals += 1

        # Day name
        if cell_words & DAY_NAMES:
            data_signals += 1

        # Month name + digits (e.g. "Oct 22, 2026")
        if cell_words & MONTH_NAMES and re.search(r"\d", cell_clean):
            data_signals += 1

        # Purely numeric
        if re.match(r"^[\d,.]+$", cell_clean):
            data_signals += 1

        # Timezone strings (GMT, UTC, +0400, IST, …)
        if re.search(r"\bGMT\b|\bUTC\b|\bIST\b|[+-]\d{4}", cell_clean):
            data_signals += 2

        # Proper name pattern — multi-word capitalized names like
        # "Abhishek Chauhan" (single cap words like "Age" or "City"
        # are ambiguous and should NOT trigger this).
        if (
            re.match(r"^[A-Z][a-z]+(\s+[A-Z][a-z]+)+$", cell_clean)
            and not cell_words & HEADER_WORDS
            and len(cell_words) <= 4
        ):
            data_signals += 1

        # Very long cell (>40 chars) — usually data, not header
        if len(cell_clean) > 40:
            data_signals += 1

        # ── Header-like signals ──────────────────────────────────
        if cell_words & HEADER_WORDS:
            header_signals += 1

        # Short and purely alphabetic (like "Status", "Name")
        if (
            cell_clean.isalpha()
            and len(cell_clean) <= 20
            and not cell_words & DAY_NAMES
            and not cell_words & MONTH_NAMES
        ):
            header_signals += 0.5

        # Snake_case or camelCase or kebab-case patterns — strong header
        if re.match(r"^[a-z][a-z0-9]*([_-][a-z0-9]+)+$", cell_clean):
            header_signals += 1.5
        elif re.match(r"^[a-z]+[A-Z][a-zA-Z0-9]*$", cell_clean):
            header_signals += 1.5

        # ALL_CAPS short identifier (like "ID", "SKU", "DOB")
        if re.match(r"^[A-Z]{2,6}$", cell_clean):
            header_signals += 1

    # ── Statistical signals ─────────────────────────────────────────
    # All cells unique (high entropy) is a mild header signal
    unique_ratio = len(set(c.strip().lower() for c in row)) / max(len(row), 1)
    if unique_ratio == 1.0:
        header_signals += 0.5

    # Mostly non-numeric cells → more likely header
    non_numeric = sum(1 for c in row if not re.match(r"^[\d,.%$€£₹+-]+$", c.strip()))
    if non_numeric >= len(row) * 0.7:
        header_signals += 0.5

    # Header must have clearly MORE header signals than data signals,
    # and at least 30% of cells should look header-like
    return (
        header_signals > data_signals
        and header_signals >= len(row) * 0.3
    )


def _join_wrapped_lines(
    non_empty: list[str], delimiter: str, target_delims: int
) -> list[str]:
    """
    Rejoin PDF text lines that were soft-wrapped mid-record.

    Scans *non_empty* and merges a line into its predecessor whenever the
    predecessor has fewer than *target_delims* occurrences of *delimiter*.
    """
    joined: list[str] = []
    for line in non_empty:
        if not joined:
            joined.append(line)
            continue
        prev_delims = joined[-1].count(delimiter)
        if prev_delims > 0 and prev_delims < target_delims:
            combined = joined[-1] + " " + line
            if combined.count(delimiter) <= target_delims:
                joined[-1] = combined
                continue
        joined.append(line)
    return joined


def _score_joining(
    non_empty: list[str], delimiter: str, target_delims: int,
) -> tuple[list[str], int, int, int]:
    """
    Try joining with *target_delims* and return
    ``(joined_lines, dominant_field_count, consistent_rows, total_parsed)``.
    """
    from collections import Counter

    joined = _join_wrapped_lines(non_empty, delimiter, target_delims)

    parsed: list[list[str]] = []
    for line in joined:
        parts = [p.strip() for p in line.split(delimiter)]
        if len(parts) >= 3:
            parsed.append(parts)

    if not parsed:
        return joined, 0, 0, 0

    field_counts = Counter(len(row) for row in parsed)
    best_count, best_freq = field_counts.most_common(1)[0]
    return joined, best_count, best_freq, len(parsed)


def _parse_delimited_text(text_lines: list[str]) -> pd.DataFrame | None:
    """
    Parse delimiter-separated text extracted from a PDF.

    Tries ``|``, ``\\t``, ``,``, ``;`` in that order.  Prioritised over
    pdfplumber structured table extraction because PDFs whose visible content
    is delimited text (common in attendance reports, exported logs, etc.)
    get garbled by pdfplumber's visual-column detection — long fields such
    as timestamps are split into multiple cells, doubling the column count.

    Returns a clean DataFrame, or ``None`` if no consistent delimiter is found.
    """
    from collections import Counter

    if len(text_lines) < 3:
        return None

    # Keep only non-trivial lines
    non_empty = [
        line.strip()
        for line in text_lines
        if line.strip() and len(line.strip()) >= 3
    ]
    if len(non_empty) < 3:
        return None

    for delimiter in ["|", "\t", ",", ";"]:
        # ── Pre-process: join wrapped lines ──────────────────────────
        # PDFs soft-wrap long lines, so one logical record may span two
        # or more text lines.  For pipe / tab data we detect the expected
        # delimiter count per record and rejoin partial lines.
        #
        # KEY FIX: when most PDF lines are partial (wrapped mid-record),
        # the most common pipe count is the PARTIAL count, not the full
        # record count.  We now try every observed count from high to low
        # and pick whichever produces the most consistent records.
        if delimiter in ("|", "\t"):
            all_counts = [
                line.count(delimiter)
                for line in non_empty
                if line.count(delimiter) >= 1
            ]
            if not all_counts:
                continue

            # Try candidate expected-delim counts, highest first
            candidates = sorted(set(all_counts), reverse=True)

            best_joined: list[str] | None = None
            best_field_count = 0
            best_consistent = 0

            for candidate in candidates:
                if candidate < 2:
                    continue
                joined, fcount, consistent, total = _score_joining(
                    non_empty, delimiter, candidate,
                )
                if fcount < 3 or total == 0:
                    continue
                # Prefer the candidate that produces:
                #   1. More consistent rows (primary)
                #   2. Higher field count (secondary — more columns = fuller records)
                if consistent > best_consistent or (
                    consistent == best_consistent and fcount > best_field_count
                ):
                    best_consistent = consistent
                    best_field_count = fcount
                    best_joined = joined

            if best_joined is None:
                continue
            working = best_joined
        else:
            working = list(non_empty)

        # ── Split each line by the delimiter ─────────────────────────
        parsed: list[list[str]] = []
        for line in working:
            parts = [p.strip() for p in line.split(delimiter)]
            if len(parts) >= 3:
                parsed.append(parts)

        if len(parsed) < 3:
            continue

        # ── Find the most common field count ─────────────────────────
        field_counts = Counter(len(row) for row in parsed)
        best_count, best_freq = field_counts.most_common(1)[0]

        if best_count < 3:
            continue

        # Consistency threshold — stricter for comma (appears in prose)
        threshold = 0.70 if delimiter in ("|", "\t") else 0.85
        if best_freq < len(parsed) * threshold:
            continue

        # Keep only rows matching the dominant field count
        data_rows = [row for row in parsed if len(row) == best_count]
        non_matching = [row for row in parsed if len(row) != best_count]
        if len(data_rows) < 2:
            continue

        # ── Detect the header row ────────────────────────────────────
        # 1) Check if the first consistent row is a real header
        first = data_rows[0]
        if _is_likely_header(first):
            headers = _dedup_columns([_clean_col(h) for h in first])
            data = data_rows[1:]
        else:
            # 2) Scan non-matching rows that appear BEFORE the first
            #    data row for a plausible header.  Often the header has
            #    a slightly different field count (e.g. one fewer pipe
            #    because of a merged cell or missing trailing pipe).
            header_found = False
            first_data_line = working[0] if working else ""
            # Find the position of the first data row in the working lines
            first_data_idx = None
            for wi, wline in enumerate(working):
                parts = [p.strip() for p in wline.split(delimiter)]
                if len(parts) == best_count:
                    first_data_idx = wi
                    break

            if first_data_idx is not None:
                # Look at the few lines just before the first data row
                for candidate_row in non_matching:
                    # Must appear before the data rows in the original text
                    # and have a close field count (within ±2)
                    if abs(len(candidate_row) - best_count) <= 2 and _is_likely_header(candidate_row):
                        # Pad or trim to match data width
                        hdr = list(candidate_row)
                        if len(hdr) < best_count:
                            hdr.extend([f"col_{i}" for i in range(len(hdr), best_count)])
                        elif len(hdr) > best_count:
                            hdr = hdr[:best_count]
                        headers = _dedup_columns([_clean_col(h) for h in hdr])
                        data = data_rows
                        header_found = True
                        break

            if not header_found:
                # 3) Fallback: generate column names
                headers = [f"column_{i + 1}" for i in range(best_count)]
                data = data_rows

        if not data:
            continue

        df = pd.DataFrame(data, columns=headers)
        for col in df.columns:
            if df[col].dtype == object:
                df[col] = df[col].str.strip()

        # Drop fully-empty rows and columns
        df.replace("", pd.NA, inplace=True)
        df.dropna(how="all", inplace=True)
        df.dropna(axis=1, how="all", inplace=True)

        if df.empty or len(df.columns) < 2:
            continue

        df = _coerce_types(df)

        logger.info(
            f"PDF text parsed as {delimiter!r}-delimited: "
            f"{len(df)} rows, {len(df.columns)} cols"
        )
        return df

    return None


def _group_compatible_tables(
    all_tables: list[list[list[str]]],
) -> list[pd.DataFrame]:
    """
    Group extracted raw tables by compatible column structure and build
    DataFrames. Tables from different pages of the same PDF often have
    the same header repeated — group them together so pd.concat doesn't
    produce the union of 84 different column sets.

    Strategy:
      - Normalise the header row of each table
      - Group tables whose column count matches exactly
      - Within each group, pick the most common header set
      - Only concat tables that share the same header
    """
    if not all_tables:
        return []

    # Build (normalised_headers_tuple, data_rows) for each table
    table_info: list[tuple[tuple[str, ...], list[list[str | None]]]] = []
    for table in all_tables:
        raw_headers = table[0]
        headers = tuple(
            _clean_col(h) if h and h.strip() else f"col_{i}"
            for i, h in enumerate(raw_headers)
        )
        # Skip tables with too many columns (likely garbage extraction)
        if len(headers) > 30:
            logger.debug(f"Skipping extracted table with {len(headers)} columns (too many)")
            continue
        data = table[1:]
        if data:
            table_info.append((headers, data))

    if not table_info:
        return []

    # Group by column count first, then by exact header match
    from collections import Counter, defaultdict

    by_col_count: dict[int, list[tuple[tuple[str, ...], list]]] = defaultdict(list)
    for hdr, data in table_info:
        by_col_count[len(hdr)].append((hdr, data))

    frames: list[pd.DataFrame] = []
    for col_count, group in by_col_count.items():
        # Find the most common header set in this group
        header_counts = Counter(hdr for hdr, _ in group)
        best_header = header_counts.most_common(1)[0][0]
        num_cols = len(best_header)
        headers_list = list(_dedup_columns(list(best_header)))

        # Merge all data rows from tables that match this header
        merged_rows: list[list[str | None]] = []
        for hdr, data in group:
            # Accept if header matches exactly, or if only column count matches
            # (subsequent pages often repeat the header or have slight variations)
            if len(hdr) == num_cols:
                for row in data:
                    if not row:
                        continue
                    # Check if this row IS a repeated header (skip it)
                    cleaned_row_check = tuple(
                        _clean_col(str(cell)) if cell else "" for cell in row[:num_cols]
                    )
                    if cleaned_row_check == best_header:
                        continue  # Skip repeated header rows

                    # Pad or trim
                    if len(row) < num_cols:
                        row = list(row) + [None] * (num_cols - len(row))
                    elif len(row) > num_cols:
                        row = row[:num_cols]
                    # Clean cell values
                    row = [
                        str(cell).strip() if cell and str(cell).strip() else None
                        for cell in row
                    ]
                    merged_rows.append(row)

        if merged_rows:
            df = pd.DataFrame(merged_rows, columns=headers_list)
            frames.append(df)

    # If we'd still end up with too many columns after concat, keep only the
    # largest group (most rows) — it's the real table
    if len(frames) > 1:
        total_cols = sum(len(f.columns) for f in frames)
        if total_cols > 30:
            frames.sort(key=len, reverse=True)
            frames = [frames[0]]

    return frames


def process_pdf(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """
    Extract tables from a PDF file.
    Strategy (multi-pass, ordered by reliability):
      1. **Delimiter-based text parsing** (``|``, ``\\t``, ``,``, ``;``)
         — runs first because pipe / comma-delimited text in PDFs gets
           garbled by pdfplumber's visual table detection (timestamps split
           into multiple cells, doubling the column count).
      2. pdfplumber with default table detection
      3. pdfplumber with aggressive settings (text-edge strategy)
      4. Space-separated text parsing (for borderless tables)
      5. Last resort: single-column text content

    Tables from different pages are grouped by compatible column structure
    before combining, to prevent the column-explosion problem (e.g. 84 cols)
    caused by naively concatenating tables with different schemas.
    """
    try:
        import pdfplumber

        all_tables: list[list[list[str]]] = []
        text_lines: list[str] = []

        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                # ── Always extract text first (needed for Priority 1) ──
                page_text = page.extract_text()
                if page_text:
                    text_lines.extend(page_text.strip().split("\n"))

                # ── Structured table extraction (saved for Priority 2/3) ──
                tables = page.extract_tables()
                found_table = False
                for table in tables:
                    if table and len(table) > 1:
                        valid_rows = [r for r in table if r and len(r) >= 2]
                        if len(valid_rows) > len(table) * 0.5:
                            all_tables.append(valid_rows)
                            found_table = True

                if not found_table:
                    try:
                        tables = page.extract_tables(
                            table_settings={
                                "vertical_strategy": "text",
                                "horizontal_strategy": "text",
                                "snap_x_tolerance": 5,
                                "snap_y_tolerance": 5,
                                "join_x_tolerance": 5,
                                "join_y_tolerance": 5,
                                "min_words_vertical": 2,
                                "min_words_horizontal": 2,
                            }
                        )
                        for table in tables:
                            if table and len(table) > 1:
                                valid_rows = [r for r in table if r and len(r) >= 2]
                                if len(valid_rows) > len(table) * 0.5:
                                    all_tables.append(valid_rows)
                    except Exception:
                        pass

        # ── Priority 1: Delimiter-based text parsing ─────────────────
        # Must run BEFORE pdfplumber results.  PDFs with pipe/comma/tab
        # delimited text get garbled by pdfplumber's visual-column
        # detection (e.g. 6-column attendance data → 13 garbage columns).
        if text_lines:
            df = _parse_delimited_text(text_lines)
            if df is not None and len(df) > 0 and len(df.columns) >= 2:
                logger.info(
                    f"PDF parsed via delimiter detection: {filename} "
                    f"— {len(df)} rows, {len(df.columns)} cols"
                )
                return df

        # ── Priority 2/3: pdfplumber structured extraction ───────────
        if all_tables:
            frames = _group_compatible_tables(all_tables)

            if frames:
                combined = pd.concat(frames, ignore_index=True)
                combined.dropna(how="all", inplace=True)
                combined.dropna(axis=1, how="all", inplace=True)
                combined = _coerce_types(combined)

                if len(combined.columns) <= 30 and len(combined) > 0:
                    logger.info(
                        f"PDF tables extracted: {filename} "
                        f"— {len(combined)} rows, {len(combined.columns)} cols"
                    )
                    return combined
                else:
                    logger.warning(
                        f"PDF structured extraction produced "
                        f"{len(combined.columns)} cols for {filename} "
                        f"— falling through to text parsing"
                    )

        # ── Priority 4: Space-separated text parsing ─────────────────
        if text_lines:
            df = _parse_space_separated_text(text_lines)
            if df is not None and len(df) > 0 and len(df.columns) >= 2:
                logger.info(
                    f"PDF parsed via space-separated text: {filename} "
                    f"— {len(df)} rows"
                )
                return df

        # ── Priority 5: Single-column text (last resort) ────────────
        if text_lines:
            non_empty = [line.strip() for line in text_lines if line.strip()]
            if non_empty:
                df = pd.DataFrame({"content": non_empty})
                logger.info(
                    f"PDF text extracted as content: {filename} "
                    f"— {len(df)} lines"
                )
                return df

        raise FileProcessingError(
            f"No extractable content found in PDF '{filename}'."
        )

    except FileProcessingError:
        raise
    except Exception as e:
        raise FileProcessingError(
            f"Failed to process PDF '{filename}': {e}"
        )


# ── XLSX / XLS ───────────────────────────────────────────────────────

def process_excel(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """
    Parse an Excel file (.xlsx / .xls) into a DataFrame.
    If the workbook has multiple sheets, all sheets with data are combined.
    """
    try:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        engine = "xlrd" if ext == "xls" else "openpyxl"

        try:
            all_sheets = pd.read_excel(
                io.BytesIO(file_bytes),
                sheet_name=None,  # read all sheets
                engine=engine,
            )
        except Exception:
            # Fallback: try the other engine
            fallback_engine = "openpyxl" if engine == "xlrd" else "xlrd"
            try:
                all_sheets = pd.read_excel(
                    io.BytesIO(file_bytes),
                    sheet_name=None,
                    engine=fallback_engine,
                )
            except Exception as e2:
                raise FileProcessingError(f"Cannot read Excel file '{filename}': {e2}")

        frames = []
        for sheet_name, df in all_sheets.items():
            if df.empty:
                continue
            # Clean column names
            df.columns = _dedup_columns([_clean_col(c) for c in df.columns])
            # Drop rows where all values are NaN
            df.dropna(how="all", inplace=True)
            if not df.empty:
                frames.append(df)

        if not frames:
            raise FileProcessingError(f"Excel file {filename} has no data.")

        combined = pd.concat(frames, ignore_index=True)
        combined = _coerce_types(combined)

        logger.info(
            f"Excel processed: {filename} — {len(combined)} rows, "
            f"{len(combined.columns)} columns from {len(frames)} sheet(s)"
        )
        return combined

    except FileProcessingError:
        raise
    except Exception as e:
        raise FileProcessingError(f"Failed to parse Excel '{filename}': {e}")


# ── DOCX ─────────────────────────────────────────────────────────────

def process_docx(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """
    Extract tables and text from a DOCX file.
    Strategy:
      1. If tables exist, extract them as DataFrames
      2. Otherwise, extract paragraphs as a single-column dataset
    """
    try:
        from docx import Document

        doc = Document(io.BytesIO(file_bytes))
        frames = []

        # Extract tables
        for table in doc.tables:
            if len(table.rows) < 2:
                continue

            headers = _dedup_columns([
                _clean_col(cell.text) or f"col_{i}"
                for i, cell in enumerate(table.rows[0].cells)
            ])

            rows = []
            for row in table.rows[1:]:
                rows.append([cell.text.strip() for cell in row.cells])

            df = pd.DataFrame(rows, columns=headers)
            df = _coerce_types(df)
            frames.append(df)

        if frames:
            combined = pd.concat(frames, ignore_index=True)
            logger.info(f"DOCX tables extracted: {filename} — {len(combined)} rows")
            return combined

        # Fallback: paragraphs as text content
        paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        if paragraphs:
            df = pd.DataFrame({"content": paragraphs})
            logger.info(f"DOCX text extracted: {filename} — {len(df)} paragraphs")
            return df

        raise FileProcessingError(f"No extractable content found in DOCX '{filename}'.")

    except FileProcessingError:
        raise
    except Exception as e:
        raise FileProcessingError(f"Failed to process DOCX '{filename}': {e}")


# ── Router ───────────────────────────────────────────────────────────

def process_file(file_bytes: bytes, filename: str, file_type: str) -> pd.DataFrame:
    """Route to the appropriate processor based on file type."""
    processors = {
        "csv": process_csv,
        "pdf": process_pdf,
        "xlsx": process_excel,
        "xls": process_excel,
        "docx": process_docx,
    }
    processor = processors.get(file_type)
    if not processor:
        raise FileProcessingError(f"Unsupported file type: {file_type}")
    return processor(file_bytes, filename)
