"""Interactively extract one unused DSA question from the prepared TACO dataset."""

from __future__ import annotations

import argparse
import ast
import hashlib
import itertools
import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from datasets import Dataset, load_from_disk


DATASET_PATH = Path("taco_candidates")
USED_QUESTIONS_PATH = Path("used_questions.json")
EXTRACTED_DIRECTORY = Path("extracted")
TRAIN_SPLIT = "train"
NUMBER_PATTERN = re.compile(r"[-+]?\d*\.?\d+")


def parse_arguments() -> argparse.Namespace:
    """Parse optional path overrides for running the extraction CLI.

    Returns:
        Parsed command-line arguments with dataset, tracker, and output paths.
    """
    parser = argparse.ArgumentParser(
        description="Select one unused question from the prepared TACO candidates dataset."
    )
    parser.add_argument("--dataset-path", type=Path, default=DATASET_PATH)
    parser.add_argument("--used-questions-path", type=Path, default=USED_QUESTIONS_PATH)
    parser.add_argument("--output-directory", type=Path, default=EXTRACTED_DIRECTORY)
    return parser.parse_args()


def load_train_dataset(dataset_path: Path) -> Dataset:
    """Load the prepared train split without applying any additional base filters.

    Args:
        dataset_path: Directory created previously with ``save_to_disk``.

    Returns:
        The prepared train split.

    Raises:
        FileNotFoundError: If the saved dataset directory is absent.
        KeyError: If the saved dataset does not contain a train split.
    """
    if not dataset_path.exists():
        raise FileNotFoundError(f"Prepared dataset not found: {dataset_path}")

    dataset_dict = load_from_disk(str(dataset_path))
    if TRAIN_SPLIT not in dataset_dict:
        raise KeyError(f"Prepared dataset has no '{TRAIN_SPLIT}' split.")
    return dataset_dict[TRAIN_SPLIT]


def load_used_questions(used_questions_path: Path) -> list[str]:
    """Load the selected-question URL tracker, defaulting to an empty tracker.

    Args:
        used_questions_path: JSON file containing a list of question URL strings.

    Returns:
        The tracked URL strings, or an empty list when the file does not exist.

    Raises:
        ValueError: If an existing tracker is not a JSON list of strings.
    """
    if not used_questions_path.exists():
        return []

    with used_questions_path.open("r", encoding="utf-8") as tracker_file:
        used_questions = json.load(tracker_file)
    if not isinstance(used_questions, list) or not all(
        isinstance(question_url, str) for question_url in used_questions
    ):
        raise ValueError(
            f"{used_questions_path} must contain a JSON list of question URL strings."
        )
    return used_questions


def prompt_tags() -> list[str]:
    """Prompt until the user enters one or more comma-separated tags.

    Returns:
        Entered tags in their original priority order, with surrounding whitespace removed.
    """
    while True:
        entered_tags = input("Tags (comma-separated, highest priority first): ")
        tags = [tag.strip() for tag in entered_tags.split(",") if tag.strip()]
        if tags:
            return tags
        print("Enter at least one non-empty tag.")


def prompt_difficulty(valid_difficulties: set[str]) -> str:
    """Prompt until the user enters a difficulty that exists in the dataset.

    Args:
        valid_difficulties: Exact difficulty values present in the loaded dataset.

    Returns:
        The validated difficulty string.
    """
    while True:
        difficulty = input("Difficulty: ").strip()
        if difficulty in valid_difficulties:
            return difficulty
        choices = ", ".join(sorted(valid_difficulties))
        print(f"Invalid difficulty. Choose one of: {choices}")


def prompt_relaxation_floor(tag_count: int) -> int:
    """Prompt for a valid minimum number of tags required after relaxation.

    Args:
        tag_count: Number of requested tags, which establishes the allowed range.

    Returns:
        A floor from one through ``tag_count`` inclusive.
    """
    if tag_count == 1:
        return 1

    while True:
        entered_floor = input(f"Relaxation floor (1-{tag_count} tags must match): ").strip()
        try:
            floor = int(entered_floor)
        except ValueError:
            print("Relaxation floor must be an integer.")
            continue
        if 1 <= floor <= tag_count:
            return floor
        print(f"Relaxation floor must be between 1 and {tag_count}.")


