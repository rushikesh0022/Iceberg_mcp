# IceGuard

Checks whether a proposed analytical table already exists, and explains why
to reuse it, extend it, or build a new one. Exposed to IDE assistants over MCP.

## Layout

```text
src/iceguard/
  models.py         typed inputs and results
  catalog.py        CatalogSource interface + SchemaFileCatalog
  clickhouse_catalog.py  catalog discovered from a running ClickHouse
  sql_analysis.py   SQL -> SqlAnalysis (sqlglot)
  candidates.py     find candidate tables
  compatibility.py  columns / grain / logic checks + decision
  lineage.py        upstream / downstream tables
  service.py        entry points the MCP server calls
  mcp_server.py     FastMCP server: search_catalog, plan_table_reuse, get_table_lineage
catalog/schema.toml hand-written demo catalog (fast tests; default MCP catalog)
catalog/contracts.toml unique keys, which ClickHouse cannot report
clickhouse/config.xml local server config (1.5 GB memory cap, localhost only)
scripts/clickhouse.sh install | start | stop | status | client
scripts/seed.py     loads the demo warehouse and simulated usage
seed/               source-table DDL and data, table comments
pipelines/existing  SQL that produces the existing tables
pipelines/proposed  planted proposals with their expected decisions
tests/
```

## Run

```bash
uv sync
uv run pytest            # ClickHouse tests are skipped unless the server is running
```

## Local ClickHouse (macOS arm64, no Docker)

```bash
scripts/clickhouse.sh install   # pinned 26.8 LTS binary, checksum-verified (~890 MB unpacked)
scripts/clickhouse.sh start
uv run python scripts/seed.py   # ~1 s: 500k deliveries over 60 days, plus usage in query_log
uv run pytest                   # now includes the integration tests
scripts/clickhouse.sh stop      # frees its memory
```

Point the MCP server at it with `ICEGUARD_CATALOG=clickhouse`.

## Use it from Claude Code

The repo's `.mcp.json` registers the server as `iceguard`. Open Claude Code in
this folder and approve the server when asked. Then try:

- "Find tables with delivery delays."
- "I want a table of delivery delays per delivery, without the status filter. Check it first."
- "What reads from analytics.fact_deliveries?"

To run the server by hand (stdio): `uv run iceguard-mcp`.
Set `ICEGUARD_SCHEMA=/path/to/schema.toml` to point it at another catalog.
