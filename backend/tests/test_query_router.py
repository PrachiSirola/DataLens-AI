"""Tests for query_router.py — query classification and status semantics."""

import pytest

from app.services.query_router import (
    determine_query_type,
    build_status_enrichment,
    format_passages_for_llm,
    build_knowledge_prompt,
    _find_status_columns,
    _group_values_by_semantics,
)


# ── determine_query_type tests ───────────────────────────────────────

class TestDetermineQueryType:
    def test_sql_only_datasets(self):
        """Only tabular datasets → always SQL."""
        assert determine_query_type("what is a denial of service", True, False) == "sql"

    def test_knowledge_only_datasets(self):
        """Only knowledge datasets → always knowledge."""
        assert determine_query_type("how many rows", False, True) == "knowledge"

    def test_no_datasets(self):
        """No datasets → SQL (will produce helpful error)."""
        assert determine_query_type("hello", False, False) == "sql"

    def test_data_signals_route_to_sql(self):
        """Clear data questions → SQL when both available."""
        assert determine_query_type("how many employees are present today", True, True) == "sql"
        assert determine_query_type("show all rows", True, True) == "sql"
        assert determine_query_type("total salary by department", True, True) == "sql"

    def test_knowledge_signals_route_to_knowledge(self):
        """Clear knowledge questions → knowledge when both available."""
        assert determine_query_type("what is a denial of service attack", True, True) == "knowledge"
        assert determine_query_type("explain cross-site scripting vulnerability", True, True) == "knowledge"
        assert determine_query_type("describe the mitigation steps for SQL injection", True, True) == "knowledge"

    def test_mixed_signals_route_to_hybrid(self):
        """Mixed signals → hybrid."""
        # Contains both "how many" (data) and "vulnerability" (knowledge)
        assert determine_query_type("how many vulnerability types exist", True, True) == "hybrid"

    def test_interrogative_defaults_to_knowledge(self):
        """Interrogative without specific signals → knowledge."""
        assert determine_query_type("what are the key ideas in this document", True, True) == "knowledge"

    def test_ambiguous_defaults_to_sql(self):
        """Ambiguous non-interrogative → SQL."""
        assert determine_query_type("show data", True, True) == "sql"


# ── build_status_enrichment tests ────────────────────────────────────

class TestBuildStatusEnrichment:
    def test_no_status_columns(self):
        """No status-like columns → empty string."""
        col_values = {"name": ["Alice", "Bob"], "age": ["25", "30"]}
        assert build_status_enrichment(col_values) == ""

    def test_detects_status_column(self):
        """Columns with status values are detected and grouped."""
        col_values = {
            "status": ["present", "late", "absent", "day_off", "on_leave", "pending"],
            "name": ["Alice", "Bob"],
        }
        result = build_status_enrichment(col_values)
        assert "status" in result.lower()
        assert "AT WORK" in result
        assert "present" in result.lower()
        assert "late" in result.lower()

    def test_status_column_by_name(self):
        """Column named 'attendance_status' detected even with few matches."""
        col_values = {
            "attendance_status": ["present", "absent", "remote"],
            "department": ["Engineering", "HR"],
        }
        result = build_status_enrichment(col_values)
        assert "attendance_status" in result

    def test_important_instruction_included(self):
        """The enrichment includes the IMPORTANT instruction about including late."""
        col_values = {
            "status": ["present", "late", "absent"],
        }
        result = build_status_enrichment(col_values)
        assert "IMPORTANT" in result


# ── _find_status_columns tests ───────────────────────────────────────

class TestFindStatusColumns:
    def test_finds_by_values(self):
        """Two+ matching values → detected."""
        col_values = {"state": ["present", "absent", "unknown"]}
        result = _find_status_columns(col_values)
        assert "state" in result

    def test_finds_by_column_name(self):
        """Status-named column with one matching value → detected."""
        col_values = {"attendance_status": ["present", "other"]}
        result = _find_status_columns(col_values)
        assert "attendance_status" in result

    def test_ignores_non_status(self):
        """Normal columns without status values → not detected."""
        col_values = {"city": ["Delhi", "Mumbai", "Pune"]}
        result = _find_status_columns(col_values)
        assert len(result) == 0


# ── format_passages_for_llm tests ────────────────────────────────────

class TestFormatPassages:
    def test_empty_chunks(self):
        assert "No relevant passages" in format_passages_for_llm([])

    def test_formats_with_page_numbers(self):
        chunks = [
            {"page_num": 5, "content": "Denial of service is an attack."},
            {"page_num": 12, "content": "Mitigation includes rate limiting."},
        ]
        result = format_passages_for_llm(chunks)
        assert "Page 5" in result
        assert "Page 12" in result
        assert "Denial of service" in result
        assert "Mitigation" in result


# ── build_knowledge_prompt tests ─────────────────────────────────────

class TestBuildKnowledgePrompt:
    def test_structure(self):
        chunks = [{"page_num": 1, "content": "Some content here."}]
        messages = build_knowledge_prompt("What is X?", chunks)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "What is X?" in messages[1]["content"]
        assert "PASSAGES" in messages[0]["content"]
        assert "Some content here" in messages[0]["content"]

    def test_grounding_instructions(self):
        """System prompt includes grounding instructions."""
        messages = build_knowledge_prompt("test", [{"page_num": 1, "content": "data"}])
        system = messages[0]["content"]
        assert "ONLY based on" in system
        assert "[Page X]" in system
        assert "Do NOT make up" in system