def parse_raw_tags(row: dict[str, Any]) -> list[str]:
    """Parse a row's serialized raw tag list.

    Args:
        row: Dataset row containing a Python-repr ``raw_tags`` field.

    Returns:
        The row's raw tags as a list of strings.

    Raises:
        ValueError: If the serialized tag field is malformed.
    """
    try:
        tags = ast.literal_eval(row["raw_tags"])
    except (KeyError, TypeError, ValueError, SyntaxError) as error:
        raise ValueError(f"Could not parse raw_tags for {row.get('url', '<unknown>')}") from error
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise ValueError(f"raw_tags is not a list of strings for {row.get('url', '<unknown>')}")
    return tags


def build_unused_pool(
    dataset: Dataset, difficulty: str, used_questions: Iterable[str]
) -> list[dict[str, Any]]:
    """Build the pool constrained only by exact difficulty and prior selection.

    Args:
        dataset: Prepared train split.
        difficulty: Exact requested difficulty.
        used_questions: URLs already selected by earlier extractions.

    Returns:
        Dataset rows that match the difficulty and are not already used.
    """
    used_urls = set(used_questions)
    return [
        dict(row)
        for row in dataset
        if row["difficulty"] == difficulty and row["url"] not in used_urls
    ]


def find_priority_tag_matches(
    unused_pool: Sequence[dict[str, Any]], tags: Sequence[str], relaxation_floor: int
) -> tuple[list[dict[str, Any]], tuple[str, ...]] | None:
    """Find the first non-empty priority-preserving relaxed tag match.

    Args:
        unused_pool: Rows already restricted by exact difficulty and unused URL.
        tags: Requested tags ordered from highest to lowest priority.
        relaxation_floor: Minimum number of requested tags which must still match.

    Returns:
        Matching rows and the successful tag combination, or ``None`` if no match exists.
    """
    parsed_tags = [(row, set(parse_raw_tags(row))) for row in unused_pool]

    for size in range(len(tags), relaxation_floor - 1, -1):
        # itertools.combinations emits combinations in input order. Consequently,
        # higher-priority tags participate in earlier relaxed attempts.
        for combination in itertools.combinations(tags, size):
            required_tags = set(combination)
            matches = [row for row, row_tags in parsed_tags if required_tags.issubset(row_tags)]
            if matches:
                return matches, combination
    return None


