"""Step 3a: find existing tables that might satisfy a proposed table.

Signals (no AI model in Milestone 1):
  - same calculations     (output expressions, after upstream expansion)
  - shared source tables  (or the proposal reads the candidate directly)
  - shared words in table names

The score only ranks candidates. `compatibility` makes the decision.
"""

from __future__ import annotations

import re

from iceguard.models import Candidate, SqlAnalysis

MAX_CANDIDATES = 5

EXPRESSION_WEIGHT = 0.6
SOURCE_WEIGHT = 0.3
NAME_WEIGHT = 0.1


def find_candidates(
    proposed: SqlAnalysis,
    expanded_proposed: SqlAnalysis,
    expanded_existing: dict[str, SqlAnalysis],
) -> list[Candidate]:
    """Rank existing tables against a proposal.

    `proposed` is the analysis as written (used to see which tables it reads
    directly); the `expanded_*` analyses are rewritten in terms of the same
    upstream sources, so their expressions are comparable.
    """
    candidates = []
    for table, existing in expanded_existing.items():
        if table == proposed.target_table:
            continue

        existing_by_expression = {o.expression: o.name for o in existing.outputs}
        # Column-less outputs such as count() look the same everywhere; ignore them.
        matched = [
            o.name
            for o in expanded_proposed.outputs
            if o.source_columns and o.expression in existing_by_expression
        ]
        shared = sorted(set(expanded_proposed.source_tables) & set(existing.source_tables))
        reads_candidate = table in proposed.source_tables

        expression_score = len(matched) / len(expanded_proposed.outputs or [None])
        source_score = (
            1.0
            if reads_candidate
            else _jaccard(set(expanded_proposed.source_tables), set(existing.source_tables))
        )
        name_score = _jaccard(name_tokens(proposed.target_table or ""), name_tokens(table))

        score = (
            EXPRESSION_WEIGHT * expression_score
            + SOURCE_WEIGHT * source_score
            + NAME_WEIGHT * name_score
        )
        if score == 0:
            continue
        candidates.append(
            Candidate(
                table=table,
                score=round(score, 3),
                matched_columns=matched,
                shared_sources=shared or ([table] if reads_candidate else []),
            )
        )

    candidates.sort(key=lambda c: (-c.score, c.table))
    return candidates[:MAX_CANDIDATES]


def name_tokens(name: str) -> set[str]:
    """Words of a table or column name, without the database and with simple stemming.

    "analytics.fact_deliveries" -> {"fact", "delivery"}
    """
    name = name.rpartition(".")[2]
    return {_stem(w) for w in re.split(r"[^a-z0-9]+", name.lower()) if w}


def _stem(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0
