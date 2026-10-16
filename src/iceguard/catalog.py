"""Catalog sources: where IceGuard learns which tables exist.

Every source implements `CatalogSource`, so the analysis code never knows
whether metadata came from a hand-written schema file or a live database.

  SchemaFileCatalog  - reads catalog/schema.toml (Steps 1-4, and the tests)
  ClickHouseCatalog  - reads ClickHouse system tables (Step 5, not built yet)
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Protocol

from iceguard.models import Column, Table


class CatalogSource(Protocol):
    def list_tables(self) -> list[Table]: ...

    def get_table(self, name: str) -> Table | None: ...

    def read_sql(self, table: Table) -> str | None:
        """Return the SQL that produces `table`, or None for source tables."""
        ...

    def schema_mapping(self) -> dict[str, dict[str, dict[str, str]]]:
        """Nested {db: {table: {column: type}}} mapping, as sqlglot's qualify() expects."""
        ...


def build_schema_mapping(tables: list[Table]) -> dict[str, dict[str, dict[str, str]]]:
    mapping: dict[str, dict[str, dict[str, str]]] = {}
    for t in tables:
        db, _, name = t.name.partition(".")
        mapping.setdefault(db, {})[name] = {c.name: c.type for c in t.columns}
    return mapping


class SchemaFileCatalog:
    """Catalog backed by a TOML file plus the SQL files it points to."""

    def __init__(self, schema_path: Path, repo_root: Path | None = None) -> None:
        self._root = repo_root or schema_path.parent.parent
        with schema_path.open("rb") as f:
            raw = tomllib.load(f)
        self._tables = {t.name: t for t in (_parse_table(n, d) for n, d in raw["tables"].items())}

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


def _parse_table(name: str, data: dict) -> Table:
    return Table(
        name=name,
        columns=[Column(name=c, type=t) for c, t in data["columns"].items()],
        description=data.get("description", ""),
        order_by=data.get("order_by", []),
        partition_by=data.get("partition_by"),
        unique_key=data.get("unique_key", []),
        produced_by=data.get("produced_by"),
    )
