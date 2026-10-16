"""Step 3b: compare a proposed table with one candidate and decide.

Both analyses must already be expanded to the same upstream sources
(see sql_analysis.expand_upstream), so equal text means equal calculation.

Checks, each PASS / FAIL / UNKNOWN:
  columns - which proposed columns the candidate has, which are new, which conflict
  grain   - does one row mean the same thing in both
  logic   - same filters and joins, so the same rows exist

Decision rules:
  columns, grain or logic FAIL      -> NEW_TABLE
  any UNKNOWN                       -> NEEDS_INFO
  all PASS, no new columns          -> REUSE
  all PASS, new columns             -> EVOLVE_SCHEMA (if the candidate's pipeline can supply them)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from iceguard.models import (
    Candidate,
    CandidateAssessment,
    CheckResult,
    CheckStatus,
    Decision,
    SqlAnalysis,
)
from iceguard.sql_analysis import DIALECT

# Lower is better; used to pick the best decision across candidates.
DECISION_RANK = {
    Decision.REUSE: 0,
    Decision.EVOLVE_SCHEMA: 1,
    Decision.NEEDS_INFO: 2,
    Decision.NEW_TABLE: 3,
}


@dataclass
class ColumnMatch:
    matched: dict[str, str] = field(default_factory=dict)  # proposed name -> candidate name
    new: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


def match_columns(proposed: SqlAnalysis, existing: SqlAnalysis) -> ColumnMatch:
    by_expression = {o.expression: o.name for o in existing.outputs}
    existing_names = {o.name for o in existing.outputs}
    same_sources = set(proposed.source_tables) == set(existing.source_tables)
    result = ColumnMatch()
    for o in proposed.outputs:
        # count() or a constant reads no column, so equal text proves nothing
        # unless both queries read the same tables.
        if o.expression in by_expression and (o.source_columns or same_sources):
            result.matched[o.name] = by_expression[o.expression]
        elif o.name in existing_names:
            result.conflicts.append(o.name)
        else:
            result.new.append(o.name)
    return result


def check_columns(proposed: SqlAnalysis, existing: SqlAnalysis) -> CheckResult:
    m = match_columns(proposed, existing)
    reasons = []
    for p_name, e_name in m.matched.items():
        reasons.append(
            f"{p_name}: same calculation"
            if p_name == e_name
            else f"{p_name}: same calculation as {e_name} (renamed)"
        )
    reasons += [f"{name}: new column" for name in m.new]
    reasons += [
        f"{name}: same name, different calculation in {existing.target_table}"
        for name in m.conflicts
    ]

    if m.conflicts:
        status = CheckStatus.FAIL
    elif not m.matched:
        status = CheckStatus.FAIL
        reasons.append("No proposed column matches a candidate column.")
    else:
        status = CheckStatus.PASS
    return CheckResult(name="columns", status=status, reasons=reasons)


def check_grain(proposed: SqlAnalysis, existing: SqlAnalysis) -> CheckResult:
    if not proposed.grain or not existing.grain:
        side = "proposed table" if not proposed.grain else str(existing.target_table)
        return CheckResult(
            name="grain",
            status=CheckStatus.UNKNOWN,
            reasons=[f"The grain of the {side} is unknown."],
        )

    p_grain = _grain_expressions(proposed)
    e_grain = _grain_expressions(existing)
    if p_grain == e_grain:
        return CheckResult(
            name="grain",
            status=CheckStatus.PASS,
            reasons=[f"Both have one row per ({', '.join(proposed.grain)})."],
        )

    reasons = [
        f"Proposed: one row per ({', '.join(proposed.grain)}); "
        f"{existing.target_table}: one row per ({', '.join(existing.grain)})."
    ]
    existing_expressions = {o.expression for o in existing.outputs}
    if proposed.group_by and set(proposed.group_by) <= existing_expressions:
        reasons.append(f"Can be built from {existing.target_table} by aggregation.")
    return CheckResult(name="grain", status=CheckStatus.FAIL, reasons=reasons)


def check_logic(proposed: SqlAnalysis, existing: SqlAnalysis) -> CheckResult:
    statuses: list[CheckStatus] = []
    reasons: list[str] = []
    name = existing.target_table

    for f in sorted(set(existing.filters) - set(proposed.filters)):
        statuses.append(CheckStatus.FAIL)
        reasons.append(f"{name} keeps only rows where {f}; the proposal needs the other rows too.")

    by_expression = {o.expression: o.name for o in existing.outputs}
    for f in sorted(set(proposed.filters) - set(existing.filters)):
        rewritten = None if f.startswith("HAVING ") else _rewrite_on_outputs(f, by_expression)
        if rewritten is None:
            statuses.append(CheckStatus.FAIL)
            reasons.append(f"Extra filter {f} cannot be applied to {name}'s columns.")
        else:
            statuses.append(CheckStatus.PASS)
            reasons.append(f"Extra filter: read {name} WHERE {rewritten}.")

    existing_joins = {(j.kind, j.table, j.condition) for j in existing.joins}
    proposed_joins = {(j.kind, j.table, j.condition) for j in proposed.joins}
    for kind, table, _ in sorted(proposed_joins - existing_joins):
        if kind != "LEFT":
            statuses.append(CheckStatus.FAIL)
            reasons.append(f"{kind} JOIN {table} drops rows without a match.")
        elif proposed.group_by:
            statuses.append(CheckStatus.UNKNOWN)
            reasons.append(f"Cannot tell whether LEFT JOIN {table} changes the aggregates.")
        elif proposed.grain:
            statuses.append(CheckStatus.PASS)
            reasons.append(f"LEFT JOIN {table} matches at most one row: it adds columns only.")
        else:
            statuses.append(CheckStatus.FAIL)
            reasons.append(f"LEFT JOIN {table} can multiply rows.")
    for kind, table, _ in sorted(existing_joins - proposed_joins):
        if kind != "LEFT":
            statuses.append(CheckStatus.FAIL)
            reasons.append(f"{name} uses {kind} JOIN {table}, which drops rows without a match.")

    if CheckStatus.FAIL in statuses:
        status = CheckStatus.FAIL
    elif CheckStatus.UNKNOWN in statuses:
        status = CheckStatus.UNKNOWN
    else:
        status = CheckStatus.PASS
    if not reasons:
        reasons.append("Same filters and joins.")
    return CheckResult(name="logic", status=status, reasons=reasons)


def decide(checks: list[CheckResult], new_columns: list[str], evolvable: bool = True) -> Decision:
    statuses = {c.status for c in checks}
    if CheckStatus.FAIL in statuses:
        return Decision.NEW_TABLE
    if CheckStatus.UNKNOWN in statuses:
        return Decision.NEEDS_INFO
    if not new_columns:
        return Decision.REUSE
    return Decision.EVOLVE_SCHEMA if evolvable else Decision.NEW_TABLE


def assess(
    proposed: SqlAnalysis, existing: SqlAnalysis, candidate: Candidate
) -> CandidateAssessment:
    checks = [
        check_columns(proposed, existing),
        check_grain(proposed, existing),
        check_logic(proposed, existing),
    ]
    new_columns = match_columns(proposed, existing).new

    # New columns can be added to the candidate only if its pipeline can read
    # their sources: the candidate's own sources, or a LEFT JOIN that the logic
    # check accepted as adding columns only.
    reachable = set(existing.source_tables) | {j.table for j in proposed.joins if j.kind == "LEFT"}
    unreachable = [
        o.name
        for o in proposed.outputs
        if o.name in new_columns
        and not {c.rpartition(".")[0] for c in o.source_columns} <= reachable
    ]
    if unreachable:
        checks[0].reasons.append(
            f"{', '.join(unreachable)} cannot be produced by {existing.target_table}'s pipeline."
        )

    return CandidateAssessment(
        candidate=candidate,
        checks=checks,
        decision=decide(checks, new_columns, evolvable=not unreachable),
        new_columns=new_columns,
    )


def _grain_expressions(analysis: SqlAnalysis) -> set[str]:
    by_name = {o.name: o.expression for o in analysis.outputs}
    return {by_name[g] for g in analysis.grain}


def _rewrite_on_outputs(predicate: str, by_expression: dict[str, str]) -> str | None:
    """Rewrite a predicate on the candidate's output columns, or None if impossible.

    "toDate(raw.deliveries.actual_time) >= '2026-01-01'"
      -> "delivery_date >= '2026-01-01'"   (fact_deliveries outputs delivery_date)
    """

    def visit(n: exp.Expression) -> exp.Expression:
        name = by_expression.get(n.sql(DIALECT))
        return exp.column(name) if name else n

    node = sqlglot.parse_one(predicate, read=DIALECT).transform(visit)
    if any(c.table for c in node.find_all(exp.Column)):
        return None
    return node.sql(DIALECT)
