# TACO loading, filtering, and deduplication

Loads TACO problems, filters unusable rows, deduplicates (URL exact match +
embedding similarity), and writes clean, validated rows ready for the next
stage: sandbox verification (not built yet).

## Quick test (no real data needed)

```bash
pip install pydantic numpy
python load_taco.py
```

Runs against a synthetic mock dataset (`mock_taco.py`) that deliberately
includes an exact-URL duplicate, a reworded near-duplicate, a LeetCode-
sourced row, and an incomplete row -- so you can see all four filter/dedup
paths actually firing before you touch real data.

## IMPORTANT: install the real embedding model before running on real data

```bash
pip install sentence-transformers
```

Without it, `embeddings.py` silently falls back to a crude hashing
vectorizer that is NOT good enough for real duplicate detection -- verified
directly during testing: it scored a genuine near-duplicate pair at 0.795
cosine similarity, below the 0.90 threshold, meaning **it would have missed
a real duplicate**. `sentence-transformers`' `all-MiniLM-L6-v2` model
downloads once (~80MB) then runs fully offline and free from then on --
install it before running this for real, not after.

## Using real TACO data

```python
from datasets import load_dataset
from load_taco import process_rows

ds = load_dataset("BAAI/TACO", split="train", difficulties=["EASY", "MEDIUM"])

stats = process_rows(
    ds,
    registry_path="problem_registry.json",   # persists across runs
    output_path="taco_extracted.jsonl",
    exclude_leetcode=False,  # True to drop LeetCode-sourced rows entirely instead of just flagging them
)
print(stats)
```

## Design decisions worth knowing about

- **Dedup order: URL match first, embeddings second.** URL match is free
  and catches the case where TACO's merged sources (APPS + CodeContests +
  direct scrapes) independently pulled the same original problem from
  Codeforces/AtCoder/etc. Embeddings catch reworded duplicates that don't
  share a URL.

- **`problem_registry.json` is a stub for the real Supabase/pgvector
  check.** It persists across runs so dedup works day-to-day, not just
  within one script execution -- but it's a local file standing in for a
  real database query. Swap `LocalRegistry` for a Supabase-backed version
  when that's wired up; `check_duplicate()`'s interface doesn't need to
  change.

- **LeetCode-sourced rows are flagged, not excluded, by default**
  (`is_leetcode_sourced` field). TACO's overall Apache-2.0 license is on
  BAAI's compilation, not necessarily a clearance of LeetCode's own terms
  on the underlying problem text. Decide your own risk tolerance -- set
  `exclude_leetcode=True` in `process_rows()` if you want them dropped
  entirely instead of just marked.

- **`call_style` (stdin_stdout vs function_call) is preserved per row**,
  detected from whether TACO's `input_output.fn_name` is present. This
  matters directly for the sandbox you're about to build -- the two styles
  need different execution wrappers, same issue flagged for CodeNet
  earlier.

- **Solutions from the dataset are NOT trusted.** `candidate_solutions`
  holds up to 5 raw solutions per problem as *candidates* -- every one
  still needs to go through your own independent-solution + sandbox diff
  before anything is considered verified. This script's job stops at
  "clean, deduplicated, parseable" -- it does not claim correctness.

## Files

```
taco/
├── schema.py        # Pydantic model -- the validation contract
├── embeddings.py     # local embedding provider (sentence-transformers + fallback)
├── dedup.py          # URL + embedding dedup logic, local registry
├── mock_taco.py       # synthetic test data with deliberate duplicates
├── load_taco.py       # main script
└── README.md
```
