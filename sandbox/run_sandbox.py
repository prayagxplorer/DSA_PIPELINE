"""
Takes a question JSON, samples test cases, runs every candidate_solution
against the sample, ranks by (most cases passed, fastest), and outputs a
single structured JSON report matching schema.SandboxEvaluationReport --
no interleaved human-readable progress text on stdout by default.

Pass --verbose to also print progress to STDERR (stdout stays clean JSON
either way, so piping/parsing this script's output is always safe).
"""

import json
import random
import sys
import time
from typing import Optional

from piston_client import run_code, normalize_output
from schema import SandboxEvaluationReport, TestCase, SolutionSummary


def log(message: str, verbose: bool):
    if verbose:
        print(message, file=sys.stderr)


def sample_test_cases(test_cases: list[dict], sample_size: int = 18, seed: Optional[int] = None) -> list[dict]:
    rng = random.Random(seed)
    if len(test_cases) <= sample_size:
        return list(test_cases)
    return rng.sample(test_cases, sample_size)


def run_solution_against_cases(code: str, test_cases: list[dict],
                                 time_limit_seconds: float, memory_limit_mb: float,
                                 verbose: bool = False) -> dict:
    results = []
    total_time = 0.0

    for i, case in enumerate(test_cases):
        start = time.time()
        run_result = run_code(
            code=code, stdin_text=case["input"],
            time_limit_seconds=time_limit_seconds, memory_limit_mb=memory_limit_mb,
        )
        elapsed = time.time() - start
        total_time += elapsed

        expected = normalize_output(case["output"])
        actual = normalize_output(run_result["stdout"])
        passed = (run_result["code"] == 0 and not run_result["timed_out"] and actual == expected)

        results.append({
            "case_index": i, "passed": passed, "input": case["input"],
            "expected_output": expected, "actual_output": actual,
            "stderr": run_result["stderr"], "timed_out": run_result["timed_out"],
            "exit_code": run_result["code"], "elapsed_seconds": round(elapsed, 3),
        })

    passed_count = sum(1 for r in results if r["passed"])
    return {
        "passed_count": passed_count, "total_count": len(test_cases),
        "all_passed": passed_count == len(test_cases),
        "total_time_seconds": round(total_time, 3), "case_results": results,
    }


def format_detailed_error(case_result: dict) -> str:
    if case_result["timed_out"]:
        return f"Test case {case_result['case_index']}: TIMED OUT.\nInput:\n{case_result['input']}"
    if case_result["exit_code"] != 0:
        return (f"Test case {case_result['case_index']}: RUNTIME ERROR "
                f"(exit code {case_result['exit_code']}).\nInput:\n{case_result['input']}\n"
                f"Stderr:\n{case_result['stderr']}")
    return (f"Test case {case_result['case_index']}: WRONG OUTPUT.\nInput:\n{case_result['input']}\n"
            f"Expected: {case_result['expected_output']!r}\nActual:   {case_result['actual_output']!r}")


def evaluate_question(question: dict, sample_size: int = 18, seed: Optional[int] = None,
                       verbose: bool = False) -> SandboxEvaluationReport:
    candidates = question["candidate_solutions"]
    all_cases = question["test_cases"]
    time_limit = question.get("time_limit_seconds", 2.0)
    memory_limit = question.get("memory_limit_mb", 256.0)

    sampled_cases = sample_test_cases(all_cases, sample_size=sample_size, seed=seed)

    solution_results = []
    for idx, code in enumerate(candidates):
        log(f"  Testing candidate_solutions[{idx}] against {len(sampled_cases)} sampled cases...", verbose)
        result = run_solution_against_cases(code, sampled_cases, time_limit, memory_limit, verbose=verbose)
        result["solution_index"] = idx
        solution_results.append(result)
        log(f"    -> {result['passed_count']}/{result['total_count']} passed "
            f"({result['total_time_seconds']}s total)", verbose)

    ranked = sorted(solution_results, key=lambda r: (-r["passed_count"], r["total_time_seconds"]))
    best = ranked[0] if ranked else None

    detailed_error = None
    if best and not best["all_passed"]:
        first_failure = next(c for c in best["case_results"] if not c["passed"])
        detailed_error = format_detailed_error(first_failure)

    # store the FULL original test case set for the DB, not just the sample
    # used for speed during verification -- sampling was an execution-time
    # shortcut, not a reason to under-populate the actual question record
    full_cases = [TestCase(input=c["input"], output=c["output"]) for c in all_cases]

    return SandboxEvaluationReport(
        question_id=question.get("question_id"),
        title=question.get("title"),
        description=question.get("question_text", ""),
        raw_difficulty=question.get("difficulty"),
        categories=question.get("matched_tags", []),
        sampleTestCases=full_cases[:1],
        hiddenTestCases=full_cases[1:],
        overall_pass=best["all_passed"] if best else False,
        sample_size_used=len(sampled_cases),
        total_test_cases_available=len(all_cases),
        candidates_tested=len(candidates),
        best_solution_index=best["solution_index"] if best else None,
        best_solution_code=candidates[best["solution_index"]] if best else None,
        per_solution_summary=[
            SolutionSummary(solution_index=r["solution_index"], passed=r["passed_count"],
                             total=r["total_count"], time_seconds=r["total_time_seconds"])
            for r in sorted(solution_results, key=lambda r: r["solution_index"])
        ],
        detailed_error=detailed_error,
    )


if __name__ == "__main__":
    verbose = "--verbose" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if len(args) < 1:
        print(json.dumps({"error": "Usage: python run_sandbox.py <question.json> [--seed N] [--verbose]"}))
        sys.exit(1)

    path = args[0]
    seed = None
    if "--seed" in sys.argv:
        seed = int(sys.argv[sys.argv.index("--seed") + 1])

    with open(path) as f:
        question = json.load(f)

    report = evaluate_question(question, seed=seed, verbose=verbose)

    output_json = report.model_dump_json(indent=2)
    with open("sandbox_report.json", "w") as f:
        f.write(output_json)

    print(f"Report written to sandbox_report.json ({'PASS' if report.overall_pass else 'FAIL'})", file=sys.stderr)
