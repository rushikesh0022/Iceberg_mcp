# IceGuard

Checks whether a proposed analytical table already exists, and explains why
to reuse it, extend it, or build a new one. Exposed to IDE assistants over MCP.

## Layout

```text
src/iceguard/
  models.py         typed inputs and results
  catalog.py        CatalogSource interface + SchemaFileCatalog
  sql_analysis.py   SQL -> SqlAnalysis (sqlglot)
  candidates.py     find candidate tables
  compatibility.py  columns / grain / logic checks + decision
  lineage.py        upstream / downstream tables
  service.py        entry points the MCP server calls
  mcp_server.py     FastMCP server: search_catalog, plan_table_reuse, get_table_lineage
catalog/schema.toml hand-written demo catalog (replaced by ClickHouse discovery in Step 5)
pipelines/existing  SQL that produces the existing tables
pipelines/proposed  planted proposals with their expected decisions
tests/
```

## Run

```bash
uv sync
uv run pytest
```

## Use it from Claude Code

The repo's `.mcp.json` registers the server as `iceguard`. Open Claude Code in
this folder and approve the server when asked. Then try:

- "Find tables with delivery delays."
- "I want a table of delivery delays per delivery, without the status filter. Check it first."
- "What reads from analytics.fact_deliveries?"

To run the server by hand (stdio): `uv run iceguard-mcp`.
Set `ICEGUARD_SCHEMA=/path/to/schema.toml` to point it at another catalog.
