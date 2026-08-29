# Sandbox

Takes a question JSON (your teammate's script's output format), samples
15-20 test cases, runs every `candidate_solutions` entry against the sample
in a Piston sandbox, ranks solutions by (most cases passed, then fastest),
and reports overall yes/no + a detailed error for the best solution's first
failure if it doesn't pass everything.

Does NOT generate or test an independent AI solution -- only evaluates the
dataset-provided candidates, as scoped.

## Self-host Piston (production)
#can also see main README for complete info
```bash
docker run -d --name piston_api --restart always -p 2000:2000 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v piston_data:/piston --privileged \
  ghcr.io/engineer-man/piston

# install the python runtime
curl -X POST http://localhost:2000/api/v2/packages \
  -H "Content-Type: application/json" \
  -d '{"language": "python", "version": "3.10.0"}'
```

`--privileged` is required for Piston's internal sandboxing (namespaces/
cgroups) -- don't expose port 2000 to the open internet without a firewall/
reverse proxy in front of it.

Then just run:
```bash
pip install requests
python run_sandbox.py path/to/question.json
python run_sandbox.py path/to/question.json --seed 42   # reproducible sampling, for debugging a specific run
```

`piston_client.py` defaults to `http://localhost:2000` -- override with the
`PISTON_BASE_URL` env var if it's hosted elsewhere.

## Testing without Docker

`mock_piston_server.py` implements just enough of Piston's HTTP API
(`/api/v2/runtimes`, `/api/v2/execute`) to validate the sandbox logic with
REAL subprocess execution, no Docker required. This is TEST INFRASTRUCTURE
ONLY -- it has none of real Piston's OS-level isolation (namespaces,
cgroups), it's a bare `subprocess.run()` underneath. Don't point production
traffic at it.

```bash
python mock_piston_server.py &
python run_sandbox.py path/to/question.json --seed 42
```

This was run against the real uploaded question
(`codeforces.com/problemset/problem/84/B`) during development: all 15 real
candidate solutions passed all 18 sampled real test cases. Separately
verified the failure path by injecting a deliberately wrong solution --
correctly detected (6/18 passed), correctly ranked the good solutions above
it, and produced a readable diff (`Expected: '21'` / `Actual: '20'`) when
tested as the only candidate.

## Design notes

- **Output whitespace normalization**: the real test_cases in your uploaded
  file have inconsistent trailing newlines (`"8"` vs `"8\n"`) -- confirmed
  directly, not assumed. `normalize_output()` strips this before comparing,
  or you'd get false-positive mismatches on correct solutions.
- **Time/memory limits enforced by Piston itself**, not just a client-side
  timer -- `run_timeout`/`run_memory_limit` are passed straight through
  using the problem's own `time_limit_seconds`/`memory_limit_mb`.
- **"Best" solution = most sampled cases passed, tiebreak by fastest total
  time.** This was an explicit assumption stated during the build, not
  confirmed with you -- change the sort key in `evaluate_question()` if you
  want a different tiebreak (e.g. shortest code, fewest lines).
- **Sampling is random per call** unless you pass `--seed` -- without a
  seed, re-running the same question can sample a different subset of test
  cases and potentially get a different verdict. Use a seed when you need
  to reproduce or debug a specific result.

## What this deliberately does NOT do yet

- No independent AI-generated solution or cross-checking against it --
  scoped out explicitly for this build.
- No retry/fixer loop -- this is a one-shot evaluator, not the LangGraph
  node yet.
- No handling of `function_call`-style problems (all real data seen so far
  is stdin/stdout) -- would need a wrapper similar to the earlier
  CodeNet/TACO work if that call style shows up in practice.

## Files

```
sandbox/
├── piston_client.py       # self-hosted Piston HTTP client
├── run_sandbox.py     
|__ schema.py      
├── mock_piston_server.py   # test-only API-compatible server, no Docker needed
└── README.md
```
