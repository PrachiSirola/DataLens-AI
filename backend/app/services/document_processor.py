"""
Document Processor — Phase 4 of DataLensAI.

Extracts full text from PDFs and DOCX files, chunks it into
retrieval-ready segments with page tracking.  Used for knowledge /
prose documents that cannot be queried with SQL.

Pipeline:
  1. extract_text_from_pdf / extract_text_from_docx → page-level text
  2. chunk_pages → overlapping TextChunk objects with page refs
  3. is_tabular_content → decide SQL vs. knowledge path
"""

import io
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ── Chunking parameters ──────────────────────────────────────────────
CHUNK_SIZE = 800       
CHUNK_OVERLAP = 200     
MIN_CHUNK_SIZE = 100    


@dataclass
class TextChunk:
    """A single retrieval unit with source page information."""
    content: str
    page_num: int
    chunk_index: int
    start_char: int
    end_char: int


# ── PDF text extraction ──────────────────────────────────────────────

def extract_text_from_pdf(file_bytes: bytes) -> list[dict]:
    """
    Extract text from every page of a PDF.

    Returns
    -------
    list[dict]
        ``[{"page": 1, "text": "..."}, ...]``
    """
    import pdfplumber

    pages: list[dict] = []
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for i, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ""
                if text.strip():
                    pages.append({"page": i, "text": text.strip()})
    except Exception as e:
        logger.error(f"pdfplumber text extraction failed: {e}")

    # Fallback: pypdf when pdfplumber yields nothing
    if not pages:
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(file_bytes))
            for i, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ""
                if text.strip():
                    pages.append({"page": i, "text": text.strip()})
        except Exception as e:
            logger.error(f"pypdf fallback failed: {e}")

    return pages


# ── DOCX text extraction ─────────────────────────────────────────────

def extract_text_from_docx(file_bytes: bytes) -> list[dict]:
    """
    Extract text from a DOCX file.

    DOCX has no native page numbers, so content is split into
    pseudo-pages of ~3 000 characters each.

    Returns
    -------
    list[dict]
        ``[{"page": 1, "text": "..."}, ...]``
    """
    try:
        from docx import Document
        doc = Document(io.BytesIO(file_bytes))
        paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        combined = "\n".join(paragraphs)
        if not combined.strip():
            return []

        page_size = 3000
        pseudo_pages: list[dict] = []
        for i in range(0, len(combined), page_size):
            segment = combined[i : i + page_size].strip()
            if segment:
                pseudo_pages.append({"page": len(pseudo_pages) + 1, "text": segment})

        return pseudo_pages if pseudo_pages else [{"page": 1, "text": combined}]
    except Exception as e:
        logger.error(f"DOCX text extraction failed: {e}")
        return []


# ── Chunking ─────────────────────────────────────────────────────────

def chunk_pages(
    pages: list[dict],
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[TextChunk]:
    """
    Split page texts into overlapping chunks suitable for retrieval.
    Each chunk records its source page number.
    """
    chunks: list[TextChunk] = []
    chunk_idx = 0

    for page_info in pages:
        page_num = page_info["page"]
        text = page_info["text"]

        # Short page → single chunk
        if len(text) <= chunk_size:
            if len(text) >= MIN_CHUNK_SIZE:
                chunks.append(TextChunk(
                    content=text,
                    page_num=page_num,
                    chunk_index=chunk_idx,
                    start_char=0,
                    end_char=len(text),
                ))
                chunk_idx += 1
            continue

        # Long page → sliding-window with sentence-boundary snapping
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))

            # Snap to a sentence boundary if we're mid-page
            if end < len(text):
                search_from = max(start, end - chunk_size // 5)
                last_period = text.rfind(". ", search_from, end)
                last_newline = text.rfind("\n", search_from, end)
                bp = max(last_period, last_newline)
                if bp > start + MIN_CHUNK_SIZE:
                    end = bp + 1

            chunk_text = text[start:end].strip()
            if len(chunk_text) >= MIN_CHUNK_SIZE:
                chunks.append(TextChunk(
                    content=chunk_text,
                    page_num=page_num,
                    chunk_index=chunk_idx,
                    start_char=start,
                    end_char=end,
                ))
                chunk_idx += 1

            start = end - overlap
            if start >= len(text):
                break

    return chunks


# ── Content-type detection ───────────────────────────────────────────

def is_tabular_content(df, file_type: str) -> bool:
    """
    Decide whether *df* represents genuine tabular data (→ SQL path)
    or poorly-extracted prose (→ knowledge / text retrieval path).

    CSV / Excel files are always treated as tabular.
    """
    if file_type in ("csv", "xlsx", "xls"):
        return True

    if df is None or df.empty:
        return False

    rows, cols = df.shape

    # Too few columns → not real tabular data
    if cols < 3:
        return False

    # High null density → prose PDF forced into a table
    total_cells = rows * cols
    if total_cells == 0:
        return False
    null_ratio = df.isnull().sum().sum() / total_cells
    if null_ratio > 0.5:
        return False

    # Count columns that carry meaningful data
    useful_cols = 0
    for col in df.columns:
        non_null = df[col].dropna()
        if len(non_null) > rows * 0.3 and non_null.nunique() >= 2:
            useful_cols += 1

    if useful_cols < cols * 0.5:
        return False

    return rows >= 5 and useful_cols >= 3


def get_text_content_length(pages: list[dict]) -> int:
    """Total character count across all extracted pages."""
    return sum(len(p["text"]) for p in pages)
