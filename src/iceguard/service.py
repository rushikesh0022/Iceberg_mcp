"""Application services: the only entry points the MCP server (Step 4) calls.

Transport code stays thin; all rules live in the modules below, so they can be
tested without Claude Code.
"""

from __future__ import annotations

from iceguard.candidates import find_candidates, name_tokens
from iceguard.catalog import CatalogSource
from iceguard.compatibility import DECISION_RANK, assess
from iceguard.lineage import DEFAULT_MAX_DEPTH, build_edges, get_lineage
from iceguard.models import CandidateAssessment, Decision, Lineage, ReusePlan, Table
from iceguard.sql_analysis import (
    SqlAnalysisError,
    analyze_catalog,
    analyze_sql,
    expand_upstream,
    identity_analysis,
)

MAX_SEARCH_RESULTS = 10

# How much a query word counts when it appears in each part of a table.
NAME_MATCH = 3
COLUMN_MATCH = 2
DESCRIPTION_MATCH = 1


def search_catalog(query: str, catalog: CatalogSource) -> list[Table]:
    """Rank tables by how many query words appear in their name, columns and description."""
    words = name_tokens(query)
    scored = []
    for table in catalog.list_tables():
        name = name_tokens(table.name)
        columns = [name_tokens(c) for c in table.column_names()]
        description = name_tokens(table.description)
        score = sum(
            NAME_MATCH * (w in name)
            + COLUMN_MATCH * sum(w in c for c in columns)
            + DESCRIPTION_MATCH * (w in description)
            for w in words
        )
        if score:
            scored.append((score, table))
    scored.sort(key=lambda s: (-s[0], s[1].name))
    return [t for _, t in scored[:MAX_SEARCH_RESULTS]]


def plan_table_reuse(proposed_sql: str, catalog: CatalogSource) -> ReusePlan:
    """analyze_sql -> find_candidates -> assess each -> pick the best decision."""
    try:
        proposed = analyze_sql(proposed_sql, catalog)
    except SqlAnalysisError as e:
        return _needs_info(None, [str(e)])
    if not proposed.outputs:  # unsupported statement shape, e.g. UNION
        return _needs_info(proposed.target_table, proposed.limitations)

    existing, errors = analyze_catalog(catalog)
    existing.pop(proposed.target_table, None)  # never compare a table with itself
    expanded_existing = {t: expand_upstream(a, existing) for t, a in existing.items()}
    expanded_proposed = expand_upstream(proposed, existing)
    limitations = expanded_proposed.limitations + errors

    assessments = []
    for candidate in find_candidates(proposed, expanded_proposed, expanded_existing):
        options = [assess(expanded_proposed, expanded_existing[candidate.table], candidate)]
        if candidate.table in expanded_proposed.source_tables:
            # The proposal reads the candidate directly (e.g. an aggregated table
            # that expansion keeps as a source): also compare against the table as-is.
            identity = identity_analysis(candidate.table, existing[candidate.table])
            options.append(assess(expanded_proposed, identity, candidate))
        assessments.append(min(options, key=_rank))

    assessments.sort(key=lambda a: (_rank(a), -a.candidate.score))
    if not assessments:
        return ReusePlan(
            proposed_table=proposed.target_table,
            decision=Decision.NEW_TABLE,
            recommended_table=None,
            assessments=[],
            limitations=[*limitations, "No related tables found."],
        )

    best = assessments[0]
    recommend = best.decision in (Decision.REUSE, Decision.EVOLVE_SCHEMA)
    return ReusePlan(
        proposed_table=proposed.target_table,
        decision=best.decision,
        recommended_table=best.candidate.table if recommend else None,
        assessments=assessments,
        limitations=limitations,
    )


def get_table_lineage(
    table: str, catalog: CatalogSource, max_depth: int = DEFAULT_MAX_DEPTH
) -> Lineage:
    if catalog.get_table(table) is None:
        raise ValueError(f"Unknown table: {table}")
    analyses, _ = analyze_catalog(catalog)
    return get_lineage(table, build_edges(analyses), max_depth)


def _needs_info(proposed_table: str | None, limitations: list[str]) -> ReusePlan:
    return ReusePlan(
        proposed_table=proposed_table,
        decision=Decision.NEEDS_INFO,
        recommended_table=None,
        assessments=[],
        limitations=limitations,
    )


def _rank(assessment: CandidateAssessment) -> int:
    return DECISION_RANK[assessment.decision]
