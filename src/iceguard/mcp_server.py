"""Step 4: expose the IceGuard services to IDE assistants over MCP (stdio).

This module only translates between MCP and `service`; it holds no rules.

The catalog is read on every call, so edits to SQL files, the schema file or
the database are picked up without restarting the server.

Environment:
  ICEGUARD_CATALOG  "file" (default): catalog/schema.toml
                    "clickhouse": discover tables from a running ClickHouse
  ICEGUARD_SCHEMA   schema file for the "file" catalog
  ICEGUARD_CH_HOST, ICEGUARD_CH_PORT   ClickHouse HTTP address (127.0.0.1:8123)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from iceguard import service
from iceguard.catalog import CatalogSource, SchemaFileCatalog
from iceguard.lineage import DEFAULT_MAX_DEPTH
from iceguard.models import Lineage, ReusePlan, Table

REPO_ROOT = Path(__file__).resolve().parents[2]
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


def load_catalog() -> CatalogSource:
    kind = os.environ.get("ICEGUARD_CATALOG", "file")
    if kind == "clickhouse":
        return _clickhouse_catalog()
    if kind != "file":
        raise ToolError(f"ICEGUARD_CATALOG must be 'file' or 'clickhouse', not {kind!r}.")

    schema = Path(os.environ.get("ICEGUARD_SCHEMA", REPO_ROOT / "catalog" / "schema.toml"))
    if not schema.is_file():
        raise ToolError(f"Catalog schema file not found: {schema}")
    return SchemaFileCatalog(schema, repo_root=schema.parent.parent)


def _clickhouse_catalog() -> CatalogSource:
    from clickhouse_connect.driver.exceptions import OperationalError

    from iceguard.clickhouse_catalog import ClickHouseCatalog

    host = os.environ.get("ICEGUARD_CH_HOST", "127.0.0.1")
    port = int(os.environ.get("ICEGUARD_CH_PORT", "8123"))
    try:
        return ClickHouseCatalog(REPO_ROOT, host=host, port=port)
    except OperationalError as e:
        raise ToolError(
            f"Cannot reach ClickHouse at {host}:{port}. Start it with scripts/clickhouse.sh start."
        ) from e


@mcp.tool(annotations=READ_ONLY)
def search_catalog(
    query: Annotated[str, Field(description="Words describing the data, e.g. 'delivery delays'.")],
) -> list[Table]:
    """Find existing tables whose name, columns or description match the query."""
    return service.search_catalog(query, load_catalog())


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
    return service.plan_table_reuse(sql, load_catalog())


@mcp.tool(annotations=READ_ONLY)
def get_table_lineage(
    table: Annotated[
        str, Field(description="Qualified table name, e.g. analytics.fact_deliveries.")
    ],
    max_depth: Annotated[int, Field(ge=1, le=20)] = DEFAULT_MAX_DEPTH,
) -> Lineage:
    """Show which tables feed a table (upstream) and which tables read it (downstream)."""
    try:
        return service.get_table_lineage(table, load_catalog(), max_depth)
    except ValueError as e:
        raise ToolError(str(e)) from e


def main() -> None:
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
