# IceGuard

Checks whether a proposed analytical table already exists, and explains why
to reuse it, extend it, or build a new one. Exposed to IDE assistants over MCP.

## Layout

```text
src/iceguard/
  models.py         typed inputs and results
  catalog.py        CatalogSource interface + SchemaFileCatalog
  sql_analysis.py   SQL -> SqlAnalysis (sqlglot)
  candidates.py     find candidate tables                    [Step 3]
  compatibility.py  columns / grain / logic checks + decision [Step 3]
  lineage.py        upstream / downstream tables             [Step 3]
  service.py        entry points the MCP server calls        [Step 3-4]
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
