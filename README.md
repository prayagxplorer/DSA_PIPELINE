# DSA Question Pipeline

Automated pipeline for sourcing, verifying, and preparing DSA questions for
a coding event, from the [TACO dataset](https://huggingface.co/datasets/BAAI/TACO)
through to a schema-validated row ready for the production Postgres/Supabase
database (Prisma `Problem` model).

## Why this exists

Originally scoped as "generate DSA questions from scratch with LLMs." That
approach was abandoned after working through the core problem: **guaranteeing
no hallucination is not achievable by prompting alone** -- LLM-generated
problems, solutions, and test cases all need independent verification, and
LLM-reviewing-LLM doesn't provide that independence. The pipeline pivoted to
**curating from a real, pre-existing dataset with real accepted solutions**
(TACO), and using **execution-based verification** (a sandbox, not another
model's opinion) as the sole authority on correctness.

## Architecture, end to end

```
1. prepare_dataset.py  -- one-time: download TACO, save locally
2. extract.py           -- (teammate's) interactive: pick one question by
                            tags + TACO's own difficulty label
3. filter.py             -- completeness check, candidate-solution cap,
                            content-artifact rejection (see Known Gaps)
4. run_sandbox.py         -- self-hosted Piston: test ALL candidate
                            solutions against a random sample of test
                            cases, rank by (most passed, fastest), output
                            a structured JSON report
5. run_pipeline.py         -- orchestrates 2-4, then prompts the operator
                            for roundId + Prisma Difficulty (NOT derived
                            from TACO's label -- see Known Gaps)
6. db_schema.py            -- ProblemInsertRow: final Pydantic validation
                            against the actual Prisma schema shape
7. [not yet built] DB insert into Supabase
```

**Dedup engine exists (`taco/dedup.py`, `taco/embeddings.py`) but is not
yet wired into `run_pipeline.py`.** This is the most important open gap --
see Known Gaps below.

## File structure

```
dsa_pipeline/
├── .env                     # secrets: HF_TOKEN, DATABASE_URL -- gitignored
├── .gitignore
├── requirements.txt
├── README.md                 # this file
│
├── extraction/
│   ├── extract.py              # teammate's -- picks one question from
│   │                            local TACO copy by tags/difficulty
│   ├── tag.py                  # teammate's -- tag-matching helper used by extract.py
│   ├── prepare_dataset.py      # one-time: downloads TACO to ./taco_candidates
│   ├── filter.py                # completeness + candidate cap + content check
│   ├── db_schema.py              # ProblemInsertRow -- final DB-shape validation
│   ├── run_pipeline.py            # orchestrates extract -> sandbox -> validate
│   ├── used_questions.json        # exact-URL usage tracker (extract.py's own)
│   ├── taco_candidates/            # local TACO copy -- gitignored, regenerable
│   └── extracted/                   # per-run question + report JSONs -- gitignored
│
├── sandbox/
│   ├── piston_client.py             # self-hosted Piston HTTP client
│   ├── schema.py                     # SandboxEvaluationReport (structured output)
│   ├── run_sandbox.py                 # samples, tests, ranks, reports
│   ├── mock_piston_server.py          # TEST ONLY -- API-compatible, no Docker needed
│   └── README.md
│
└── taco/
    ├── embeddings.py             # local (sentence-transformers) embedding provider
    ├── dedup.py                   # URL + embedding dedup engine
    ├── problem_registry.json      # dedup's persistent registry -- NOT YET WIRED IN
    └── README.md
```

## Setup

```bash
cd dsa_pipeline
pip install -r requirements.txt
```

`requirements.txt` should contain:
```
pydantic>=2.0.0
numpy>=1.26.0
datasets>=2.19.0
sentence-transformers>=3.0.0
requests>=2.31.0
```

**One-time dataset download** (needs a HuggingFace token -- create one at
huggingface.co/settings/tokens, "read" scope is enough):
```bash
cd extraction
export HF_TOKEN=hf_your_token_here     # Windows Git Bash: same syntax
python prepare_dataset.py
```

**Self-host Piston** (the public API is no longer usable for this):
```bash
docker run -d --name piston_api --restart always -p 2000:2000 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v piston_data:/piston --privileged \
  ghcr.io/engineer-man/piston

curl -X POST http://localhost:2000/api/v2/packages -H "Content-Type: application/json" -d "{\"language\": \"python\", \"version\": \"3.10.0\"}"
curl http://localhost:2000/api/v2/runtimes   # confirm python shows up
```
`--privileged` is required for Piston's own internal sandboxing -- don't
expose port 2000 outside your machine without a firewall in front of it.

No Docker available yet? `sandbox/mock_piston_server.py` implements the same
API surface backed by a plain subprocess, so you can validate pipeline logic
without it -- but it has **none of real Piston's isolation** (no cgroups/
namespaces), test-only, never point production traffic at it.

## Running it

```bash
cd extraction
python run_pipeline.py
```

Prompts for tags + difficulty (from `extract.py`), picks a question, tests
every candidate solution in the sandbox, and if it passes, prompts for
`roundId` and the actual Prisma `Difficulty` enum value before validating
the final DB-ready row.

## Key design decisions, and why

- **Every dataset-provided solution is untrusted by default.** TACO's own
  documentation notes known false-positive risk (weak test coverage lets
  incorrect solutions get accepted). The sandbox re-verifies every
  candidate against real execution -- a solution being "accepted" in TACO's
  original judge is a starting candidate, not a guarantee.
- **"Best" solution = most sampled test cases passed, tiebreak by fastest
  total execution time.** An explicit assumption, stated rather than
  silently decided -- change the sort key in `run_sandbox.py` if you want
  different tiebreak logic.
- **Output whitespace is normalized before comparison.** TACO's test case
  outputs have inconsistent trailing newlines -- confirmed directly during
  testing, not assumed. Skipping this causes false-positive mismatches on
  genuinely correct solutions.
- **roundId and Difficulty are operator inputs, not derived from TACO.**
  Your Prisma `Difficulty` enum (`R0`/`R1_EASY`/`R1_MEDIUM`/`R1_HARD`/
  `R2_BOUNTY`/`R2_CHALLENGE`/`R3`) encodes round + tier together, not a
  flat difficulty scale, and doesn't map cleanly from TACO's own
  EASY/MEDIUM/HARD/etc. label -- that mapping is a judgment call made
  per-import, by a human, on purpose.
- **Local embeddings, not an LLM API, for dedup.** Dedup checks run on
  every candidate question -- routing that through Gemini/Groq would eat
  into the same daily quota you're trying to protect for other roles.
  `sentence-transformers` runs free, unlimited, and fully offline once the
  model's downloaded once.
- **Boilerplate generation is deliberately deferred**, matching your
  original instinct: generate it once question/solution/test cases are
  fully stable, not before, since the signature could still change mid-fix.

## Known gaps -- read before assuming this is production-complete

1. **Dedup is built but not wired into `run_pipeline.py`.** `extract.py`
   only excludes exact-URL repeats (`used_questions.json`). Nothing checks
   for reworded/paraphrased duplicates right now. This is the single
   highest-priority next step.
2. **`constraints: String[]` is never populated.** TACO/`extract.py`
   provides no constraints field -- every row currently inserts with
   `constraints: []`. Needs either a heuristic parse of `question_text` or
   a dedicated LLM step; neither is built yet.
3. **Content-artifact filtering is a known real issue, not hypothetical.**
   A real extracted question was found with `<image>` placeholders where
   Codeforces' original math notation should be (the image was stripped
   during TACO's scraping) -- the description was unreadable but passed
   sandbox + schema validation cleanly, since neither checks semantic
   comprehensibility. A basic string-match filter for known scrape
   artifacts (`<image>`, mojibake patterns) should be added to `filter.py`
   before this is safe to run unattended.
4. **No independent AI-generated solution or cross-check against TACO's
   candidates** -- explicitly scoped out for now. The sandbox only tests
   what TACO already provides.
5. **DB insert itself isn't built.** `db_schema.py` validates the shape;
   nothing yet writes to Supabase.
6. **No monitoring/alerting.** If Piston goes down, quota runs out, or a
   run silently fails, nothing notifies anyone -- worth adding before this
   runs unattended close to the actual event.
7. **`used_questions.json` / `problem_registry.json` are local files, not
   a shared database.** Fine for a single machine; if multiple people run
   extraction from different machines, these need to be shared (committed
   to git for now, or migrated to Supabase directly) or duplicate/conflicting
   selections become possible across the team.

## Attribution

Question content sourced from [BAAI/TACO](https://huggingface.co/datasets/BAAI/TACO)
(Apache-2.0 license on the compilation). TACO itself merges content from
APPS and CodeContests, which in turn source from Codeforces, AtCoder,
CodeChef, Kattis, HackerRank, and (via APPS) LeetCode. LeetCode-sourced
rows carry a less clear rights chain than the rest -- flag or exclude these
per your own risk tolerance before using them in the live event.
