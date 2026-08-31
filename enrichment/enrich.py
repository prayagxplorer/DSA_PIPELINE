"""Enrich a temporary sandbox report with a Groq title, constraints, and hints.

The input path is deliberately fixed while the sandbox-to-enrichment handoff is
being set up. The LangGraph flow and report I/O remain separate so this can be
changed to directory discovery later without changing the LLM logic.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
# Temporary handoff: use directory discovery here once sandbox reports have a
# permanent home. Until then, this is the user-supplied reference report.
REPORT_PATH = Path("/home/daemon_bash/Downloads/report.json")
ENRICHED_DIRECTORY = REPOSITORY_ROOT / "enriched"
ENVIRONMENT_PATH = REPOSITORY_ROOT / ".env"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"
MAX_VALIDATION_ATTEMPTS = 3
MAX_UNIQUENESS_ATTEMPTS = 3

LOGGER = logging.getLogger(__name__)
TitleSet = set[str]
GenerationFunction = Callable[[dict[str, Any], str | None], Any]


class Enrichment(BaseModel):
    """The only fields Grok is allowed to add or replace in a sandbox report."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    constraints: list[str]
    hints: list[str] = Field(min_length=2, max_length=4)

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value

    @field_validator("constraints", "hints")
    @classmethod
    def strings_must_not_be_blank(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("constraints and hints must not contain blank strings")
        return cleaned


class EnrichmentState(TypedDict, total=False):
    """State passed between the LangGraph generation, validation, and title nodes."""

    question: dict[str, Any]
    used_titles: TitleSet
    candidate: dict[str, Any] | None
    feedback: str | None
    validation_failures: int
    uniqueness_failures: int
    status: Literal[
        "generated",
        "retry_validation",
        "validated",
        "retry_title",
        "use_suffix",
        "success",
        "provider_failed",
        "failed",
    ]
    error: str | None
    generation_error: str | None


def normalize_title(title: str) -> str:
    """Return a case-insensitive comparison key that ignores extra whitespace."""

    return re.sub(r"\s+", " ", title).strip().casefold()


def get_question_text(question: dict[str, Any]) -> str:
    """Read the question text from either supported sandbox-report field name."""

    for field_name in ("description", "question_text"):
        value = question.get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError("sandbox report has no non-empty description or question_text")


def get_question_tags(question: dict[str, Any]) -> list[str]:
    """Read tags from the sandbox report's supported field names."""

    for field_name in ("matched_tags", "requested_tags", "categories"):
        value = question.get(field_name)
        if isinstance(value, list):
            return [tag.strip() for tag in value if isinstance(tag, str) and tag.strip()]
    return []


def build_messages(question: dict[str, Any], feedback: str | None) -> list[dict[str, str]]:
    """Build the provider prompt without coupling it to graph control flow."""

    tags = get_question_tags(question)
    tag_text = ", ".join(tags) if tags else "No tags were supplied."
    correction = f"\n\nCorrection for this attempt: {feedback}" if feedback else ""
    system_prompt = (
        "You enrich competitive-programming problems. Return only the requested structured "
        "data. Create a concise, descriptive title for this exact problem. Extract constraints "
        "only from explicit input bounds stated in the problem text; never infer, estimate, or "
        "invent bounds. Return an empty constraints list when no bounds are stated. Give 2 to 4 "
        "short hints that guide the solver without revealing a full solution. Ground hints in the "
        "provided tags when they are available."
    )
    user_prompt = f"Problem tags: {tag_text}\n\nProblem text:\n{get_question_text(question)}{correction}"
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def create_groq_generator(api_key: str, model: str | None = None) -> GenerationFunction:
    """Create a Pydantic-schema-backed Groq caller using Groq's compatible API."""

    client = OpenAI(api_key=api_key, base_url=GROQ_BASE_URL)
    active_model = model or os.getenv("GROQ_MODEL", DEFAULT_GROQ_MODEL)
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "problem_enrichment",
            "schema": Enrichment.model_json_schema(),
            "strict": True,
        },
    }

    def generate(question: dict[str, Any], feedback: str | None) -> Enrichment:
        response = client.chat.completions.create(
            model=active_model,
            messages=build_messages(question, feedback),
            response_format=response_format,
        )
        if not response.choices or not response.choices[0].message.content:
            raise StructuredResponseError("Groq returned no structured response content")
        return Enrichment.model_validate_json(response.choices[0].message.content)

    return generate


class StructuredResponseError(ValueError):
    """Raised when Groq responds without data that can enter Pydantic validation."""


def format_provider_error(error: Exception) -> str:
    """Preserve a provider error's status and body instead of replacing it with None."""

    status_code = getattr(error, "status_code", None)
    detail = str(error) or repr(error)
    status = f" (HTTP {status_code})" if status_code is not None else ""
    return f"{type(error).__name__}{status}: {detail}"


