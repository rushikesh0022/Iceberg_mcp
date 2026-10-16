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
