"""
Pydantic schema for the sandbox's evaluation output. Field names deliberately
mirror db_schema.ProblemInsertRow where they overlap (title, description,
sampleTestCases, hiddenTestCases, etc.) so downstream mapping into the DB
row is close to a direct pass-through, not a re-derivation.

NOTE: best_solution_code has NO corresponding column in the Prisma Problem
model -- there's no field there for storing an accepted solution's source.
It's included here as pipeline/audit metadata (so you can see WHAT was
verified, not just that something passed), not as something destined for
that DB table as-is. If you want it persisted, that's a schema change on
the Prisma side, not something this model should quietly smuggle in.
"""

from typing import Optional
from pydantic import BaseModel


class TestCase(BaseModel):
    input: str
    output: str


class SolutionSummary(BaseModel):
    solution_index: int
    passed: int
    total: int
    time_seconds: float


class SandboxEvaluationReport(BaseModel):
    # -- question content, mirrors db_schema.ProblemInsertRow field names --
    question_id: Optional[str] = None
    title: Optional[str] = None
    description: str
    raw_difficulty: Optional[str] = None   # TACO's own label -- NOT the Prisma Difficulty enum
    constraints: list[str] = []             # not populated by TACO -- stays empty, same known gap as before
    hints: list[str] = []
    categories: list[str] = []
    sampleTestCases: list[TestCase] = []
    hiddenTestCases: list[TestCase] = []
    avgTimeComplexity: Optional[str] = None
    avgSpaceComplexity: Optional[str] = None

    # -- sandbox verification results --
    overall_pass: bool
    sample_size_used: int
    total_test_cases_available: int
    candidates_tested: int
    best_solution_index: Optional[int] = None
    best_solution_code: Optional[str] = None
    per_solution_summary: list[SolutionSummary] = []
    detailed_error: Optional[str] = None