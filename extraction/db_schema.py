"""
Validates pipeline output against the ACTUAL Prisma Problem model before
DB insert.

Excluded here (not this model's job): id/createdAt/updatedAt (DB-assigned),
hackAttempts/lockedSolutions/submissions (relations populated later).

roundId and difficulty are OPERATOR INPUTS, supplied explicitly at insert
time in run_pipeline.py -- not derived from TACO or the sandbox report.

No standalone build_problem_row() here anymore -- run_pipeline.py
constructs ProblemInsertRow directly from schema.SandboxEvaluationReport's
already-validated fields, since duplicating that construction logic in two
places just risked them drifting out of sync.
"""

from enum import Enum
from typing import Optional, Any
from pydantic import BaseModel, field_validator, model_validator


class Difficulty(str, Enum):
    R0 = "R0"
    R1_EASY = "R1_EASY"
    R1_MEDIUM = "R1_MEDIUM"
    R1_HARD = "R1_HARD"
    R2_BOUNTY = "R2_BOUNTY"
    R2_CHALLENGE = "R2_CHALLENGE"
    R3 = "R3"


MIN_DESCRIPTION_LENGTH = 20
MIN_TEST_CASES = 2


class ProblemInsertRow(BaseModel):
    title: str
    description: str
    difficulty: Difficulty
    constraints: list[str] = []     # always [] currently -- TACO/extract.py never populates this, unchanged known gap
    hints: list[str] = []
    boilerplate: Optional[dict[str, Any]] = None
    sampleTestCases: list[dict]
    hiddenTestCases: list[dict]
    avgTimeComplexity: Optional[str] = None
    avgSpaceComplexity: Optional[str] = None
    roundId: int
    categories: list[str] = []

    source_url: Optional[str] = None
    verified_solution_index: Optional[int] = None

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("title cannot be empty")
        return v.strip()

    @field_validator("description")
    @classmethod
    def description_long_enough(cls, v):
        if not v or len(v.strip()) < MIN_DESCRIPTION_LENGTH:
            raise ValueError(f"description missing or shorter than {MIN_DESCRIPTION_LENGTH} chars")
        return v.strip()

    @field_validator("sampleTestCases", "hiddenTestCases")
    @classmethod
    def test_cases_well_formed(cls, v):
        for case in v:
            if "input" not in case or "output" not in case:
                raise ValueError(f"test case missing input/output keys: {case}")
        return v

    @model_validator(mode="after")
    def enough_test_cases_total(self):
        total = len(self.sampleTestCases) + len(self.hiddenTestCases)
        if total < MIN_TEST_CASES:
            raise ValueError(f"only {total} test case(s) total, need at least {MIN_TEST_CASES}")
        return self

    @field_validator("roundId")
    @classmethod
    def round_id_positive(cls, v):
        if v <= 0:
            raise ValueError("roundId must be a positive integer matching an existing Round.roundNumber")
        return v