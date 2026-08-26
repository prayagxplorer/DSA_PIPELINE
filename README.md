# DSA Pipeline

## Project description

This repository is an automated pipeline for sourcing data structures and algorithms (DSA) competitive-programming questions for the chapter's one-on-one event database. It draws from the [BAAI/TACO dataset](https://huggingface.co/datasets/BAAI/TACO), which contains roughly 25,000 questions.

The work is split into independent stages so it can be maintained across years and contributors: Goal 1, extraction, is complete; Goal 2, sandbox validation, is in progress; and Goal 3, feeding validated questions into the event database, has not started. Each stage is intended to be understandable and buildable on its own.

## Setup

1. Clone this repository.
2. Create and activate a Python environment. Python 3.11 or newer is recommended; conda, `venv`, and other environment managers all work.
3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Copy `.env.example` to `.env`, then paste in a real Hugging Face token:

   ```bash
   cp .env.example .env
   # Edit .env so it contains: HF_TOKEN=...
   ```

   A token is only necessary when regenerating `taco_candidates/` from scratch. It is not needed to work with the already extracted question files in `extracted/`.

## Getting started by task

### Extending or modifying extraction (Goal 1)

Build the local candidate dataset, then run the interactive extractor from the repository root:

```bash
python Filter.py
python extraction/extract.py
```

`Filter.py` downloads and filters TACO into `taco_candidates/`. That directory is derived data, is not tracked by Git, and generally regenerates in about one to two minutes.

### Building the sandbox (Goal 2)

No dataset or Hugging Face token is needed. The committed JSON files in `extracted/` are genuine Goal-1 outputs and test fixtures. Build against the schema in [Extraction output format](#extraction-output-format) without needing to read `extract.py` or access the source dataset.

## Candidate filtering constraints

`Filter.py` creates `taco_candidates/` using these constraints, which define a valid candidate throughout the pipeline:

- At least one solution must be present.
- `starter_code` must be absent. This deliberately excludes LeetCode-style function-signature problems, which are not supported yet.
- `picture_num` must be absent, excluding questions that depend on an image in the statement.
- At least 10 test cases must be available.
- At least one solution must parse as valid Python (`ast.parse` succeeds). Non-Python solutions are removed from the `solutions` field entirely, rather than merely being deprioritized.
- `time_limit` and `memory_limit` must each contain a parseable, non-null numeric value.

## Tag reference

Extraction searches the `raw_tags` field. `tags` and `skill_types` also exist in TACO, but they are not used for this pipeline's search. Run the following after creating `taco_candidates/` to print frequency counts for every available tag, which helps choose tags before extraction:

```bash
python tag.py
```

## How extraction search works

- Enter one or more tags in priority order: the first tag has the highest priority. Enter an exact dataset difficulty as well.
- When entering multiple tags, choose a relaxation floor: the minimum number of tags that must continue to match.
- The extractor first requires every requested tag. If no question matches, it progressively tries lower-priority tag combinations until it reaches that floor.
- Difficulty is always a hard filter and is never relaxed.
- URLs listed in `used_questions.json` are excluded before tag matching begins.

## Extraction output format

Each `extracted/*.json` file has this exact shape:

| Field | Type | Description |
| --- | --- | --- |
| `question_id` | string | Unique question URL from TACO. |
| `source` | string | Source platform recorded by TACO. |
| `title` | string | Problem title. |
| `question_text` | string | Complete problem statement. |
| `difficulty` | string | Exact TACO difficulty label. |
| `requested_tags` | array of strings | Tags entered by the user, in priority order. |
| `matched_tags` | array of strings | Tag combination that actually produced the selected question. |
| `relaxation_floor` | integer | Minimum requested tag count configured for the search. |
| `all_tags` | array of strings | All `raw_tags` on the selected question. |
| `time_limit_seconds` | number | Numeric time limit parsed from TACO. |
| `memory_limit_mb` | number | Numeric memory limit parsed from TACO. |
| `candidate_solutions` | array of strings | Every Python-parseable solution; correctness is not yet verified. |
| `test_cases` | array of objects | Flat `{input, output}` pairs, with no sample/hidden split. |
| `extracted_at` | string | ISO 8601 UTC extraction timestamp. |

`candidate_solutions` can contain more than one solution string, and none is guaranteed correct yet. Verifying them and choosing a correct solution is the sandbox stage's responsibility.

## Handoff to the sandbox (Goal 2)

Every file in `extracted/` is one independent sandbox work item. A sandbox implementation should be able to load any one file, run every `candidate_solutions` entry against every `test_cases` entry within `time_limit_seconds`, and determine which solution or solutions are correct. It needs no dataset, Hugging Face token, or knowledge of the extraction internals.

## Known limitation

`used_questions.json` is versioned as one shared list. It works when one person runs `extract.py` and pushes at a time, but concurrent extract-and-push workflows will create Git conflicts. Goal 3 is expected to replace this temporary tracker with database-backed tracking; until then, treat running `extract.py` as a single-owner task.
