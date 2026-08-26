# Task: Build the DSA Question Extraction Script (Goal 1 of 3)

## Project context

This is part of a chapter's automated DSA (data structures & algorithms) question pipeline for a competitive one-on-one event. The full pipeline has three independent goals:

1. **Extraction** (this task) — search a filtered dataset by tag/difficulty, select a question, output it as JSON.
2. **Sandbox validation** (separate future task) — run the extracted solution against the extracted test cases to confirm correctness.
3. **DB feed** (separate future task) — insert validated questions into the event's database.

Each goal is built and tested independently, since this project spans years and multiple contributors/AI sessions with limited context per session. This task covers **Goal 1 only** — do not attempt sandbox execution or DB logic.

## Environment

- Use the conda environment **`ml-env`** for everything — running the script, installing any missing packages, testing. All required libraries (`datasets`, etc.) are already installed there. Do not use the base environment or a different env.
- Working directory: `Pipeline/` (repo root).

## Existing artifacts (do not modify or re-filter these)

- `Pipeline/taco_candidates/` — a HuggingFace `datasets` `DatasetDict` already saved to disk via `save_to_disk`. Load with `datasets.load_from_disk("taco_candidates")`. Use the `train` split.
- This dataset has **already been filtered** so that every row has: ≥1 solution, no `starter_code`, no `picture_num`, ≥10 test cases, and ≥1 Python-parseable solution. **Do not re-apply these filters** — they're already satisfied by every row in this dataset.

### Dataset field reference

| Field | Format | Notes |
|---|---|---|
| `solutions` | JSON string | Parse with `json.loads`. List of Python solution source strings (already filtered to Python-only). |
| `input_output` | Python-repr string | Parse with `ast.literal_eval`. Dict with `"inputs"` and `"outputs"` lists, parallel and same length. |
| `raw_tags` | Python-repr string | Parse with `ast.literal_eval`. List of lowercase CP-style tag strings, e.g. `"greedy"`, `"brute force"`, `"math"`, `"dp"`. This is the tag field to search on. |
| `difficulty` | string | One of the dataset's difficulty labels, e.g. `"VERY_HARD"`. |
| `url` | string | Unique per question — use as the question's unique ID. |
| `time_limit` | string | e.g. `"1.0 seconds"` — parse the numeric value. |
| `memory_limit` | string | e.g. `"64.0 megabytes"` — parse the numeric value. |
| `question` | string | Problem statement text. |
| `source` | string | e.g. `"codeforces"`. |
| `name` | string | Problem title. |

## What to build

A CLI script, `extraction/extract.py`, that does the following, in order:

1. Load `taco_candidates` (train split) from disk.
2. Load `used_questions.json` from the working directory if it exists (a JSON list of `url` strings — the "already selected" tracker). Treat as an empty list if the file doesn't exist yet.
3. Prompt the user for:
   - One or more tags, comma-separated (e.g. `greedy, brute force, math`). **Preserve the order entered** — order encodes priority, first entered = highest priority.
   - A difficulty value. Validate against the actual set of difficulty values present in the dataset; reject invalid input and re-prompt.
   - **Only if more than one tag was entered:** a relaxation floor (integer — minimum number of tags that must still match, see algorithm below). Validate it's between 1 and the number of tags entered; reject and re-prompt otherwise. Skip this prompt entirely if only one tag was entered (floor is implicitly 1).
4. Build the unused candidate pool: rows where `difficulty` matches exactly AND `url` is not in the loaded `used_questions.json` list. **Difficulty is never relaxed.**
5. **Tag matching with priority-ordered relaxation:**
   - For `size` from `len(tags)` down to `relaxation_floor`:
     - For each combination of `size` tags from the entered tag list, generated via `itertools.combinations(tags, size)` — **this naturally preserves priority order (first-entered tags survive longest); do not re-sort or reorder these combinations**:
       - Filter the unused pool to rows where `set(combo).issubset(set(parsed_raw_tags))`.
       - If any matches: **stop searching immediately**, keep this match list, and record which combo succeeded.
   - If no combination at any size down to the floor produces matches: print a clear "no matching question found" message and **exit without writing output or modifying `used_questions.json`**.
6. From the matched list, pick one row **uniformly at random**.
7. Build the output JSON with **exactly** this shape:

```json
{
  "question_id": "<url>",
  "source": "<source>",
  "title": "<name>",
  "question_text": "<question>",
  "difficulty": "<difficulty>",
  "requested_tags": ["<tags as entered, in order>"],
  "matched_tags": ["<the combo that actually succeeded>"],
  "relaxation_floor": 0,
  "all_tags": ["<all raw_tags of the selected question>"],
  "time_limit_seconds": 0.0,
  "memory_limit_mb": 0.0,
  "candidate_solutions": ["<all Python-parseable solutions for this question, unfiltered for correctness>"],
  "test_cases": [
    {"input": "<input string>", "output": "<expected output string>"}
  ],
  "extracted_at": "<ISO 8601 UTC timestamp of extraction>"
}
```

   - `test_cases` is built by zipping `input_output["inputs"]` and `input_output["outputs"]` pairwise, in order. **No sample/hidden split** — keep as one flat list.
   - `candidate_solutions` keeps **all** Python-parseable solutions for the question, not narrowed to one — deciding which one is actually correct is the sandbox stage's job, not this script's.

8. Write this JSON to a file inside an `extracted/` directory (create it if missing). Name the file something filesystem-safe and deterministic (e.g. a sanitized version of `name`, or a short hash of `url`).
9. Append the selected row's `url` to the in-memory used-questions list and write it back to `used_questions.json` — **only after successful extraction**. A failed/zero-match search must not touch this file.
10. Print a short human-readable summary at the end: which question was selected, its difficulty, requested vs matched tags, and the output file path.

## Code quality requirements

This pipeline will be maintained by different chapter members across multiple years, so:

- Clear function boundaries — separate functions for loading data, prompting/validating input, building the candidate pool, the relaxation search, random selection, JSON assembly, and file I/O. **No monolithic `main()` doing everything inline.**
- Docstrings on every function: purpose, inputs, outputs.
- Inline comments explaining *why* on non-obvious logic — especially the relaxation search and the `itertools.combinations` priority-ordering behavior.
- No hardcoded paths scattered through the code — define `taco_candidates` path and `used_questions.json` path as constants at the top of the file (or CLI args with sensible defaults).
- Handle the zero-match case gracefully everywhere — never crash on an empty pool.
- Don't touch or filter the dataset further beyond what's described above — all base filtering was already applied when `taco_candidates` was built; this script only does tag/difficulty/dedup search on top of it.

## Out of scope for this task

- No sandbox / solution execution / correctness checking.
- No database code.
- No modification of `taco_candidates` itself.
