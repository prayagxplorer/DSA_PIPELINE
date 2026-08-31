"""Focused tests for the LangGraph retry and report-writing behaviour."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from enrichment.enrich import (
    DEFAULT_GROQ_MODEL,
    Enrichment,
    GROQ_BASE_URL,
    build_enrichment_graph,
    enrich_report,
    load_existing_enrichment,
    normalize_title,
    write_enriched_report,
)


QUESTION = {
    "question_id": "https://example.test/problems/42",
    "description": "Given n, calculate a result. 1 <= n <= 100.",
    "categories": ["math"],
}


class EnrichmentGraphTests(unittest.TestCase):
    def test_groq_strict_schema_has_only_required_closed_fields(self) -> None:
        schema = Enrichment.model_json_schema()

        self.assertEqual(GROQ_BASE_URL, "https://api.groq.com/openai/v1")
        self.assertEqual(DEFAULT_GROQ_MODEL, "openai/gpt-oss-20b")
        self.assertEqual(schema["required"], ["title", "constraints", "hints"])
        self.assertFalse(schema["additionalProperties"])

    def test_provider_failure_is_reported_once_without_a_validation_retry(self) -> None:
        calls = 0

        def denied_request(question, feedback):
            nonlocal calls
            calls += 1
            error = RuntimeError("API key is not permitted")
            error.status_code = 403
            raise error

        graph = build_enrichment_graph(denied_request)

        with self.assertRaisesRegex(RuntimeError, "HTTP 403.*not permitted"):
            enrich_report(graph, QUESTION, set())
        self.assertEqual(calls, 1)

    def test_duplicate_title_is_regenerated(self) -> None:
        responses = iter(
            [
                Enrichment(title="Shared Title", constraints=[], hints=["A", "B"]),
                Enrichment(title="Fresh Title", constraints=[], hints=["A", "B"]),
            ]
        )
        graph = build_enrichment_graph(lambda question, feedback: next(responses))

        enrichment, titles = enrich_report(graph, QUESTION, {normalize_title("Shared Title")})

        self.assertEqual(enrichment.title, "Fresh Title")
        self.assertIn(normalize_title("Fresh Title"), titles)

    def test_three_duplicate_titles_receive_a_hash_suffix(self) -> None:
        graph = build_enrichment_graph(
            lambda question, feedback: Enrichment(
                title="Shared Title", constraints=[], hints=["A", "B"]
            )
        )

        enrichment, _ = enrich_report(graph, QUESTION, {normalize_title("Shared Title")})

        suffix = hashlib.sha256(QUESTION["question_id"].encode("utf-8")).hexdigest()[:6]
        self.assertEqual(enrichment.title, f"Shared Title — {suffix}")

    def test_invalid_candidates_fail_after_three_attempts(self) -> None:
        graph = build_enrichment_graph(
            lambda question, feedback: {"title": "", "constraints": [], "hints": []}
        )

        with self.assertRaisesRegex(RuntimeError, "Invalid enrichment response"):
            enrich_report(graph, QUESTION, set())


class EnrichmentReportIoTests(unittest.TestCase):
    def test_written_report_preserves_original_fields_and_is_reloaded(self) -> None:
        report = {**QUESTION, "overall_pass": True, "title": None, "constraints": [], "hints": []}
        enrichment = Enrichment(
            title="Calculated Result", constraints=["1 <= n <= 100"], hints=["A", "B"]
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_directory = Path(temporary_directory)
            output_path = output_directory / "report.json"
            write_enriched_report(report, enrichment, output_path)
            titles, question_ids = load_existing_enrichment(output_directory)

            written = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertTrue(written["overall_pass"])
        self.assertEqual(written["title"], "Calculated Result")
        self.assertIn(normalize_title("Calculated Result"), titles)
        self.assertEqual(question_ids, {QUESTION["question_id"]})


if __name__ == "__main__":
    unittest.main()
