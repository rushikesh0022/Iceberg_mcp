"""Step 3b: compare a proposed table with one candidate and decide.

Checks, each PASS / FAIL / UNKNOWN:
  columns - which proposed columns the candidate has, which are new
  grain   - does one row mean the same thing in both
  logic   - same calculation for shared columns, same filters, same joins

Decision rules:
  all PASS, no new columns    -> REUSE
  all PASS, new columns       -> EVOLVE_SCHEMA
  grain or logic FAIL         -> NEW_TABLE
  any UNKNOWN (and no FAIL)   -> NEEDS_INFO
"""

from __future__ import annotations

from iceguard.models import CandidateAssessment, CheckResult, Decision, SqlAnalysis


def check_columns(proposed: SqlAnalysis, existing: SqlAnalysis) -> CheckResult:
    raise NotImplementedError("Step 3")


def check_grain(proposed: SqlAnalysis, existing: SqlAnalysis) -> CheckResult:
    raise NotImplementedError("Step 3")


def check_logic(proposed: SqlAnalysis, existing: SqlAnalysis) -> CheckResult:
    raise NotImplementedError("Step 3")


def decide(checks: list[CheckResult], new_columns: list[str]) -> Decision:
    raise NotImplementedError("Step 3")


def assess(proposed: SqlAnalysis, existing: SqlAnalysis) -> CandidateAssessment:
    raise NotImplementedError("Step 3")
