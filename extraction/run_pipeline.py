import json
import subprocess
import sys
from pathlib import Path

from db_schema import ProblemInsertRow, Difficulty
from pydantic import ValidationError

EXTRACTION_DIR = Path(__file__).resolve().parent
PROJECT_DIR = EXTRACTION_DIR.parent
SANDBOX_DIR = PROJECT_DIR / "sandbox"
EXTRACT_SCRIPT = EXTRACTION_DIR / "extract.py"
DATASET_PATH = PROJECT_DIR / "taco_candidates"
USED_QUESTIONS_PATH = EXTRACTION_DIR / "used_questions.json"
EXTRACTED_DIR = PROJECT_DIR / "extracted"

sys.path.insert(0, str(SANDBOX_DIR))
from run_sandbox import evaluate_question

extracted_dir = EXTRACTED_DIR
before = set(extracted_dir.glob("*.json")) if extracted_dir.exists() else set()

result = subprocess.run(
    [
        sys.executable,
        str(EXTRACT_SCRIPT),
        "--dataset-path",
        str(DATASET_PATH),
        "--used-questions-path",
        str(USED_QUESTIONS_PATH),
        "--output-directory",
        str(EXTRACTED_DIR),
    ],
    cwd=PROJECT_DIR,
)
if result.returncode != 0:
    sys.exit(result.returncode)

new_files = set(extracted_dir.glob("*.json")) - before
if not new_files:
    print("No question extracted -- nothing to test.")
    sys.exit(0)

output_path = new_files.pop()
with open(output_path) as f:
    question = json.load(f)

report = evaluate_question(question, seed=42)

report_path = output_path.parent / f"{output_path.stem}_report.json"
with open(report_path, "w") as f:
    f.write(report.model_dump_json(indent=2))


print(f"\nFull report written to: {report_path}", file=sys.stderr)

if report.overall_pass:
    try:
        round_id = int(input("Round ID for this problem: "))
    except ValueError:
        print("Round ID must be an integer.")
        sys.exit(1)

    print("Difficulty options:", ", ".join(d.value for d in Difficulty))
    difficulty_input = input("Assign difficulty: ").strip()
    try:
        difficulty = Difficulty(difficulty_input)
    except ValueError:
        print(f"Invalid difficulty '{difficulty_input}'.")
        sys.exit(1)

    try:
        problem_row = ProblemInsertRow(
            title=report.title or report.question_id,
            description=report.description,
            difficulty=difficulty,
            constraints=report.constraints,
            hints=report.hints,
            sampleTestCases=[t.model_dump() for t in report.sampleTestCases],
            hiddenTestCases=[t.model_dump() for t in report.hiddenTestCases],
            avgTimeComplexity=report.avgTimeComplexity,
            avgSpaceComplexity=report.avgSpaceComplexity,
            roundId=round_id,
            categories=report.categories,
            source_url=report.question_id,
            verified_solution_index=report.best_solution_index,
        )
        print(f"Validated for DB insert (round {round_id}, {difficulty.value}): {problem_row.title}")
        # actual Supabase insert call goes here
    except ValidationError as e:
        print(f"REJECTED -- failed schema validation:\n{e}")
        sys.exit(1)
    