def generate_node(generator: GenerationFunction) -> Callable[[EnrichmentState], dict[str, Any]]:
    """Create the graph node that requests a fresh candidate from Grok."""

    def generate(state: EnrichmentState) -> dict[str, Any]:
        try:
            candidate = generator(state["question"], state.get("feedback"))
            if isinstance(candidate, Enrichment):
                candidate = candidate.model_dump()
            return {
                "candidate": candidate,
                "status": "generated",
                "error": None,
                "generation_error": None,
            }
        except (ValidationError, StructuredResponseError) as error:
            # This is malformed model output, so it follows the documented
            # three-attempt validation retry loop.
            return {
                "candidate": None,
                "status": "generated",
                "error": None,
                "generation_error": f"Malformed Groq response: {error}",
            }
        except (APIConnectionError, APITimeoutError, RateLimitError) as error:
            # These failures may be transient and get the same bounded retry.
            return {
                "candidate": None,
                "status": "generated",
                "error": None,
                "generation_error": f"Transient Groq request failure: {format_provider_error(error)}",
            }
        except APIStatusError as error:
            # Authentication, permission, model-access, and malformed-request
            # errors will not succeed on a retry, so avoid wasting API calls.
            return {
                "candidate": None,
                "status": "provider_failed",
                "error": f"Groq request failed: {format_provider_error(error)}",
                "generation_error": None,
            }
        except Exception as error:
            return {
                "candidate": None,
                "status": "provider_failed",
                "error": f"Groq request failed before a response: {format_provider_error(error)}",
                "generation_error": None,
            }

    return generate


def validate_node(state: EnrichmentState) -> dict[str, Any]:
    """Validate the Pydantic response and request another generation when needed."""

    if state.get("status") == "provider_failed":
        return {"status": "failed", "error": state.get("error")}

    try:
        candidate = Enrichment.model_validate(state.get("candidate"))
    except (ValidationError, TypeError) as error:
        failures = state.get("validation_failures", 0) + 1
        message = state.get("generation_error") or f"Invalid enrichment response: {error}"
        if failures >= MAX_VALIDATION_ATTEMPTS:
            return {
                "validation_failures": failures,
                "status": "failed",
                "error": message,
            }
        return {
            "candidate": None,
            "validation_failures": failures,
            "feedback": message,
            "status": "retry_validation",
            "error": message,
        }

    return {
        "candidate": candidate.model_dump(),
        "feedback": None,
        "status": "validated",
        "error": None,
        "generation_error": None,
    }


def validation_route(state: EnrichmentState) -> Literal["generate", "uniqueness_check", "failed"]:
    """Send failed validation back to Grok or move a valid candidate to title checking."""

    if state.get("status") == "failed":
        return "failed"
    if state.get("status") == "retry_validation":
        return "generate"
    return "uniqueness_check"


def uniqueness_check_node(state: EnrichmentState) -> dict[str, Any]:
    """Accept a unique title or request a replacement title from the generation node."""

    candidate = Enrichment.model_validate(state["candidate"])
    used_titles = set(state["used_titles"])
    if normalize_title(candidate.title) not in used_titles:
        used_titles.add(normalize_title(candidate.title))
        return {"used_titles": used_titles, "status": "success", "error": None}

    failures = state.get("uniqueness_failures", 0) + 1
    message = "That title is already in use. Generate a distinctly worded title for this problem."
    if failures >= MAX_UNIQUENESS_ATTEMPTS:
        return {
            "uniqueness_failures": failures,
            "feedback": message,
            "status": "use_suffix",
            "error": None,
        }
    return {
        "uniqueness_failures": failures,
        "feedback": message,
        "status": "retry_title",
        "error": None,
    }


def uniqueness_route(state: EnrichmentState) -> Literal["generate", "suffix_title", "done"]:
    """Choose the next graph edge after checking title uniqueness."""

    status = state.get("status")
    if status == "retry_title":
        return "generate"
    if status == "use_suffix":
        return "suffix_title"
    return "done"


def suffix_title_node(state: EnrichmentState) -> dict[str, Any]:
    """Give an exhausted duplicate title a deterministic, collision-safe suffix."""

    candidate = Enrichment.model_validate(state["candidate"])
    question_id = str(state["question"].get("question_id", ""))
    digest = hashlib.sha256(question_id.encode("utf-8")).hexdigest()[:6]
    base_title = f"{candidate.title} — {digest}"
    used_titles = set(state["used_titles"])
    title = base_title
    collision_number = 2
    while normalize_title(title) in used_titles:
        title = f"{base_title}-{collision_number}"
        collision_number += 1
    candidate.title = title
    used_titles.add(normalize_title(title))
    return {
        "candidate": candidate.model_dump(),
        "used_titles": used_titles,
        "status": "success",
        "error": None,
    }


