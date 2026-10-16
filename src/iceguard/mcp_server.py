"""Step 4: expose the IceGuard services to IDE assistants over MCP (stdio).

This module only translates between MCP and `service`; it holds no rules.

The catalog is read on every call, so edits to SQL files, the schema file or
the database are picked up without restarting the server. Configuration is
shared with the hook: see iceguard.runtime. Every reuse check is recorded in
the SQLite history.
"""

from __future__ import annotations

from typing import Annotated

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from iceguard import runtime, service
from iceguard.catalog import CatalogSource
from iceguard.lineage import DEFAULT_MAX_DEPTH
from iceguard.models import Lineage, ReusePlan, Table
from iceguard.runtime import CatalogUnavailable

READ_ONLY = {"read_only_hint": True, "idempotent_hint": True, "open_world_hint": False}

mcp = FastMCP(
    name="IceGuard",
    instructions=(
        "IceGuard knows which analytical tables already exist and how they are calculated. "
        "Before writing SQL that creates a new table or pipeline, call plan_table_reuse with "
        "that SQL and follow its decision. Use search_catalog to find existing data and "
        "get_table_lineage before changing or removing a table."
    ),
)


def _catalog() -> CatalogSource:
    try:
        return runtime.load_catalog()
    except CatalogUnavailable as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=READ_ONLY)
def search_catalog(
    query: Annotated[str, Field(description="Words describing the data, e.g. 'delivery delays'.")],
) -> list[Table]:
    """Find existing tables whose name, columns or description match the query."""
    return service.search_catalog(query, _catalog())


@mcp.tool(annotations=READ_ONLY)
def plan_table_reuse(
    sql: Annotated[
        str,
        Field(
            description=(
                "The proposed SQL: CREATE TABLE ... AS SELECT, INSERT ... SELECT, or SELECT "
                "(ClickHouse dialect)."
            )
        ),
    ],
) -> ReusePlan:
    """Check whether a proposed table already exists. Call this BEFORE creating any new table.

    Returns one decision with reasons:
      REUSE          - an existing table already has this data; use recommended_table.
      EVOLVE_SCHEMA  - same rows and logic; add new_columns to recommended_table instead.
      NEW_TABLE      - the rows, grain or logic differ; the reasons say how.
      NEEDS_INFO     - IceGuard cannot decide; limitations say what is missing.
    """
    catalog = _catalog()
    store = runtime.open_store()
    try:
        plan = service.plan_table_reuse(sql, catalog, cache=store)
        if store:
            store.record_check(
                sql=sql, plan=plan, source="mcp", catalog=runtime.catalog_kind(), action="reported"
            )
        return plan
    finally:
        if store:
            store.close()


@mcp.tool(annotations=READ_ONLY)
def get_table_lineage(
    table: Annotated[
        str, Field(description="Qualified table name, e.g. analytics.fact_deliveries.")
    ],
    max_depth: Annotated[int, Field(ge=1, le=20)] = DEFAULT_MAX_DEPTH,
) -> Lineage:
    """Show which tables feed a table (upstream) and which tables read it (downstream)."""
    try:
        return service.get_table_lineage(table, _catalog(), max_depth)
    except ValueError as e:
        raise ToolError(str(e)) from e


def main() -> None:
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
