"""Step 5: a catalog discovered from a live ClickHouse server.

  tables, engine, sort key, partition key,
  description (COMMENT), rows, bytes          <- system.tables
  columns and types                           <- system.columns
  which SQL file creates each table           <- target of each pipelines/existing/*.sql
  unique key (grain)                          <- catalog/contracts.toml (ClickHouse cannot know it)

The catalog is a snapshot taken when the object is created.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import clickhouse_connect
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from iceguard.catalog import build_schema_mapping
from iceguard.models import Column, Table

DEFAULT_DATABASES = ("raw", "analytics")

TABLES_QUERY = """
SELECT database, name, engine, sorting_key, partition_key, comment, total_rows, total_bytes
FROM system.tables
WHERE database IN {databases:Array(String)} AND NOT is_temporary
ORDER BY database, name
"""

COLUMNS_QUERY = """
SELECT database, table, name, type
FROM system.columns
WHERE database IN {databases:Array(String)}
ORDER BY database, table, position
"""


class ClickHouseCatalog:
    def __init__(
        self,
        repo_root: Path,
        host: str = "127.0.0.1",
        port: int = 8123,
        databases: tuple[str, ...] = DEFAULT_DATABASES,
        contracts_path: Path | None = None,
        pipelines_dir: Path | None = None,
    ) -> None:
        self._root = repo_root
        contracts = _load_contracts(contracts_path or repo_root / "catalog" / "contracts.toml")
        producers = _find_producers(
            pipelines_dir or repo_root / "pipelines" / "existing", repo_root
        )

        client = clickhouse_connect.get_client(host=host, port=port)
        try:
            params = {"databases": list(databases)}
            columns: dict[str, list[Column]] = {}
            for db, table, name, type_ in client.query(
                COLUMNS_QUERY, parameters=params
            ).result_rows:
                columns.setdefault(f"{db}.{table}", []).append(Column(name=name, type=type_))

            self._tables: dict[str, Table] = {}
            for row in client.query(TABLES_QUERY, parameters=params).result_rows:
                db, name, engine, sorting_key, partition_key, comment, rows, size = row
                qualified = f"{db}.{name}"
                self._tables[qualified] = Table(
                    name=qualified,
                    columns=columns.get(qualified, []),
                    description=comment,
                    order_by=split_key(sorting_key),
                    partition_by=partition_key or None,
                    unique_key=contracts.get(qualified, []),
                    produced_by=producers.get(qualified),
                    engine=engine,
                    total_rows=rows,
                    total_bytes=size,
                )
        finally:
            client.close()

    def list_tables(self) -> list[Table]:
        return list(self._tables.values())

    def get_table(self, name: str) -> Table | None:
        return self._tables.get(name)

    def read_sql(self, table: Table) -> str | None:
        if table.produced_by is None:
            return None
        return (self._root / table.produced_by).read_text()

    def schema_mapping(self) -> dict[str, dict[str, dict[str, str]]]:
        return build_schema_mapping(self.list_tables())


def split_key(key: str) -> list[str]:
    """Split a ClickHouse key expression on top-level commas.

    "delivery_date, delivery_id" -> ["delivery_date", "delivery_id"]
    "toYYYYMM(d), id"            -> ["toYYYYMM(d)", "id"]
    """
    parts, depth, current = [], 0, []
    for char in key:
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        depth += char in "(["
        depth -= char in ")]"
        current.append(char)
    tail = "".join(current).strip()
    return [*parts, tail] if tail else parts


def _load_contracts(path: Path) -> dict[str, list[str]]:
    if not path.is_file():
        return {}
    with path.open("rb") as f:
        tables = tomllib.load(f).get("tables", {})
    return {name: data.get("unique_key", []) for name, data in tables.items()}


def _find_producers(pipelines_dir: Path, repo_root: Path) -> dict[str, str]:
    """Map each table to the SQL file that creates or inserts into it."""
    producers: dict[str, str] = {}
    for path in sorted(pipelines_dir.glob("*.sql")):
        try:
            statement = sqlglot.parse_one(path.read_text(), read="clickhouse")
        except ParseError:
            continue
        if isinstance(statement, (exp.Create, exp.Insert)) and (
            target := statement.find(exp.Table)
        ):
            name = f"{target.db}.{target.name}" if target.db else target.name
            producers[name] = str(path.relative_to(repo_root))
    return producers
