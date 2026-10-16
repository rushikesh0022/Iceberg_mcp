"""Step 3a: find existing tables that might satisfy a proposed table.

Signals (no AI model in Milestone 1):
  - overlap of output column names
  - overlap of source tables
  - shared words in table names

The score only ranks candidates. `compatibility` makes the decision.
"""

from __future__ import annotations

from iceguard.catalog import CatalogSource
from iceguard.models import Candidate, SqlAnalysis

MAX_CANDIDATES = 5


def find_candidates(proposed: SqlAnalysis, catalog: CatalogSource) -> list[Candidate]:
    raise NotImplementedError("Step 3")