def build_enrichment_graph(generator: GenerationFunction):
    """Build the LangGraph flow independently of report loading and file output."""

    graph = StateGraph(EnrichmentState)
    graph.add_node("generate", generate_node(generator))
    graph.add_node("validate", validate_node)
    graph.add_node("uniqueness_check", uniqueness_check_node)
    graph.add_node("suffix_title", suffix_title_node)
    graph.add_edge(START, "generate")
    graph.add_edge("generate", "validate")
    graph.add_conditional_edges(
        "validate",
        validation_route,
        {"generate": "generate", "uniqueness_check": "uniqueness_check", "failed": END},
    )
    graph.add_conditional_edges(
        "uniqueness_check",
        uniqueness_route,
        {"generate": "generate", "suffix_title": "suffix_title", "done": END},
    )
    graph.add_edge("suffix_title", END)
    return graph.compile()


def enrich_report(
    graph: Any, report: dict[str, Any], used_titles: TitleSet
) -> tuple[Enrichment, TitleSet]:
    """Run one sandbox report through the compiled graph or raise its final error."""

    result = graph.invoke(
        {
            "question": report,
            "used_titles": set(used_titles),
            "candidate": None,
            "feedback": None,
            "validation_failures": 0,
            "uniqueness_failures": 0,
            "error": None,
            "generation_error": None,
        }
    )
    if result.get("status") == "failed":
        raise RuntimeError(result.get("error") or "enrichment failed after retries")
    return Enrichment.model_validate(result["candidate"]), set(result["used_titles"])


def read_report(path: Path) -> dict[str, Any]:
    """Load one sandbox JSON report and reject non-object payloads early."""

    with path.open("r", encoding="utf-8") as report_file:
        report = json.load(report_file)
    if not isinstance(report, dict):
        raise ValueError(f"{path} must contain one sandbox-report JSON object")
    return report


def output_path_for_report(report_path: Path, output_directory: Path) -> Path:
    """Keep the sandbox report's filename as the enriched report identifier."""

    return output_directory / report_path.name


def load_existing_enrichment(output_directory: Path) -> tuple[TitleSet, set[str]]:
    """Load prior titles and question IDs so reruns are idempotent and title-safe."""

    titles: TitleSet = set()
    question_ids: set[str] = set()
    if not output_directory.exists():
        return titles, question_ids

    for path in output_directory.glob("*.json"):
        try:
            report = read_report(path)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            LOGGER.warning("Ignoring malformed enriched report %s: %s", path, error)
            continue
        title = report.get("title")
        if isinstance(title, str) and title.strip():
            titles.add(normalize_title(title))
        question_id = report.get("question_id")
        if isinstance(question_id, str) and question_id.strip():
            question_ids.add(question_id)
    return titles, question_ids


def write_enriched_report(
    report: dict[str, Any], enrichment: Enrichment, output_path: Path
) -> None:
    """Write a full copied report with only its enrichment fields replaced."""

    enriched_report = dict(report)
    enriched_report.update(enrichment.model_dump())
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_file:
        json.dump(enriched_report, output_file, indent=2, ensure_ascii=False)
        output_file.write("\n")


def print_summary(enriched_count: int, skipped_count: int, failures: list[tuple[str, str]]) -> None:
    """Print the requested per-run outcome counts and failure reasons."""

    print(
        "Summary: "
        f"enriched={enriched_count}, skipped={skipped_count}, failed={len(failures)}"
    )
    for question_id, reason in failures:
        print(f"  FAILED {question_id}: {reason}")


def main() -> int:
    """Run the temporary single-report enrichment stage and print a batch summary."""

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    load_dotenv(ENVIRONMENT_PATH)

    enriched_count = 0
    skipped_count = 0
    failures: list[tuple[str, str]] = []
    used_titles, enriched_question_ids = load_existing_enrichment(ENRICHED_DIRECTORY)

    try:
        report = read_report(REPORT_PATH)
        question_id = report.get("question_id")
        if not isinstance(question_id, str) or not question_id.strip():
            raise ValueError("sandbox report is missing a non-empty question_id")
        get_question_text(report)
        output_path = output_path_for_report(REPORT_PATH, ENRICHED_DIRECTORY)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        failures.append((str(REPORT_PATH), str(error)))
        print_summary(enriched_count, skipped_count, failures)
        return 1

    if output_path.exists() or question_id in enriched_question_ids:
        LOGGER.info("Skipping %s: an enriched report already exists", question_id)
        skipped_count += 1
        print_summary(enriched_count, skipped_count, failures)
        return 0

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        failures.append((question_id, "GROQ_API_KEY is not set in .env or the environment"))
        print_summary(enriched_count, skipped_count, failures)
        return 1

    graph = build_enrichment_graph(create_groq_generator(api_key))
    try:
        enrichment, used_titles = enrich_report(graph, report, used_titles)
        write_enriched_report(report, enrichment, output_path)
        enriched_question_ids.add(question_id)
        enriched_count += 1
        LOGGER.info("Enriched %s -> %s", question_id, output_path)
    except Exception as error:
        LOGGER.exception("Failed to enrich %s", question_id)
        failures.append((question_id, str(error)))

    print_summary(enriched_count, skipped_count, failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
