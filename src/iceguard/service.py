"""Application services: the only entry points the MCP server (Step 4) calls.

Transport code stays thin; all rules live in the modules below, so they can be
tested without Claude Code.
"""

from __future__ import annotations

from iceguard.catalog import CatalogSource
from iceguard.models import Lineage, ReusePlan, Table


def search_catalog(query: str, catalog: CatalogSource) -> list[Table]:
    raise NotImplementedError("Step 3")


def plan_table_reuse(proposed_sql: str, catalog: CatalogSource) -> ReusePlan:
    """analyze_sql -> find_candidates -> assess each -> pick the best decision."""
    raise NotImplementedError("Step 3")


def get_table_lineage(table: str, catalog: CatalogSource) -> Lineage:
    raise NotImplementedError("Step 3")