def choose_question(matches: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Uniformly choose one row from a non-empty matched candidate list.

    Args:
        matches: Rows satisfying the successful tag combination.

    Returns:
        One randomly selected dataset row.

    Raises:
        ValueError: If called with no matches.
    """
    if not matches:
        raise ValueError("Cannot choose a question from an empty match list.")
    return random.choice(matches)


def parse_numeric_limit(value: str, field_name: str) -> float:
    """Extract the leading numeric quantity from a human-readable resource limit.

    Args:
        value: Limit text such as ``'1.0 seconds'``.
        field_name: Field name used in an informative validation error.

    Returns:
        The numeric portion converted to a float.

    Raises:
        ValueError: If the value contains no numeric quantity.
    """
    match = NUMBER_PATTERN.search(str(value))
    if match is None:
        raise ValueError(f"Could not parse numeric {field_name}: {value!r}")
    return float(match.group())


def parse_python_solutions(row: dict[str, Any]) -> list[str]:
    """Return every syntactically Python-parseable solution from a row.

    Args:
        row: Dataset row with a JSON-encoded ``solutions`` list.

    Returns:
        Python-parseable solution source strings, with no correctness filtering.

    Raises:
        ValueError: If the solution field is malformed.
    """
    try:
        solutions = json.loads(row["solutions"])
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not parse solutions for {row.get('url', '<unknown>')}") from error
    if not isinstance(solutions, list):
        raise ValueError(f"solutions is not a list for {row.get('url', '<unknown>')}")

    python_solutions = []
    for solution in solutions:
        if not isinstance(solution, str):
            continue
        try:
            ast.parse(solution)
        except (SyntaxError, ValueError):
            continue
        python_solutions.append(solution)
    return python_solutions


def build_test_cases(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Build ordered input/output test-case pairs from a serialized row field.

    Args:
        row: Dataset row containing a Python-repr ``input_output`` dictionary.

    Returns:
        A flat list of paired test case dictionaries.

    Raises:
        ValueError: If the input/output field is malformed.
    """
    try:
        input_output = ast.literal_eval(row["input_output"])
    except (KeyError, TypeError, ValueError, SyntaxError) as error:
        raise ValueError(f"Could not parse input_output for {row.get('url', '<unknown>')}") from error
    if not isinstance(input_output, dict):
        raise ValueError(f"input_output is not a dictionary for {row.get('url', '<unknown>')}")

    inputs = input_output.get("inputs")
    outputs = input_output.get("outputs")
    if not isinstance(inputs, list) or not isinstance(outputs, list):
        raise ValueError(f"input_output lacks input/output lists for {row.get('url', '<unknown>')}")
    return [{"input": input_value, "output": output_value} for input_value, output_value in zip(inputs, outputs)]


def build_extraction_payload(
    row: dict[str, Any], requested_tags: Sequence[str], matched_tags: Sequence[str], relaxation_floor: int
) -> dict[str, Any]:
    """Assemble the exact JSON payload consumed by later pipeline stages.

    Args:
        row: Selected dataset row.
        requested_tags: Tags supplied by the user in priority order.
        matched_tags: The priority-preserving tag combination that succeeded.
        relaxation_floor: The user-selected minimum number of matching tags.

    Returns:
        A JSON-serializable extraction payload.
    """
    return {
        "question_id": row["url"],
        "source": row["source"],
        "title": row["name"],
        "question_text": row["question"],
        "difficulty": row["difficulty"],
        "requested_tags": list(requested_tags),
        "matched_tags": list(matched_tags),
        "relaxation_floor": relaxation_floor,
        "all_tags": parse_raw_tags(row),
        "time_limit_seconds": parse_numeric_limit(row["time_limit"], "time limit"),
        "memory_limit_mb": parse_numeric_limit(row["memory_limit"], "memory limit"),
        "candidate_solutions": parse_python_solutions(row),
        "test_cases": build_test_cases(row),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }


def output_path_for_question(output_directory: Path, question_url: str) -> Path:
    """Produce a deterministic filesystem-safe JSON filename from a question URL.

    Args:
        output_directory: Directory in which extraction files are stored.
        question_url: Unique dataset URL for the selected question.

    Returns:
        Destination path based on a stable short SHA-256 digest.
    """
    identifier = hashlib.sha256(question_url.encode("utf-8")).hexdigest()[:16]
    return output_directory / f"question_{identifier}.json"


def write_json(path: Path, payload: Any) -> None:
    """Write a JSON value with UTF-8 encoding and readable indentation.

    Args:
        path: Destination JSON file.
        payload: JSON-serializable value to write.

    Returns:
        None.
    """
    with path.open("w", encoding="utf-8") as json_file:
        json.dump(payload, json_file, ensure_ascii=False, indent=2)
        json_file.write("\n")


def write_extraction(output_directory: Path, payload: dict[str, Any]) -> Path:
    """Create the extraction directory and persist one extraction payload.

    Args:
        output_directory: Directory for generated question JSON files.
        payload: Completed extraction payload.

    Returns:
        The path of the newly written extraction JSON file.
    """
    output_directory.mkdir(parents=True, exist_ok=True)
    output_path = output_path_for_question(output_directory, payload["question_id"])
    write_json(output_path, payload)
    return output_path


def save_used_questions(used_questions_path: Path, used_questions: list[str]) -> None:
    """Persist the selected-question URL tracker after successful extraction.

    Args:
        used_questions_path: JSON tracker destination.
        used_questions: Complete list of selected question URLs.

    Returns:
        None.
    """
    write_json(used_questions_path, used_questions)


def main() -> int:
    """Run the interactive extraction flow and return a process status code.

    Returns:
        Zero after extraction or a graceful zero-match exit.
    """
    arguments = parse_arguments()
    dataset = load_train_dataset(arguments.dataset_path)
    used_questions = load_used_questions(arguments.used_questions_path)

    tags = prompt_tags()
    valid_difficulties = set(dataset["difficulty"])
    difficulty = prompt_difficulty(valid_difficulties)
    relaxation_floor = prompt_relaxation_floor(len(tags))

    unused_pool = build_unused_pool(dataset, difficulty, used_questions)
    search_result = find_priority_tag_matches(unused_pool, tags, relaxation_floor)
    if search_result is None:
        print("No matching question found. No output or used-question tracker was changed.")
        return 0

    matches, matched_tags = search_result
    selected_row = choose_question(matches)
    payload = build_extraction_payload(selected_row, tags, matched_tags, relaxation_floor)
    output_path = write_extraction(arguments.output_directory, payload)

    # Update the tracker only after the output JSON has been written successfully.
    used_questions.append(selected_row["url"])
    save_used_questions(arguments.used_questions_path, used_questions)

    print("Selected question:", payload["title"])
    print("Difficulty:", payload["difficulty"])
    print("Requested tags:", ", ".join(payload["requested_tags"]))
    print("Matched tags:", ", ".join(payload["matched_tags"]))
    print("Output:", output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
